"""Deterministic tests for the Version B historical adapter."""

from datetime import datetime, timezone
import tempfile
import unittest

import pandas as pd

from backtesting.version_b_backtest import VersionBBacktestEngine
from database.version_b_store import DecisionRecord, VersionBStore


class FixtureStrategy:
    def __init__(self):
        self.calls = 0

    def check_buy_signal(self, df_1h, df_15m, df_5m):
        self.calls += 1
        if self.calls != 1:
            return False, {"reason": "fixture"}
        return True, {"signal": "BUY", "entry_price": 100.0, "atr": 1.0}

    def validate_signal(self, df_1h, df_15m, df_5m, signal):
        return True, {"score_result": {"total_score": 100.0}}

    def calculate_exits(self, entry_price, atr):
        return {
            "valid": True,
            "stop_loss": entry_price - 2,
            "take_profit_1": entry_price + 2,
            "take_profit_2": entry_price + 4,
        }

    def calculate_position_size(self, **kwargs):
        return {"contract_size": 1.0, "leverage": 1.0}


class VersionBBacktestTests(unittest.TestCase):
    def frames(self):
        end = pd.Timestamp("2026-01-01 00:00:00", tz="UTC")
        one_hour = pd.date_range(end=end, periods=220, freq="1h")
        fifteen = pd.date_range(end=end, periods=880, freq="15min")
        five = pd.date_range(end=end, periods=2640, freq="5min")

        def frame(index, highs=None):
            highs = highs or {}
            values = [highs.get(i, 100.2) for i in range(len(index))]
            return pd.DataFrame({
                "open": 100.0,
                "high": values,
                "low": 100.1,
                "close": 100.0,
            }, index=index)

        # The first valid decision is after 25 one-hour candles.  The first
        # later 5M bar crosses TP1 and a subsequent one crosses TP2.
        entry_idx = int((one_hour[200] - five[0]).total_seconds() / 300)
        highs = {entry_idx + 1: 102.0, entry_idx + 4: 104.5}
        return frame(one_hour), frame(fifteen), frame(five, highs)

    def test_15m_decisions_and_5m_exit_lifecycle(self):
        one_hour, fifteen, five = self.frames()
        engine = VersionBBacktestEngine(
            initial_balance=1000,
            fee_rate=0.0,
            slippage_rate=0.0,
            strategy=FixtureStrategy(),
        )
        report = engine.run(one_hour, fifteen, five, symbol="BTC/USDT")
        self.assertEqual(report["status"], "completed")
        self.assertEqual(report["execution_model_version"], "vb-1.0-5m-stop-first")
        self.assertEqual(report["decision_timeframe"], "15m")
        self.assertEqual(report["exit_timeframe"], "5m")
        self.assertEqual(report["total_trades"], 1)
        self.assertEqual(report["trades"][0]["event_count"], 6)  # entry, protection, TP1, BE, TP2, close
        # Canonical B sizing is 2% risk with the existing good-score factor
        # (0.75): quantity=10 at entry 100; TP1/TP2 net price PnL is 32.5.
        self.assertAlmostEqual(report["total_profit"], 32.5)

    def test_optional_store_reconstructs_decisions_and_trade_lifecycle(self):
        one_hour, fifteen, five = self.frames()
        with tempfile.TemporaryDirectory() as directory:
            store = VersionBStore(f"{directory}/run.sqlite")
            store.create_run(
                run_id="run-1",
                environment="backtest",
                code_version="test",
                strategy_version="fixture",
                config_hash="cfg",
                data_hash="data",
                execution_model_version="vb-1.0-5m-stop-first",
                universe=["BTC/USDT"],
                effective_config={"paper": False},
            )
            engine = VersionBBacktestEngine(
                initial_balance=1000,
                fee_rate=0.0,
                slippage_rate=0.0,
                strategy=FixtureStrategy(),
                store=store,
                run_id="run-1",
                config_hash="cfg",
            )
            report = engine.run(one_hour, fifteen, five, symbol="BTC/USDT")
            self.assertEqual(report["total_trades"], 1)
            self.assertGreater(DecisionRecord.select().where(DecisionRecord.run == "run-1").count(), 0)
            reconstructed = store.reconstruct_trade(report["trades"][0]["trade_id"])
            self.assertEqual(reconstructed["state"], "CLOSED")
            self.assertEqual(len(reconstructed["fills"]), 3)
            self.assertEqual([event["sequence"] for event in reconstructed["events"]], [1, 2, 3, 4, 5, 6])
            self.assertAlmostEqual(reconstructed["recomputed_net_pnl"], report["trades"][0]["profit"])
            self.assertAlmostEqual(reconstructed["stored_net_pnl"], reconstructed["recomputed_net_pnl"])
            self.assertEqual(reconstructed["recomputed_exit_reason"], "TAKE_PROFIT_2")
            store.close()


if __name__ == "__main__":
    unittest.main()
