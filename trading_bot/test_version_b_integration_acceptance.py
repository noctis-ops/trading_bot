"""Integration Acceptance for the unified Version B operational path.

These tests are deliberately not component tests.  Each one drives a real
decision through the whole chain and then inspects what actually happened at
the other end:

    Data → StrategyCore → RiskEngine → ExecutionService → TradeLifecycle
         → VersionBStore → restart

The bar being tested is not "the class exists" and not "the unit passes".  It
is: the same decision travels the whole path, lands in the database, and a
restart rebuilds it from the database alone.
"""

import ast
import os
import tempfile
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from backtesting.version_b_backtest import VersionBBacktestEngine
from core.execution_service import (
    PURPOSE_ENTRY,
    PURPOSE_STOP_LOSS,
    PURPOSE_TAKE_PROFIT_1,
    PURPOSE_TAKE_PROFIT_2,
    VersionBExecutionService,
)
from core.risk_engine import RiskEngine
from core.strategy_core import StrategyCore
from core.version_b_paper import VersionBPaperEngine, VersionBPaperPipeline
from core.version_b_recovery import recover_unified_state
from database.version_b_store import (
    DecisionRecord,
    FillRecord,
    LifecycleEventRecord,
    OrderIntentRecord,
    TradeLifecycleRecord,
    VersionBStore,
)
from test_version_b_backtest import FixtureStrategy
from test_version_b_replay_parity import replay_frames

SYMBOL = "BTC/USDT"

# Modules that constitute the unified operational path.
UNIFIED_MODULES = [
    "core/version_b_replay.py",
    "core/version_b_paper.py",
    "core/version_b_recovery.py",
    "core/execution_service.py",
    "core/strategy_core.py",
    "core/risk_engine.py",
    "core/trade_lifecycle.py",
    "backtesting/version_b_backtest.py",
]

# Legacy implementations that must never be reached from that path.
LEGACY_MODULES = {"core.risk_manager", "core.order_manager", "core.paper_trading",
                  "backtesting.backtesting_advanced"}

REPO_ROOT = Path(__file__).resolve().parent


class CrashedMidLifecycle(RuntimeError):
    """Stand-in for the process dying while a position is still open."""


class CrashingStrategy(FixtureStrategy):
    """Dies on the first decision cycle after TP1 has been taken.

    The condition is semantic rather than a decision-cycle count, so the test
    keeps meaning the same thing if the fixture geometry changes: the process
    is gone while a position is open, TP1 is done, breakeven is armed, and TP2
    has not happened yet.
    """

    def __init__(self, service_ref: dict):
        super().__init__()
        self.service_ref = service_ref

    def check_buy_signal(self, df_1h, df_15m, df_5m):
        service = self.service_ref.get("service")
        if service is not None:
            for position in service.positions.values():
                if position.tp1_hit:
                    raise CrashedMidLifecycle("process died with TP1 taken and TP2 resting")
        return super().check_buy_signal(df_1h, df_15m, df_5m)


class UnifiedPathTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = Path(self.tmp.name) / "paper.sqlite"

    def frames(self):
        return replay_frames()

    def open_store(self):
        return VersionBStore(self.db_path)


# ─────────────────────────────────────────────────────────────────────
# 1. One path, not a parallel implementation
# ─────────────────────────────────────────────────────────────────────
class OnePathNotParallelImplementationTests(UnifiedPathTestCase):
    def test_the_unified_path_never_imports_a_legacy_implementation(self):
        """A silent fallback would show up here as an import."""
        offenders = {}
        for relative in UNIFIED_MODULES:
            tree = ast.parse((REPO_ROOT / relative).read_text(encoding="utf-8"))
            found = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    found.update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    found.add(node.module)
            hit = found & LEGACY_MODULES
            if hit:
                offenders[relative] = sorted(hit)
        self.assertEqual(offenders, {}, f"unified path reaches legacy code: {offenders}")

    def test_backtest_paper_and_trading_bot_share_one_strategy_and_risk_object(self):
        """Same StrategyCore type, same canonical RiskEngine instance, one service."""
        strategy = FixtureStrategy()
        core = StrategyCore(strategy=strategy, risk_engine=RiskEngine())
        paper = VersionBPaperEngine(initial_balance=1000, strategy_core=core,
                                    fee_rate=0.0, slippage_rate=0.0)
        backtest = VersionBBacktestEngine(initial_balance=1000, strategy=FixtureStrategy(),
                                          fee_rate=0.0, slippage_rate=0.0)
        pipeline = VersionBPaperPipeline(db_path=self.db_path, initial_balance=1000,
                                         strategy=FixtureStrategy(),
                                         fee_rate=0.0, slippage_rate=0.0)
        self.addCleanup(pipeline.close)

        for engine in (paper, backtest, pipeline.engine):
            self.assertIsInstance(engine.strategy_core, StrategyCore)
            self.assertIsInstance(engine.risk_engine, RiskEngine)
            # Not two risk objects that happen to agree: one shared instance.
            self.assertIs(engine.strategy_core.risk_engine, engine.risk_engine)
            self.assertIs(engine.service.risk_engine, engine.risk_engine)
            self.assertIsInstance(engine.service, VersionBExecutionService)
            self.assertEqual(engine.decision_timeframe, "15m")
            self.assertEqual(engine.exit_timeframe, "5m")
        # The injected StrategyCore is the one actually used, not a copy.
        self.assertIs(paper.strategy_core, core)
        self.assertIs(paper.strategy_core.strategy, strategy)

    def test_trading_bot_version_b_owns_a_durable_pipeline(self):
        """The bot must run the pipeline, not merely hold Version B objects."""
        from core.bot import TradingBot

        market_data = _FixtureMarketData(self.frames())
        bot = TradingBot(
            version_b=True,
            market_data=market_data,
            strategy=FixtureStrategy(),
            version_b_db_path=str(self.db_path),
            version_b_initial_balance=1000,
            version_b_fee_rate=0.0,
            version_b_slippage_rate=0.0,
        )
        self.addCleanup(bot.version_b_pipeline.close)
        self.assertTrue(bot.version_b_durable)
        self.assertIsNotNone(bot.version_b_store)
        self.assertIsNotNone(bot.version_b_run_id)
        # The engine the bot runs is the pipeline's engine, sharing its store.
        self.assertIs(bot.version_b_engine, bot.version_b_pipeline.engine)
        self.assertIs(bot.version_b_engine.store, bot.version_b_store)

        report = bot.run_version_b_once(symbol=SYMBOL)
        self.assertEqual(report["total_trades"], 1)
        store = self.open_store()
        self.addCleanup(store.close)
        self.assertEqual(TradeLifecycleRecord.select().count(), 1)
        self.assertGreater(DecisionRecord.select().count(), 0)
        self.assertEqual(
            store.run_initial_balance(bot.version_b_run_id), 1000.0,
            "a restart cannot rebuild balance without a durable starting point",
        )

    def test_memory_only_paper_is_reported_as_such(self):
        from core.bot import TradingBot

        bot = TradingBot(
            version_b=True,
            market_data=_FixtureMarketData(self.frames()),
            strategy=FixtureStrategy(),
            version_b_fee_rate=0.0,
            version_b_slippage_rate=0.0,
        )
        self.assertFalse(bot.version_b_durable)
        self.assertIsNone(bot.version_b_store)


# ─────────────────────────────────────────────────────────────────────
# 2/4/5. Strategy → Risk → Execution → Lifecycle → DB, end to end
# ─────────────────────────────────────────────────────────────────────
class StrategyRiskExecutionEndToEndTests(UnifiedPathTestCase):
    def test_one_decision_travels_the_whole_chain(self):
        """The stop RiskEngine computed is the stop ExecutionService enforces."""
        pipeline = VersionBPaperPipeline(db_path=self.db_path, initial_balance=1000,
                                         strategy=FixtureStrategy(),
                                         fee_rate=0.0, slippage_rate=0.0)
        self.addCleanup(pipeline.close)
        frames = self.frames()
        report = pipeline.run(frames["1h"], frames["15m"], frames["5m"], symbol=SYMBOL)

        self.assertEqual(report["total_trades"], 1)
        planned = report["signals"][0]["stops"]
        # Canonical RiskEngine output for entry 100 / ATR 1 with the frozen
        # multipliers — asserted, not assumed.
        self.assertAlmostEqual(planned["stop_loss"], 98.5)
        self.assertAlmostEqual(planned["take_profit_1"], 102.0)
        self.assertAlmostEqual(planned["take_profit_2"], 104.5)

        trade_id = report["trades"][0]["trade_id"]
        store = self.open_store()
        self.addCleanup(store.close)

        # The durable intents carry the same levels the risk engine produced,
        # which is what makes stop parity provable inside the unified path.
        intents = {i.purpose: i for i in store.order_intents_for_trade(trade_id)}
        self.assertEqual(set(intents), {PURPOSE_ENTRY, PURPOSE_STOP_LOSS,
                                        PURPOSE_TAKE_PROFIT_1, PURPOSE_TAKE_PROFIT_2})
        self.assertAlmostEqual(intents[PURPOSE_TAKE_PROFIT_1].intended_price, 102.0)
        self.assertAlmostEqual(intents[PURPOSE_TAKE_PROFIT_2].intended_price, 104.5)
        # The stop was planned at 98.5 and then amended to breakeven once TP1
        # took half.  The durable intent must show the level actually resting,
        # so it reads 100.0 while the plan still records 98.5.
        self.assertAlmostEqual(planned["stop_loss"], 98.5)
        self.assertAlmostEqual(intents[PURPOSE_STOP_LOSS].intended_price, 100.0)
        self.assertAlmostEqual(intents[PURPOSE_STOP_LOSS].intended_quantity, 5.0)
        be = store.find_event(trade_id, "BE_UPDATED")
        self.assertIsNotNone(be)

        # Stop parity inside the path: TP1 fired at 102 and TP2 at 104.5, the
        # exact planned levels, through the real 5m intrabar evaluation.
        fills = store.fills_for_trade(trade_id)
        exit_prices = sorted(round(f.price, 4) for f in fills if f.role == "exit")
        self.assertEqual(exit_prices, [102.0, 104.5])

    def test_lifecycle_events_and_fills_are_durable_in_order(self):
        pipeline = VersionBPaperPipeline(db_path=self.db_path, initial_balance=1000,
                                         strategy=FixtureStrategy(),
                                         fee_rate=0.0, slippage_rate=0.0)
        self.addCleanup(pipeline.close)
        frames = self.frames()
        report = pipeline.run(frames["1h"], frames["15m"], frames["5m"], symbol=SYMBOL)
        trade_id = report["trades"][0]["trade_id"]

        store = self.open_store()
        self.addCleanup(store.close)
        events = [e.event_type for e in store.events_for_trade(trade_id)]
        self.assertEqual(events, ["ENTRY_FILLED", "PROTECTION_PLACED", "TAKE_PROFIT_1",
                                  "BE_UPDATED", "TAKE_PROFIT_2", "TRADE_CLOSED"])
        sequences = [e.sequence for e in store.events_for_trade(trade_id)]
        self.assertEqual(sequences, sorted(sequences))
        row = store.get_trade(trade_id)
        self.assertEqual(row.state, "CLOSED")
        self.assertEqual(row.final_exit_reason, "TAKE_PROFIT_2")
        self.assertAlmostEqual(row.net_pnl, report["total_profit"])
        # Leverage is execution state; without it margin cannot be rebuilt.
        self.assertEqual(row.leverage, report["signals"][0]["position_size"]["leverage"])

    def test_fills_are_linked_to_the_intent_that_produced_them(self):
        pipeline = VersionBPaperPipeline(db_path=self.db_path, initial_balance=1000,
                                         strategy=FixtureStrategy(),
                                         fee_rate=0.0, slippage_rate=0.0)
        self.addCleanup(pipeline.close)
        frames = self.frames()
        report = pipeline.run(frames["1h"], frames["15m"], frames["5m"], symbol=SYMBOL)
        trade_id = report["trades"][0]["trade_id"]

        store = self.open_store()
        self.addCleanup(store.close)
        links = [(f.role, f.order_intent_id) for f in store.fills_for_trade(trade_id)]
        self.assertEqual(links, [
            ("entry", f"{trade_id}:{PURPOSE_ENTRY}"),
            ("exit", f"{trade_id}:{PURPOSE_TAKE_PROFIT_1}"),
            ("exit", f"{trade_id}:{PURPOSE_TAKE_PROFIT_2}"),
        ])
        # The stop was never consumed, so it is still resting — and visible.
        stop = store.get_order_intent(f"{trade_id}:{PURPOSE_STOP_LOSS}")
        self.assertEqual(stop.status, "SUBMITTED")
        self.assertIn(stop.order_intent_id,
                      [i.order_intent_id for i in store.unresolved_order_intents(pipeline.run_id)])

    def test_paper_state_survives_a_fresh_handle_on_the_same_database(self):
        """Nothing about the run lives only in the process that produced it."""
        run_id = f"paper-{uuid.uuid4().hex[:12]}"
        pipeline = VersionBPaperPipeline(db_path=self.db_path, run_id=run_id,
                                         initial_balance=1000, strategy=FixtureStrategy(),
                                         fee_rate=0.0, slippage_rate=0.0)
        frames = self.frames()
        report = pipeline.run(frames["1h"], frames["15m"], frames["5m"], symbol=SYMBOL)
        pipeline.close()
        del pipeline

        reopened = VersionBStore(self.db_path)
        self.addCleanup(reopened.close)
        reconstructed = reopened.reconstruct_trade(report["trades"][0]["trade_id"])
        self.assertEqual(reconstructed["state"], "CLOSED")
        self.assertEqual(reconstructed["recomputed_exit_reason"], "TAKE_PROFIT_2")
        self.assertAlmostEqual(reconstructed["recomputed_net_pnl"], report["total_profit"])
        self.assertEqual(len(reconstructed["fills"]), 3)
        self.assertEqual(reconstructed["leverage"], 1.0)


# ─────────────────────────────────────────────────────────────────────
# 6. Restart during an open lifecycle, rebuilt from the DB alone
# ─────────────────────────────────────────────────────────────────────
class RestartDuringOpenLifecycleTests(UnifiedPathTestCase):
    def _run_until_crash(self, run_id):
        service_ref: dict = {}
        strategy = CrashingStrategy(service_ref)
        pipeline = VersionBPaperPipeline(db_path=self.db_path, run_id=run_id,
                                         initial_balance=1000, strategy=strategy,
                                         fee_rate=0.0, slippage_rate=0.0)
        service_ref["service"] = pipeline.service
        frames = self.frames()
        with self.assertRaises(CrashedMidLifecycle):
            pipeline.run(frames["1h"], frames["15m"], frames["5m"], symbol=SYMBOL)
        return pipeline

    def test_restart_rebuilds_the_open_position_from_rows_alone(self):
        run_id = f"paper-{uuid.uuid4().hex[:12]}"
        crashed = self._run_until_crash(run_id)

        # The position is genuinely open, and TP1 plus breakeven already happened.
        pre_crash = crashed.operational_state()
        self.assertEqual(pre_crash["open_position_count"], 1, pre_crash)
        position = pre_crash["positions"][0]
        self.assertTrue(position["tp1_hit"])
        self.assertTrue(position["sl_moved_to_be"])
        self.assertAlmostEqual(position["stop_loss"], position["initial_quantity"] and 100.0)
        self.assertAlmostEqual(position["remaining_quantity"], 5.0)
        trade_id = position["trade_id"]
        crashed.close()
        del crashed  # the process is gone; only the database remains

        resumed = VersionBPaperPipeline(db_path=self.db_path, run_id=run_id, resume=True)
        self.addCleanup(resumed.close)
        report = resumed.recovery_report
        self.assertTrue(report.is_clean, report.as_dict())
        self.assertEqual(report.hydrated_trades, [trade_id])
        self.assertEqual(report.tp1_taken_before_restart, [trade_id])
        self.assertEqual(report.breakeven_armed_before_restart, [trade_id])
        # Recovery reads; it must not re-append history.
        self.assertTrue(report.wrote_no_new_rows, report.as_dict())

        # trade_id / state / fills / TP1 / BE / remaining quantity / accounting
        comparison = resumed.state_matches_durable_snapshot(pre_crash)
        self.assertTrue(comparison["matches"], comparison["differences"])

        revived = resumed.service.positions[SYMBOL]
        self.assertEqual(revived.lifecycle.trade_id, trade_id)
        self.assertEqual(revived.lifecycle.state.value, "PARTIALLY_CLOSED")
        self.assertTrue(revived.tp1_hit)
        self.assertTrue(revived.sl_moved_to_be)
        self.assertAlmostEqual(revived.stop_loss, 100.0)  # breakeven, not 98.5
        self.assertAlmostEqual(revived.remaining_quantity, 5.0)
        self.assertAlmostEqual(revived.margin_locked, pre_crash["margin_locked"])
        self.assertAlmostEqual(resumed.service.balance, pre_crash["balance"])
        # Two entry/exit fills existed before the crash; hydration added none.
        self.assertEqual(len(revived.lifecycle.fills), 2)

    def test_pending_protection_is_still_pending_after_the_restart(self):
        run_id = f"paper-{uuid.uuid4().hex[:12]}"
        crashed = self._run_until_crash(run_id)
        trade_id = crashed.operational_state()["positions"][0]["trade_id"]
        crashed.close()
        del crashed

        resumed = VersionBPaperPipeline(db_path=self.db_path, run_id=run_id, resume=True)
        self.addCleanup(resumed.close)
        pending = resumed.recovery_report.pending_order_intents
        self.assertIn(f"{trade_id}:{PURPOSE_STOP_LOSS}", pending)
        self.assertIn(f"{trade_id}:{PURPOSE_TAKE_PROFIT_2}", pending)
        self.assertNotIn(f"{trade_id}:{PURPOSE_TAKE_PROFIT_1}", pending,
                         "TP1 was consumed before the crash")

    def test_recovering_twice_changes_nothing(self):
        """Idempotency: a second recovery must not duplicate or mutate state."""
        run_id = f"paper-{uuid.uuid4().hex[:12]}"
        crashed = self._run_until_crash(run_id)
        pre_crash = crashed.operational_state()
        crashed.close()
        del crashed

        resumed = VersionBPaperPipeline(db_path=self.db_path, run_id=run_id, resume=True)
        self.addCleanup(resumed.close)
        first = resumed.operational_state()
        second_report = recover_unified_state(resumed.store, run_id, resumed.service)
        self.assertEqual(second_report.hydrated_trades, [])
        self.assertEqual(second_report.skipped_already_loaded, [pre_crash["positions"][0]["trade_id"]])
        self.assertTrue(second_report.wrote_no_new_rows, second_report.as_dict())
        self.assertEqual(resumed.operational_state()["positions"], first["positions"])

        store = self.open_store()
        self.addCleanup(store.close)
        trade_id = pre_crash["positions"][0]["trade_id"]
        self.assertEqual(len(store.fills_for_trade(trade_id)), 2)
        self.assertEqual(len(store.events_for_trade(trade_id)), 4)

    def test_the_restarted_position_still_executes_correctly(self):
        """Recovery is only useful if the revived position still exits properly."""
        run_id = f"paper-{uuid.uuid4().hex[:12]}"
        crashed = self._run_until_crash(run_id)
        pre_crash = crashed.operational_state()
        trade_id = pre_crash["positions"][0]["trade_id"]
        crashed.close()
        del crashed

        resumed = VersionBPaperPipeline(db_path=self.db_path, run_id=run_id, resume=True)
        self.addCleanup(resumed.close)
        # The remaining 5 contracts reach TP2 exactly as they would have.
        event = resumed.service.process_bar(
            SYMBOL, {"open": 104.0, "high": 104.5, "low": 103.9, "close": 104.5},
            event_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        self.assertEqual(event["event_type"], "TAKE_PROFIT_2")
        self.assertEqual(event["state"], "CLOSED")
        self.assertAlmostEqual(event["remaining_quantity"], 0.0)

        store = self.open_store()
        self.addCleanup(store.close)
        row = store.get_trade(trade_id)
        self.assertEqual(row.state, "CLOSED")
        self.assertEqual(row.final_exit_reason, "TAKE_PROFIT_2")
        # Entry 100, TP1 102 x5, TP2 104.5 x5, zero fees.
        self.assertAlmostEqual(row.net_pnl, 32.5)

    def test_a_restart_cannot_silently_change_the_cost_model(self):
        """Resuming must reuse the rates the run executed with, not config.yaml."""
        run_id = f"paper-{uuid.uuid4().hex[:12]}"
        crashed = self._run_until_crash(run_id)
        crashed.close()
        del crashed

        # Same rates: resumes, and the revived position prices identically.
        resumed = VersionBPaperPipeline(db_path=self.db_path, run_id=run_id, resume=True)
        self.addCleanup(resumed.close)
        self.assertEqual(resumed.service.fee_rate, 0.0)
        self.assertEqual(resumed.service.slippage_rate, 0.0)

        # Different rates: refused rather than applied to an open trade.
        with self.assertRaises(ValueError) as caught:
            VersionBPaperPipeline(db_path=self.db_path, run_id=run_id, resume=True,
                                  fee_rate=0.0004)
        self.assertIn("fee_rate", str(caught.exception))

    def test_a_run_without_durable_leverage_refuses_to_hydrate(self):
        """Fail closed rather than rebuild margin on a guessed leverage."""
        run_id = f"paper-{uuid.uuid4().hex[:12]}"
        store = self.open_store()
        self.addCleanup(store.close)
        store.create_run(
            run_id=run_id, environment="paper", code_version="test", strategy_version="test",
            config_hash="test", data_hash=None, execution_model_version="vb-test",
            universe=[SYMBOL], effective_config={}, initial_balance=1000.0,
        )
        store.create_trade(trade_id="T-NOLEV", run_id=run_id, signal_id=None, symbol=SYMBOL,
                           side="long", state="OPEN", initial_quantity=10.0,
                           remaining_quantity=10.0)
        store.append_event(event_id="T-NOLEV:e1", trade_id="T-NOLEV", sequence=1,
                           event_type="ENTRY_FILLED",
                           event_time=datetime(2026, 1, 1, tzinfo=timezone.utc), payload={})
        store.append_event(event_id="T-NOLEV:e2", trade_id="T-NOLEV", sequence=2,
                           event_type="PROTECTION_PLACED",
                           event_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
                           payload={"stop_loss": 98.5, "take_profit_1": 102.0,
                                    "take_profit_2": 104.5})
        store.record_fill(fill_id="T-NOLEV:f1", trade="T-NOLEV", order_intent=None,
                          role="entry", side="long",
                          fill_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
                          price=100.0, quantity=10.0, fee=0.0, slippage=0.0, funding=0.0)

        service = VersionBExecutionService(initial_balance=1000.0, fee_rate=0.0,
                                           slippage_rate=0.0, store=store, run_id=run_id)
        report = recover_unified_state(store, run_id, service)
        self.assertFalse(report.is_clean)
        self.assertEqual(report.hydrated_trades, [])
        self.assertEqual(report.hydration_failures,
                         [{"trade_id": "T-NOLEV", "reason": "missing durable leverage"}])
        self.assertEqual(service.positions, {})


# ─────────────────────────────────────────────────────────────────────
# 9. Legacy paths and silent fallbacks
# ─────────────────────────────────────────────────────────────────────
class LegacyFallbackClosureTests(UnifiedPathTestCase):
    def test_implicit_paper_no_longer_runs_outside_version_b(self):
        """The default entry point used to start a memory-only legacy loop."""
        from core.bot import LegacyPaperPathDisabled, TradingBot

        os.environ["TRADING_MODE"] = "paper"
        with self.assertRaises(LegacyPaperPathDisabled):
            TradingBot()

    def test_the_legacy_path_only_opens_with_an_explicit_recorded_override(self):
        from core.bot import LegacyPaperPathDisabled, TradingBot

        os.environ["TRADING_MODE"] = "paper"
        # Still refused without the override.
        with self.assertRaises(LegacyPaperPathDisabled):
            TradingBot()
        # The override is a named, loud opt-in — never a silent default.
        import inspect

        signature = inspect.signature(TradingBot.__init__)
        self.assertFalse(signature.parameters["allow_legacy_paper"].default)

    def test_the_gate_paper_module_tests_the_version_b_paper_engine(self):
        """`test_version_b_paper.py` used to exercise the legacy in-memory
        exchange while counting towards the Version B contract."""
        tree = ast.parse((REPO_ROOT / "test_version_b_paper.py").read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        self.assertIn("core.version_b_paper", imported)
        self.assertNotIn("core.paper_trading", imported)


class _FixtureMarketData:
    def __init__(self, frames):
        self.frames = frames
        self.calls = []

    def get_complete_dataframe(self, symbol, timeframe):
        self.calls.append((symbol, timeframe))
        return self.frames[timeframe].copy()


if __name__ == "__main__":
    unittest.main()
