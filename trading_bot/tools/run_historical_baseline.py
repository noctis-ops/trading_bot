"""Historical Baseline runner — Type-1 Strategy Model Baseline.

Execution tooling OUTSIDE the frozen Version B implementation. It only wires
the pinned inputs into the frozen pipeline; every decision, risk, execution,
metric, lineage, and artifact rule lives in frozen code:

    snapshot frames -> VersionBBacktestEngine (= VersionBReplayEngine,
    environment "backtest") -> StrategyCore -> RiskEngine ->
    VersionBExecutionService (vb-1.0-5m-stop-first) -> VersionBStore
    -> measurement_lineage / measurement_metrics -> baseline_artifact

Contract obligations honoured here (VERSION_B_BASELINE_DEFINITION.md):
  §1  frozen model replayed over historical OHLCV — engine defaults only,
      no fee/slippage/balance/parameter overrides anywhere in this file;
  §4  self-labelling artifact via build_baseline_artifact (frozen);
  §5  metrics come exclusively from report["model_metrics"] (pinned keys);
  §6  require_clean_tree=True; artifacts carry full lineage; the runner
      REFUSES to run on a dirty tree so code identity is a committed commit.

The runner reads ONLY the immutable snapshot (data_snapshots/baseline_2025).
It never touches the network. Artifacts are written to --out (outside the
repo during measurement so later runs still see a clean tree).

Usage:
    python tools/run_historical_baseline.py --symbol BTC/USDT --direction long \
        --out /tmp/baseline_artifacts [--db-dir /tmp/baseline_dbs]
"""

from __future__ import annotations

import argparse
import gzip
import json
import subprocess
import sys
import uuid
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from backtesting.version_b_backtest import VersionBBacktestEngine  # noqa: E402
from core.baseline_artifact import (  # noqa: E402
    artifact_fingerprint,
    build_baseline_artifact,
    write_baseline_artifact,
)
from database.version_b_store import VersionBStore  # noqa: E402
from tools.baseline_snapshot_spec import (  # noqa: E402
    SNAPSHOT_DIRNAME,
    SYMBOLS,
    TIMEFRAMES,
    dataset_filename,
)

SNAPSHOT_DIR = REPO / SNAPSHOT_DIRNAME


def load_frame(symbol: str, timeframe: str) -> pd.DataFrame:
    """Snapshot gz-CSV -> OHLCV frame indexed by UTC open time. Verbatim rows."""
    path = SNAPSHOT_DIR / dataset_filename(symbol, timeframe)
    with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
        raw = pd.read_csv(handle)
    frame = pd.DataFrame({
        "open": raw["open"].astype(float),
        "high": raw["high"].astype(float),
        "low": raw["low"].astype(float),
        "close": raw["close"].astype(float),
        "volume": raw["volume"].astype(float),
    })
    frame.index = pd.DatetimeIndex(pd.to_datetime(raw["open_time"], unit="ms", utc=True))
    expected = TIMEFRAMES[timeframe]["expected_rows_gapless"]
    if len(frame) != expected:
        raise SystemExit(f"snapshot integrity: {path.name} has {len(frame)} rows, expected {expected}")
    return frame


def require_clean_committed_tree() -> str:
    status = subprocess.run(["git", "status", "--porcelain"], cwd=REPO.parent,
                            capture_output=True, text=True)
    if status.stdout.strip():
        raise SystemExit("REFUSED: working tree is dirty; a Baseline measurement "
                         "must be reproducible from a committed commit (§6.2).")
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO.parent,
                          capture_output=True, text=True)
    return head.stdout.strip()


def run_one(symbol: str, direction: str, out_dir: Path, db_dir: Path) -> dict:
    df_1h = load_frame(symbol, "1h")
    df_15m = load_frame(symbol, "15m")
    df_5m = load_frame(symbol, "5m")

    run_id = f"baseline2025-{symbol.replace('/', '')}-{direction}-{uuid.uuid4().hex[:8]}"
    store = VersionBStore(db_dir / f"{run_id}.sqlite")
    engine = VersionBBacktestEngine(store=store, run_id=run_id)  # frozen defaults only
    report = engine.run(df_1h, df_15m, df_5m, symbol=symbol, direction=direction)
    if report.get("status") != "completed":
        raise SystemExit(f"run did not complete: {symbol} {direction}: {report.get('status')}")

    lineage = engine.measurement_lineage(symbol=symbol, direction=direction)
    artifact = build_baseline_artifact(
        metrics=report["model_metrics"],
        lineage=lineage,
        execution_model_version=report["execution_model_version"],
        counts={
            "signal_checks": report["signal_checks"],
            "accepted_signals": report["accepted_signals"],
            "rejected_signals": report["rejected_signals"],
            "data_rejections": report["data_rejections"],
            "decision_records": len(report["decisions"]),
            "frame_rows": report["frame_row_counts"],
        },
        require_clean_tree=True,
    )
    path = write_baseline_artifact(artifact, out_dir)
    return {
        "symbol": symbol,
        "direction": direction,
        "run_id": run_id,
        "artifact_file": path.name,
        "data_hash": lineage["data"]["data_hash"],
        "fingerprint": artifact_fingerprint(artifact),
        "model_trade_count": report["model_metrics"]["model_trade_count"],
        "model_forced_exit_count": report["model_metrics"]["model_forced_exit_count"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", required=True, choices=SYMBOLS)
    parser.add_argument("--direction", required=True, choices=["long", "short"])
    parser.add_argument("--out", required=True)
    parser.add_argument("--db-dir", default="/tmp/baseline_dbs")
    args = parser.parse_args()

    commit = require_clean_committed_tree()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    db_dir = Path(args.db_dir)
    db_dir.mkdir(parents=True, exist_ok=True)

    summary = run_one(args.symbol, args.direction, out_dir, db_dir)
    summary["code_commit"] = commit
    (out_dir / f"summary_{summary['run_id']}.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
