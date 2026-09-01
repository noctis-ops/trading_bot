"""Phase 3 tests: indicator parity and frozen Strategy Core behavior."""

import copy
import json
from pathlib import Path
import unittest

import numpy as np
import pandas as pd

from core.runtime_config import load_runtime_config
from core.strategy import TradingStrategy
from core.strategy_core import StrategyCore
from core.risk_engine import RiskEngine
from data.time_alignment import align_timeframes, legacy_closed_view
from indicators.provider import IndicatorProvider
from data.market_data import MarketData


ROOT = Path(__file__).parent
FIXTURE = json.loads((ROOT / "fixtures/version_a_strategy_contract.json").read_text())
CONFIG = load_runtime_config(
    ROOT / "config.yaml",
    environ={"TRADING_MODE": "paper", "PAPER_INITIAL_BALANCE": "10000"},
)
T = pd.Timestamp(FIXTURE["decision_time"])


def make_strategy_frame(side: str, timeframe: str, periods: int) -> pd.DataFrame:
    delta = {"1h": pd.Timedelta(hours=1), "15m": pd.Timedelta(minutes=15), "5m": pd.Timedelta(minutes=5)}[timeframe]
    freq = {"1h": "1h", "15m": "15min", "5m": "5min"}[timeframe]
    values = {
        "open": 100.0,
        "close": 100.0,
        "high": 101.0,
        "low": 99.0,
        "volume": 200.0,
        "ema_slow": 99.0,
        "ema_fast": 100.0,
        "ema_medium": 100.0,
        "rsi": 60.0,
        "macd": 2.0,
        "macd_signal": 1.0,
        "macd_hist": 1.0,
        "atr": 1.0,
        "adx": 30.0,
        "volume_sma": 100.0,
    }
    if side == "short":
        values.update({
            "open": 98.0,
            "close": 98.0,
            "high": 99.0,
            "low": 97.0,
            "ema_slow": 100.0,
            "ema_fast": 99.0,
            "ema_medium": 98.0,
            "rsi": 40.0,
            "macd": -2.0,
            "macd_signal": -1.0,
            "macd_hist": -1.0,
        })
    index = pd.date_range(end=T - delta, periods=periods, freq=freq, tz="UTC")
    return pd.DataFrame({key: value for key, value in values.items()}, index=index)


def aligned_for(side: str):
    frames = {
        "1h": make_strategy_frame(side, "1h", 210),
        "15m": make_strategy_frame(side, "15m", 210),
        "5m": make_strategy_frame(side, "5m", 30),
    }
    return align_timeframes(
        frames,
        decision_time=T,
        decision_timeframe="15m",
        warmup={"1h": 200, "15m": 200, "5m": 21},
    )


class VersionBStrategyFreezeTests(unittest.TestCase):
    def test_indicator_provider_matches_legacy_market_data_formulas(self):
        index = pd.date_range("2026-01-01", periods=80, freq="15min", tz="UTC")
        x = np.arange(80, dtype=float)
        raw = pd.DataFrame(
            {
                "open": 100 + x * 0.1,
                "high": 101 + x * 0.1 + (x % 3) * 0.1,
                "low": 99 + x * 0.1 - (x % 2) * 0.1,
                "close": 100 + x * 0.12 + np.sin(x / 3),
                "volume": 1000 + (x % 7) * 10,
            },
            index=index,
        )
        new = IndicatorProvider.add_indicators(raw, CONFIG)
        old = raw.copy()
        old["ema_fast"] = MarketData.calculate_ema(old["close"], 50)
        old["ema_slow"] = MarketData.calculate_ema(old["close"], 200)
        old["ema_medium"] = MarketData.calculate_ema(old["close"], 21)
        old["rsi"] = MarketData.calculate_rsi(old["close"], 14)
        old["macd"], old["macd_signal"], old["macd_hist"] = MarketData.calculate_macd(old["close"], 12, 26, 9)
        old["atr"] = MarketData.calculate_atr(old, 14)
        old["bb_upper"], old["bb_middle"], old["bb_lower"] = MarketData.calculate_bollinger_bands(old["close"], 20, 2.0)
        old["adx"] = MarketData.calculate_adx(old, 14)
        old["volume_sma"] = MarketData.calculate_volume_sma(old["volume"], 20)

        for column in ["ema_fast", "ema_slow", "ema_medium", "rsi", "macd", "macd_signal", "macd_hist", "atr", "bb_upper", "bb_middle", "bb_lower", "adx", "volume_sma"]:
            np.testing.assert_allclose(new[column].to_numpy(), old[column].to_numpy(), equal_nan=True)

    def test_long_and_short_golden_contract(self):
        for side in ("long", "short"):
            aligned = aligned_for(side)
            self.assertTrue(aligned.valid)
            result = StrategyCore().evaluate("BTC/USDT", aligned, direction=side, config_hash="fixture")
            expected = FIXTURE[side]
            self.assertEqual(result["gate_passed"], expected["gate_passed"])
            self.assertEqual(result["should_trade"], expected["should_trade"])
            self.assertEqual(result["score_result"]["total_score"], expected["score"])
            self.assertEqual(result["score_result"]["recommendation"], expected["recommendation"])
            self.assertEqual(result["gate_result"]["gate_strength"], expected["gate_strength"])
            self.assertEqual(result["effective_score"], expected["effective_score"])
            self.assertEqual(result["gate_result"]["conditions"], expected["conditions"])

    def test_strategy_core_matches_direct_version_a_strategy_on_same_closed_view(self):
        for side in ("long", "short"):
            aligned = aligned_for(side)
            views = {key: legacy_closed_view(value, T) for key, value in aligned.frames.items()}
            strategy = TradingStrategy()
            direct = (
                strategy.get_short_signal_breakdown(views["1h"], views["15m"], views["5m"])
                if side == "short"
                else strategy.get_signal_breakdown(views["1h"], views["15m"], views["5m"])
            )
            actual = StrategyCore(strategy).evaluate("BTC/USDT", aligned, direction=side)
            self.assertEqual(actual["gate_passed"], direct["gate_passed"])
            self.assertEqual(actual["should_trade"], direct["should_trade"])
            self.assertEqual(actual["gate_result"], direct["gate_result"])
            self.assertEqual(actual["score_result"], direct["score_result"])
            self.assertEqual(actual["validation_info"], direct["validation_info"])

    def test_strategy_core_execution_plan_preserves_frozen_stops_for_sides_and_atr_boundaries(self):
        core = StrategyCore(risk_engine=RiskEngine(CONFIG))
        for side in ("long", "short"):
            for entry, atr in ((100.0, 0.1), (100.0, 1.0), (100.0, 10.0)):
                decision = {
                    "should_trade": True,
                    "direction": side,
                    "effective_score": 100.0,
                }
                plan = core.execution_plan(
                    decision, equity=10000.0, entry_price=entry, atr=atr
                )
                expected = core.risk_engine.calculate_stops(
                    side=side, entry_price=entry, atr=atr
                )
                self.assertEqual(plan["stops"], expected)
                self.assertEqual(plan["stops"]["side"], side)
                self.assertAlmostEqual(plan["stops"]["sl_distance"], atr * 1.5)

        invalid = core.execution_plan
        with self.assertRaises(ValueError):
            invalid({"should_trade": False, "direction": "long"}, equity=10000, entry_price=100, atr=1)

    def test_5m_changes_do_not_change_entry_decision(self):
        aligned = aligned_for("long")
        mutated = {key: value.copy() for key, value in aligned.frames.items()}
        mutated["5m"] = mutated["5m"].copy()
        mutated["5m"]["close"] = 999999.0
        mutated["5m"]["high"] = 1000000.0
        mutated["5m"]["low"] = 999998.0
        mutated_aligned = align_timeframes(
            mutated,
            decision_time=T,
            decision_timeframe="15m",
            warmup={"1h": 200, "15m": 200, "5m": 21},
        )
        first = StrategyCore().evaluate("BTC/USDT", aligned, direction="long")
        second = StrategyCore().evaluate("BTC/USDT", mutated_aligned, direction="long")
        self.assertEqual(first["gate_passed"], second["gate_passed"])
        self.assertEqual(first["should_trade"], second["should_trade"])
        self.assertEqual(first["gate_result"], second["gate_result"])
        self.assertEqual(first["score_result"], second["score_result"])


if __name__ == "__main__":
    unittest.main()
