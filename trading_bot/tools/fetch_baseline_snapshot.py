"""Fetch the pinned Baseline OHLCV snapshot from Binance historical data.

Acquisition tooling only — OUTSIDE the frozen Version B implementation.
Historical market data only: the public klines endpoint, no API key, no
account or order surface, no live execution.

Endpoint policy:
  1. Preferred: https://data-api.binance.vision/api/v3/klines — Binance's
     official public market-data mirror of api.binance.com (same payload).
  2. Fallback: https://api.binance.com/api/v3/klines.
Pagination uses startTime (the REST spelling of CCXT's `since`) with
limit=1000 until each dataset covers the pinned bounds.

Rows are persisted verbatim (all 12 kline fields, string prices exactly as
returned) into gzip CSVs under data_snapshots/baseline_2025/, which is then
treated as immutable. A manifest.json records provenance including the
SHA-256 of every raw file.

Usage:
    python tools/fetch_baseline_snapshot.py [--out DIR]
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.baseline_snapshot_spec import (  # noqa: E402
    MANIFEST_FILENAME,
    MEASUREMENT_WINDOW,
    RAW_KLINE_COLUMNS,
    SNAPSHOT_DIRNAME,
    SPEC_REFERENCE,
    SYMBOLS,
    TIMEFRAMES,
    dataset_filename,
    iso_to_ms,
    market_symbol,
)

ENDPOINTS = [
    "https://data-api.binance.vision/api/v3/klines",
    "https://api.binance.com/api/v3/klines",
]
LIMIT = 1000
MAX_RETRIES = 6


def http_get_json(url: str) -> list:
    last: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "baseline-snapshot/1.0"})
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
            last = exc
            time.sleep(min(2 ** attempt, 20))
    raise RuntimeError(f"GET failed after {MAX_RETRIES} attempts: {url}: {last}")


def fetch_range(endpoint: str, symbol: str, interval: str, start_ms: int, end_open_ms: int,
                step_ms: int) -> list[list]:
    """Paginate with startTime until the candle opening at end_open_ms is included."""
    rows: list[list] = []
    cursor = start_ms
    while cursor <= end_open_ms:
        query = urllib.parse.urlencode({
            "symbol": market_symbol(symbol),
            "interval": interval,
            "startTime": cursor,
            "limit": LIMIT,
        })
        batch = http_get_json(f"{endpoint}?{query}")
        if not batch:
            break
        for row in batch:
            open_ms = int(row[0])
            if start_ms <= open_ms <= end_open_ms:
                rows.append(row)
        last_open = int(batch[-1][0])
        next_cursor = last_open + step_ms
        if next_cursor <= cursor:  # defensive: no forward progress
            break
        cursor = next_cursor
        time.sleep(0.15)  # polite pacing, well under public rate limits
    return rows


def write_csv_gz(path: Path, rows: list[list]) -> str:
    """Deterministic gzip CSV (mtime=0 so the hash is content-only)."""
    buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=buffer, mode="wb", filename="", mtime=0) as gz:
        text = io.TextIOWrapper(gz, encoding="utf-8", newline="")
        writer = csv.writer(text)
        writer.writerow(RAW_KLINE_COLUMNS)
        for row in rows:
            writer.writerow(row)
        text.flush()
        text.detach()
    payload = buffer.getvalue()
    path.write_bytes(payload)
    return hashlib.sha256(payload).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=None, help="snapshot directory (default: repo data_snapshots/baseline_2025)")
    args = parser.parse_args()

    repo = Path(__file__).resolve().parent.parent
    out_dir = Path(args.out) if args.out else repo / SNAPSHOT_DIRNAME
    out_dir.mkdir(parents=True, exist_ok=True)

    manifest_path = out_dir / MANIFEST_FILENAME
    if manifest_path.exists():
        print(f"REFUSED: {manifest_path} already exists — the snapshot is immutable. "
              f"Delete the directory deliberately if a re-fetch is truly intended.")
        return 2

    endpoint = None
    for candidate in ENDPOINTS:
        try:
            probe = urllib.parse.urlencode({"symbol": "BTCUSDT", "interval": "1h",
                                            "startTime": iso_to_ms("2025-01-01T00:00:00Z"), "limit": 1})
            http_get_json(f"{candidate}?{probe}")
            endpoint = candidate
            break
        except RuntimeError as exc:
            print(f"endpoint unavailable: {candidate}: {exc}")
    if endpoint is None:
        print("FATAL: no Binance historical endpoint reachable")
        return 3

    datasets = []
    for symbol in SYMBOLS:
        for timeframe, spec in TIMEFRAMES.items():
            start_ms = iso_to_ms(spec["first_open_utc"])
            end_open_ms = iso_to_ms(spec["last_open_utc"])
            print(f"fetching {symbol} {timeframe} "
                  f"[{spec['first_open_utc']} .. {spec['last_open_utc']}] ...", flush=True)
            rows = fetch_range(endpoint, symbol, timeframe, start_ms, end_open_ms, spec["step_ms"])
            filename = dataset_filename(symbol, timeframe)
            sha256 = write_csv_gz(out_dir / filename, rows)
            datasets.append({
                "symbol": symbol,
                "market_symbol": market_symbol(symbol),
                "timeframe": timeframe,
                "file": filename,
                "row_count": len(rows),
                "expected_rows_gapless": spec["expected_rows_gapless"],
                "warmup_rows": spec["warmup_rows"],
                "first_open_utc_requested": spec["first_open_utc"],
                "last_open_utc_requested": spec["last_open_utc"],
                "first_open_ms_actual": int(rows[0][0]) if rows else None,
                "last_open_ms_actual": int(rows[-1][0]) if rows else None,
                "sha256": sha256,
            })
            print(f"  -> {len(rows)} rows, sha256={sha256[:16]}…", flush=True)

    manifest = {
        "manifest_schema": "baseline-snapshot-1",
        "spec_reference": SPEC_REFERENCE,
        "source": {
            "exchange": "binance",
            "endpoint": endpoint,
            "endpoint_note": "public historical klines only; no API key, no account, no orders",
            "transport": "urllib (REST); startTime pagination == CCXT `since` semantics",
        },
        "measurement_window": MEASUREMENT_WINDOW,
        "warmup_policy": "Option B — exact-count warm-up rows prepended before the window (VB-BASE-001)",
        "identity_note": (
            "sha256 values below are RAW-FILE provenance only. The binding "
            "measurement identity is lineage.data.data_hash computed by the "
            "frozen engine (measurement_lineage.hash_frames) over the frames "
            "actually consumed, warm-up included."
        ),
        "retrieval_timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "raw_columns": RAW_KLINE_COLUMNS,
        "datasets": datasets,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"\nmanifest written: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
