"""Verify the immutable Baseline snapshot against the pinned specification.

Acquisition/verification tooling only — OUTSIDE the frozen Version B
implementation. Read-only over the raw files; writes a verification report
JSON next to the manifest (the report is not raw data and carries no hash
authority).

Checks per dataset:
  - file exists and its SHA-256 matches the manifest (integrity);
  - header equals the pinned raw kline columns;
  - open_time strictly increasing, no duplicates (ordering/dedup);
  - every open_time aligned to the timeframe step; close_time == open+step-1;
  - first/last open exactly equal the pinned bounds (spec §4);
  - gap scan: every missing step is listed AND any missing candle fails
    verification (`no_missing_candles`); an incomplete dataset can never
    verify as passed;
  - row_count == expected_gapless - missing;
  - numeric sanity: prices > 0, high>=low, volume >= 0.

Identity separation (spec §5.3): the SHA-256 values verified here are
RAW-FILE PROVENANCE. The binding measurement identity is
lineage.data.data_hash = hash_frames(...) computed later by the frozen
engine over the frames it actually consumes. This tool never computes it.

Usage:
    python tools/verify_baseline_snapshot.py [--dir DIR]
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.baseline_snapshot_spec import (  # noqa: E402
    MANIFEST_FILENAME,
    RAW_KLINE_COLUMNS,
    SNAPSHOT_DIRNAME,
    TIMEFRAMES,
    iso_to_ms,
)


def ms_iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def verify_dataset(directory: Path, entry: dict) -> dict:
    spec = TIMEFRAMES[entry["timeframe"]]
    step = spec["step_ms"]
    expected_first = iso_to_ms(spec["first_open_utc"])
    expected_last = iso_to_ms(spec["last_open_utc"])
    path = directory / entry["file"]
    result: dict = {"symbol": entry["symbol"], "timeframe": entry["timeframe"],
                    "file": entry["file"], "checks": {}, "gaps": [], "anomalies": []}

    if not path.exists():
        result["checks"]["file_exists"] = False
        return result
    result["checks"]["file_exists"] = True

    payload = path.read_bytes()
    sha256 = hashlib.sha256(payload).hexdigest()
    result["sha256_recomputed"] = sha256
    result["checks"]["sha256_matches_manifest"] = (sha256 == entry["sha256"])

    opens: list[int] = []
    ordered = True
    duplicates = 0
    aligned = True
    close_consistent = True
    numeric_ok = True
    with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        result["checks"]["header_matches"] = (header == RAW_KLINE_COLUMNS)
        previous = None
        for row in reader:
            open_ms = int(row[0])
            if previous is not None:
                if open_ms <= previous:
                    ordered = False
                if open_ms == previous:
                    duplicates += 1
            if open_ms % step != 0:
                aligned = False
            if int(row[6]) != open_ms + step - 1:
                close_consistent = False
            o, h, l, c, v = (float(row[1]), float(row[2]), float(row[3]),
                             float(row[4]), float(row[5]))
            if not (o > 0 and h > 0 and l > 0 and c > 0 and v >= 0 and h >= l):
                numeric_ok = False
                result["anomalies"].append({"open_time": ms_iso(open_ms), "row": row[:6]})
            opens.append(open_ms)
            previous = open_ms

    result["row_count"] = len(opens)
    result["checks"]["ordering_strictly_increasing"] = ordered
    result["checks"]["no_duplicates"] = (duplicates == 0)
    result["checks"]["timestamps_aligned_to_step"] = aligned
    result["checks"]["close_time_consistent"] = close_consistent
    result["checks"]["numeric_sanity"] = numeric_ok
    result["first_open"] = ms_iso(opens[0]) if opens else None
    result["last_open"] = ms_iso(opens[-1]) if opens else None
    result["checks"]["first_open_matches_spec"] = bool(opens) and opens[0] == expected_first
    result["checks"]["last_open_matches_spec"] = bool(opens) and opens[-1] == expected_last

    expected_cursor = expected_first
    seen = set(opens)
    while expected_cursor <= expected_last:
        if expected_cursor not in seen:
            result["gaps"].append(ms_iso(expected_cursor))
        expected_cursor += step
    result["missing_candles"] = len(result["gaps"])
    # A gap is a defect, not an annotation: an unexplained missing candle must
    # FAIL verification (non-zero exit) so the workflow never commits an
    # incomplete snapshot labelled as passed. Found as a real blocker in the
    # pre-run review: `passed` ignored `missing_candles` entirely, and
    # row_count_consistent_with_gaps is true by construction whenever the only
    # problem is missing rows.
    result["checks"]["no_missing_candles"] = (result["missing_candles"] == 0)
    result["checks"]["row_count_consistent_with_gaps"] = (
        len(opens) == spec["expected_rows_gapless"] - len(result["gaps"])
    )
    if len(result["gaps"]) > 50:  # keep the report readable; full count retained
        result["gaps"] = result["gaps"][:50] + [f"... and {result['missing_candles'] - 50} more"]

    result["passed"] = all(result["checks"].values())
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", default=None)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parent.parent
    directory = Path(args.dir) if args.dir else repo / SNAPSHOT_DIRNAME
    manifest_path = directory / MANIFEST_FILENAME
    if not manifest_path.exists():
        print(f"FATAL: manifest not found: {manifest_path}")
        return 2
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    results = [verify_dataset(directory, entry) for entry in manifest["datasets"]]
    all_passed = all(item["passed"] for item in results)
    report = {
        "report_schema": "baseline-snapshot-verification-1",
        "verified_at_utc": datetime.now(timezone.utc).isoformat(),
        "manifest_file": str(manifest_path.name),
        "identity_note": manifest.get("identity_note"),
        "datasets": results,
        "all_passed": all_passed,
        "total_missing_candles": sum(item.get("missing_candles", 0) for item in results),
    }
    out = directory / "verification_report.json"
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    for item in results:
        status = "PASS" if item["passed"] else "FAIL"
        print(f"{status}  {item['symbol']:<10} {item['timeframe']:<4} rows={item.get('row_count')} "
              f"missing={item.get('missing_candles')} sha256_ok={item['checks'].get('sha256_matches_manifest')}")
    print(f"\nverification report: {out}")
    print("ALL PASSED" if all_passed else "FAILURES PRESENT")
    return 0 if all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
