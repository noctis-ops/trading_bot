"""Phase 2 tests for deterministic Version B data semantics."""

from datetime import datetime, timezone
import unittest

import pandas as pd

from data.time_alignment import (
    DataStatus,
    align_timeframes,
    assess_frame,
    candle_close_time,
    derive_warmup_requirements,
)
from core.runtime_config import load_runtime_config


T = pd.Timestamp("2026-01-01 10:15:00", tz="UTC")


def frame(start: str, periods: int, freq: str) -> pd.DataFrame:
    index = pd.date_range(start=start, periods=periods, freq=freq, tz="UTC")
    return pd.DataFrame(
        {
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
            "volume": 10.0,
        },
        index=index,
    )


class VersionBDataSemanticsTests(unittest.TestCase):
    def test_only_candles_closed_at_or_before_decision_time_are_selected(self):
        data = frame("2026-01-01 09:45:00", 4, "15min")
        closed, quality = assess_frame(data, "15m", T, required_rows=0)
        self.assertEqual(list(closed.index), [
            pd.Timestamp("2026-01-01 09:45:00", tz="UTC"),
            pd.Timestamp("2026-01-01 10:00:00", tz="UTC"),
        ])
        self.assertEqual(quality.latest_close, T)
        self.assertEqual(quality.status, DataStatus.VALID)

    def test_timeframes_align_to_same_decision_time(self):
        frames = {
            "1h": frame("2026-01-01 08:00:00", 3, "1h"),
            "15m": frame("2025-12-30 08:00:00", 210, "15min"),
            "5m": frame("2026-01-01 09:00:00", 16, "5min"),
        }
        result = align_timeframes(
            frames,
            decision_time=T,
            decision_timeframe="15m",
            warmup={"1h": 2, "15m": 200, "5m": 21},
        )
        self.assertEqual(result.decision_time, T)
        self.assertTrue(result.frames["1h"].index[-1] + pd.Timedelta(hours=1) <= T)
        self.assertTrue(result.frames["15m"].index[-1] + pd.Timedelta(minutes=15) <= T)
        self.assertTrue(result.frames["5m"].index[-1] + pd.Timedelta(minutes=5) <= T)
        self.assertEqual(result.quality["15m"].status, DataStatus.VALID)
        self.assertEqual(result.quality["5m"].status, DataStatus.INSUFFICIENT_WARMUP)

    def test_warmup_is_derived_from_longest_active_consumer(self):
        config = load_runtime_config(
            "config.yaml",
            environ={"TRADING_MODE": "paper", "PAPER_INITIAL_BALANCE": "10000"},
        )
        requirements = derive_warmup_requirements(config)
        self.assertEqual(requirements["1h"], 200)
        self.assertEqual(requirements["15m"], 200)
        self.assertEqual(requirements["5m"], 21)

    def test_insufficient_warmup_is_not_no_signal(self):
        data = frame("2026-01-01 09:00:00", 10, "15min")
        _, quality = assess_frame(data, "15m", T, required_rows=21)
        self.assertEqual(quality.status, DataStatus.INSUFFICIENT_WARMUP)

    def test_stale_data_is_distinct_from_empty_data(self):
        data = frame("2026-01-01 06:00:00", 2, "15min")
        _, quality = assess_frame(data, "15m", T, required_rows=0)
        self.assertEqual(quality.status, DataStatus.STALE)

        _, empty_quality = assess_frame(pd.DataFrame(), "15m", T, required_rows=0)
        self.assertEqual(empty_quality.status, DataStatus.EMPTY)

    def test_gaps_are_detected(self):
        data = frame("2026-01-01 09:00:00", 4, "15min").drop(
            pd.Timestamp("2026-01-01 09:15:00", tz="UTC")
        )
        _, quality = assess_frame(data, "15m", T, required_rows=0)
        self.assertEqual(quality.status, DataStatus.DATA_GAP)
        self.assertEqual(quality.gap_count, 1)

    def test_invalid_schema_is_distinct(self):
        data = pd.DataFrame({"close": [100]}, index=pd.date_range("2026-01-01", periods=1, tz="UTC"))
        _, quality = assess_frame(data, "15m", T, required_rows=0)
        self.assertEqual(quality.status, DataStatus.INVALID_SCHEMA)

    def test_candle_close_time_is_explicit_utc(self):
        result = candle_close_time(datetime(2026, 1, 1, 10, 0), "15m")
        self.assertEqual(result, pd.Timestamp("2026-01-01 10:15:00", tz="UTC"))


if __name__ == "__main__":
    unittest.main()
