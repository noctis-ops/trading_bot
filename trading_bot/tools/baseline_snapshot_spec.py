"""Pinned constants for the Baseline data snapshot.

Single source of truth shared by the fetch tool and the verification tool.
Values are copied verbatim from BASELINE_DATA_SPECIFICATION.md (VB-BASE-001)
and must never drift from it. This module is acquisition tooling and is NOT
part of the frozen Version B implementation.
"""

from __future__ import annotations

SPEC_REFERENCE = "BASELINE_DATA_SPECIFICATION.md (VB-BASE-001, pinned at b7bb993)"

# Frozen Version A universe.
SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "DOGE/USDT"]

MEASUREMENT_WINDOW = {
    "start_utc": "2025-01-01T00:00:00Z",
    "end_open_utc": "2025-12-31T23:55:00Z",
}

# Option-B warm-up: exact-count rows prepended before the measurement window.
# first_open_utc .. last_open_utc are the OPEN times of the first and last
# candles in the snapshot; expected_rows_gapless assumes a 24/7 gapless feed.
TIMEFRAMES = {
    "1h": {
        "step_ms": 3_600_000,
        "first_open_utc": "2024-12-23T16:00:00Z",   # 200 warm-up bars
        "last_open_utc": "2025-12-31T23:00:00Z",    # closes 2026-01-01 00:00
        "expected_rows_gapless": 8_960,
        "warmup_rows": 200,
    },
    "15m": {
        "step_ms": 900_000,
        "first_open_utc": "2024-12-29T22:00:00Z",   # 200 warm-up bars
        "last_open_utc": "2025-12-31T23:45:00Z",    # closes 2026-01-01 00:00
        "expected_rows_gapless": 35_240,
        "warmup_rows": 200,
    },
    "5m": {
        "step_ms": 300_000,
        "first_open_utc": "2024-12-31T22:15:00Z",   # 21 warm-up bars
        "last_open_utc": "2025-12-31T23:55:00Z",    # closes 2026-01-01 00:00
        "expected_rows_gapless": 105_141,
        "warmup_rows": 21,
    },
}

# Raw Binance kline fields, persisted verbatim (no transformation).
RAW_KLINE_COLUMNS = [
    "open_time", "open", "high", "low", "close", "volume",
    "close_time", "quote_volume", "trades",
    "taker_buy_base_volume", "taker_buy_quote_volume", "ignore",
]

SNAPSHOT_DIRNAME = "data_snapshots/baseline_2025"
MANIFEST_FILENAME = "manifest.json"


def market_symbol(symbol: str) -> str:
    """CCXT unified symbol -> Binance market symbol (BTC/USDT -> BTCUSDT)."""
    return symbol.replace("/", "")


def dataset_filename(symbol: str, timeframe: str) -> str:
    return f"{market_symbol(symbol)}_{timeframe}.csv.gz"


def iso_to_ms(value: str) -> int:
    from datetime import datetime, timezone

    return int(
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
        .replace(tzinfo=timezone.utc)
        .timestamp()
        * 1000
    )
