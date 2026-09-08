"""Deterministic Paper checks for the Version B operational path.

This module previously exercised ``PaperTradingExchange`` — the legacy
in-memory exchange — while counting towards the Version B contract.  That was
the single largest source of false confidence in the Paper surface: the gate
reported a passing "Paper" module that never touched StrategyCore, the
canonical RiskEngine, or VersionBStore.  These tests exercise the real
Version B Paper engine instead.  The legacy exchange checks moved to
``test_legacy_paper_exchange.py``, where they are labelled as what they are.
"""

import tempfile
import unittest
from pathlib import Path

from core.execution_service import PURPOSE_STOP_LOSS
from core.risk_engine import RiskEngine
from core.strategy_core import StrategyCore
from core.version_b_paper import VersionBPaperEngine, VersionBPaperPipeline
from database.version_b_store import TradeLifecycleRecord, VersionBStore
from test_version_b_backtest import FixtureStrategy
from test_version_b_replay_parity import replay_frames

SYMBOL = "BTC/USDT"


class VersionBPaperTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = Path(self.tmp.name) / "paper.sqlite"
        self.frames = replay_frames()

    def test_paper_uses_the_canonical_strategy_and_risk_objects(self):
        core = StrategyCore(strategy=FixtureStrategy(), risk_engine=RiskEngine())
        engine = VersionBPaperEngine(initial_balance=1000, strategy_core=core,
                                     fee_rate=0.0, slippage_rate=0.0)
        self.assertEqual(engine.environment, "paper")
        self.assertIs(engine.strategy_core, core)
        self.assertIs(engine.risk_engine, core.risk_engine)
        self.assertIs(engine.service.risk_engine, core.risk_engine)

    def test_one_trade_id_spans_tp1_be_and_tp2_with_one_open_close_pair(self):
        engine = VersionBPaperEngine(initial_balance=1000, strategy=FixtureStrategy(),
                                     fee_rate=0.0, slippage_rate=0.0)
        report = engine.run(self.frames["1h"], self.frames["15m"], self.frames["5m"],
                            symbol=SYMBOL)
        self.assertEqual(report["environment"], "paper")
        self.assertEqual(report["total_trades"], 1)
        trade = report["trades"][0]
        self.assertEqual(trade["exit_type"], "TAKE_PROFIT_2")
        self.assertEqual(trade["event_count"], 6)  # entry, protection, TP1, BE, TP2, close
        # TP1 took half, so only 5 of 10 contracts reach TP2.
        self.assertAlmostEqual(report["total_profit"], 32.5)

    def test_paper_is_durable_and_reconstructable(self):
        pipeline = VersionBPaperPipeline(db_path=self.db_path, initial_balance=1000,
                                         strategy=FixtureStrategy(),
                                         fee_rate=0.0, slippage_rate=0.0)
        run_id = pipeline.run_id
        report = pipeline.run(self.frames["1h"], self.frames["15m"], self.frames["5m"],
                              symbol=SYMBOL)
        pipeline.close()

        reopened = VersionBStore(self.db_path)
        self.addCleanup(reopened.close)
        row = TradeLifecycleRecord.get_by_id(report["trades"][0]["trade_id"])
        self.assertEqual(row.run_id, run_id)
        self.assertEqual(row.state, "CLOSED")
        self.assertEqual(reopened.run_initial_balance(run_id), 1000.0)
        self.assertEqual(reopened.operational_state(run_id)["closed_trade_count"], 1)

    def test_the_resting_stop_is_durable_paper_protection(self):
        """A paper stop that exists only in memory is not a stop."""
        pipeline = VersionBPaperPipeline(db_path=self.db_path, initial_balance=1000,
                                         strategy=FixtureStrategy(),
                                         fee_rate=0.0, slippage_rate=0.0)
        self.addCleanup(pipeline.close)
        report = pipeline.run(self.frames["1h"], self.frames["15m"], self.frames["5m"],
                              symbol=SYMBOL)
        trade_id = report["trades"][0]["trade_id"]
        store = VersionBStore(self.db_path)
        self.addCleanup(store.close)
        stop = store.get_order_intent(f"{trade_id}:{PURPOSE_STOP_LOSS}")
        self.assertIsNotNone(stop)
        self.assertEqual(stop.status, "SUBMITTED")
        # TP1 moved the stop to breakeven, so the durable resting intent shows
        # 100.0 — the level actually resting, not the pre-TP1 98.5.
        self.assertAlmostEqual(stop.intended_price, 100.0)
        self.assertAlmostEqual(stop.intended_quantity, 5.0)


if __name__ == "__main__":
    unittest.main()
