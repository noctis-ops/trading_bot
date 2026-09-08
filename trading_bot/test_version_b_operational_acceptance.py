"""Operational Acceptance for the event-driven Version B Paper runtime.

The bar here is not "a class exists".  Each test drives the same runtime
through ``start → event → decide → risk → intent → fill → lifecycle → persist``
and then either crashes it, duplicates an event, degrades the venue, or starts
a second instance — and asserts what the database says afterwards.

Nothing here reaches a network.  The venue is a deterministic double that
implements the same ``ExternalOrderAdapter`` contract as the real path, so
UNKNOWN, partial fills, and unconfirmed protection exercise real intent
semantics without proving anything about Binance.
"""

import ast
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from core.version_b_paper import VersionBPaperPipeline
from core.version_b_runtime import (
    DECISION_TIMEFRAME,
    EXIT_TIMEFRAME,
    AuditSeverity,
    DeterministicExchangeDouble,
    DeterministicMarketFeed,
    EventOutcome,
    HealthStatus,
    MarketEvent,
    NotPrimaryInstance,
    RuntimeNotRunning,
    StaleDataRejected,
    VersionBPaperRuntime,
)
from data.time_alignment import candle_close_time
from database.version_b_store import (
    DecisionRecord,
    FillRecord,
    LifecycleEventRecord,
    OrderIntentRecord,
    RuntimeEventRecord,
    TradeLifecycleRecord,
    VersionBStore,
)
from test_version_b_backtest import FixtureStrategy
from test_version_b_replay_parity import replay_frames

SYMBOL = "BTC/USDT"
RUNTIME_MODULES = [
    "core/version_b_runtime.py",
    "core/version_b_paper.py",
    "core/version_b_recovery.py",
    "core/version_b_replay.py",
]
LEGACY_MODULES = {"core.risk_manager", "core.order_manager", "core.paper_trading",
                  "core.exchange", "backtesting.backtesting_advanced"}
REPO_ROOT = Path(__file__).resolve().parent


class AlwaysApproveStrategy(FixtureStrategy):
    """Approves on every call, so position-already-open is reachable."""

    def check_buy_signal(self, df_1h, df_15m, df_5m):
        self.calls += 1
        return True, {"signal": "BUY", "entry_price": 100.0, "atr": 1.0}


class OperationalTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = Path(self.tmp.name) / "runtime.sqlite"
        self.frames = replay_frames()
        self.feed = DeterministicMarketFeed(self.frames, symbol=SYMBOL)
        self.events = self.feed.events()

    def make_runtime(self, *, run_id="RT", strategy=None, exchange=None, **kwargs):
        runtime = VersionBPaperRuntime(
            db_path=self.db_path, run_id=run_id, initial_balance=1000,
            strategy=strategy or FixtureStrategy(), fee_rate=0.0, slippage_rate=0.0,
            symbol=SYMBOL, direction="long", exchange=exchange, **kwargs,
        )
        return runtime

    def feed_until(self, runtime, events, predicate, start=0):
        """Feed events one at a time until predicate(outcome, index) is true."""
        for index in range(start, len(events)):
            outcome = runtime.on_event(events[index])
            if predicate(outcome, index):
                return index
        return len(events)

    def all_entry_client_order_ids(self, runtime):
        """Every client order id an entry submission could carry."""
        return [
            f"{runtime.run_id}-{runtime.engine._signal_id(runtime.run_id, SYMBOL, 'long', e.decision_time)}-ENTRY"
            for e in self.events if e.is_decision_boundary and e.decision_time is not None
        ]

    def reopen_store(self):
        store = VersionBStore(self.db_path)
        self.addCleanup(store.close)
        return store


# ─────────────────────────────────────────────────────────────────────
# 1. Normal bar-by-bar operation
# ─────────────────────────────────────────────────────────────────────
class NormalBarByBarTests(OperationalTestCase):
    def test_events_arrive_individually_and_produce_one_trade(self):
        runtime = self.make_runtime()
        runtime.start()
        self.addCleanup(runtime.close)
        tally = runtime.run_stream(self.events)
        runtime.close_at_end_of_data(100.0, self.events[-1].emitted_at.to_pydatetime())

        self.assertEqual(tally[EventOutcome.EXECUTED], 1)
        self.assertEqual(tally[EventOutcome.DATA_REJECTED], 86)
        self.assertEqual(tally[EventOutcome.NO_SIGNAL], 32)
        self.assertEqual(tally[EventOutcome.EXIT_PROCESSED], 2)
        # Every event that arrived is recorded exactly once.
        self.assertEqual(runtime.store.processed_event_count("RT"), len(self.events))
        self.assertEqual(len(runtime.engine._trade_rows()), 1)
        self.assertAlmostEqual(sum(t["profit"] for t in runtime.engine._trade_rows()), 32.5)
        runtime.shutdown()

    def test_the_engine_never_receives_the_whole_frame(self):
        """No future data: only closed bars plus the open of the bar starting."""
        runtime = self.make_runtime()
        runtime.start()
        self.addCleanup(runtime.close)
        halfway = len(self.events) // 2
        for event in self.events[:halfway]:
            runtime.on_event(event)
            for timeframe, frame in runtime.frames.items():
                if frame.empty:
                    continue
                closed_at = candle_close_time(frame.index[-1], timeframe)
                # Nothing that closes after this event may be present yet.
                self.assertLessEqual(closed_at, event.emitted_at,
                                     f"{timeframe} leaked a future bar")
        runtime.shutdown()

    def test_a_decision_only_happens_on_a_decision_boundary(self):
        runtime = self.make_runtime()
        runtime.start()
        self.addCleanup(runtime.close)
        decisions_before = 0
        for event in self.events:
            before = len(runtime.engine.decisions)
            runtime.on_event(event)
            if len(runtime.engine.decisions) > before:
                self.assertTrue(event.is_decision_boundary,
                                "a decision was taken off a non-decision event")
                decisions_before += 1
        self.assertEqual(decisions_before, 119)
        runtime.shutdown()

    def test_events_are_refused_before_start_and_after_shutdown(self):
        runtime = self.make_runtime()
        self.addCleanup(runtime.close)
        with self.assertRaises(RuntimeNotRunning):
            runtime.on_event(self.events[0])
        runtime.start()
        runtime.on_event(self.events[0])
        runtime.shutdown()
        with self.assertRaises(RuntimeNotRunning):
            runtime.on_event(self.events[1])


# ─────────────────────────────────────────────────────────────────────
# 2/3/10. Crash and restart mid-lifecycle, then continue
# ─────────────────────────────────────────────────────────────────────
class CrashRestartContinueTests(OperationalTestCase):
    def test_restart_during_an_open_lifecycle_then_continue(self):
        runtime = self.make_runtime()
        runtime.start()
        opened_at = self.feed_until(
            runtime, self.events, lambda outcome, i: outcome == EventOutcome.EXECUTED)
        pre_crash = runtime.operational_state()
        trade_id = pre_crash["positions"][0]["trade_id"]
        self.assertEqual(pre_crash["open_position_count"], 1)
        runtime.close()
        del runtime  # the process is gone

        resumed = VersionBPaperRuntime(
            db_path=self.db_path, run_id="RT", resume=True, initial_balance=1000,
            strategy=FixtureStrategy(), fee_rate=0.0, slippage_rate=0.0,
            holder="supervisor")
        # A crashed process still holds the lock; taking over is explicit.
        with self.assertRaises(NotPrimaryInstance):
            resumed.start()
        resumed.start(takeover=True)
        self.addCleanup(resumed.close)
        self.assertIn("LOCK_TAKEN_OVER",
                      [a.code for a in resumed.store.audit_trail("RT")])
        self.assertTrue(resumed.recovery_report.is_clean, resumed.recovery_report.as_dict())
        self.assertEqual(resumed.recovery_report.hydrated_trades, [trade_id])
        self.assertEqual(resumed.service.positions[SYMBOL].lifecycle.trade_id, trade_id)

        # Continue from where the crash happened, not from the beginning.
        resumed.run_stream(self.events[opened_at + 1:])
        resumed.close_at_end_of_data(100.0, self.events[-1].emitted_at.to_pydatetime())
        trades = resumed.engine._trade_rows()
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["exit_type"], "TAKE_PROFIT_2")
        self.assertAlmostEqual(trades[0]["profit"], 32.5)
        resumed.shutdown()

    def test_restart_after_tp1_and_breakeven_keeps_tp2_protection_alive(self):
        runtime = self.make_runtime()
        runtime.start()
        # Stop right after TP1 has been processed and breakeven armed.
        self.feed_until(runtime, self.events,
                        lambda outcome, i: outcome == EventOutcome.EXIT_PROCESSED)
        pre_crash = runtime.operational_state()
        position = pre_crash["positions"][0]
        self.assertTrue(position["tp1_hit"])
        self.assertTrue(position["sl_moved_to_be"])
        self.assertAlmostEqual(position["stop_loss"], 100.0)
        trade_id = position["trade_id"]
        events_done = pre_crash["events_processed"]
        runtime.close()
        del runtime

        resumed = VersionBPaperRuntime(
            db_path=self.db_path, run_id="RT", resume=True, initial_balance=1000,
            strategy=FixtureStrategy(), fee_rate=0.0, slippage_rate=0.0,
            holder="supervisor")
        resumed.start(takeover=True)
        self.addCleanup(resumed.close)
        revived = resumed.service.positions[SYMBOL]
        self.assertTrue(revived.tp1_hit, "TP1 state came back from rows")
        self.assertTrue(revived.sl_moved_to_be, "breakeven came back from rows")
        self.assertAlmostEqual(revived.stop_loss, 100.0)
        self.assertAlmostEqual(revived.remaining_quantity, 5.0)

        # TP2 protection is still resting in the database.
        store = self.reopen_store()
        stop = store.get_order_intent(f"{trade_id}:STOP_LOSS")
        self.assertEqual(stop.status, "SUBMITTED")
        self.assertAlmostEqual(stop.intended_price, 100.0, places=6,
                               msg="the resting stop is the breakeven level")

        # And the remainder still exits at TP2 after the restart.
        resumed.run_stream(self.events[events_done:])
        row = store.get_trade(trade_id)
        self.assertEqual(row.state, "CLOSED")
        self.assertEqual(row.final_exit_reason, "TAKE_PROFIT_2")
        self.assertAlmostEqual(row.net_pnl, 32.5)
        resumed.shutdown()

    def test_a_restart_continues_after_the_last_persisted_event(self):
        """Resume must not replay the window from the beginning."""
        runtime = self.make_runtime()
        runtime.start()
        stop = len(self.events) // 2
        runtime.run_stream(self.events[:stop])
        consumed = runtime.store.processed_event_count("RT")
        runtime.shutdown()
        runtime.close()
        del runtime

        resumed = VersionBPaperRuntime(
            db_path=self.db_path, run_id="RT", resume=True, initial_balance=1000,
            strategy=FixtureStrategy(), fee_rate=0.0, slippage_rate=0.0,
            holder="supervisor")
        resumed.start(takeover=True)
        self.addCleanup(resumed.close)
        self.assertEqual(consumed, stop)
        last = resumed.store.last_processed_event("RT")
        self.assertEqual(last.event_id, self.events[stop - 1].event_id)

        # Redelivering the already-consumed prefix executes nothing new.
        outcomes = [resumed.on_event(event) for event in self.events[:stop]]
        self.assertEqual(set(outcomes), {EventOutcome.DUPLICATE_IGNORED})
        self.assertEqual(resumed.store.processed_event_count("RT"), stop)
        resumed.shutdown()

    def test_crash_without_shutdown_still_recovers_and_the_lock_is_reclaimable(self):
        runtime = self.make_runtime()
        runtime.start()
        self.feed_until(runtime, self.events, lambda o, i: o == EventOutcome.EXECUTED)
        pre_crash = runtime.operational_state()
        holder = runtime.holder
        runtime.close()  # killed: no shutdown(), so the lock row is still held
        del runtime

        store = self.reopen_store()
        self.assertIsNotNone(store.lock_holder("RT"), "an ungraceful death keeps the lock")
        self.assertEqual(store.lock_holder("RT").holder, holder)

        # The same holder (a supervisor restarting the process) may reclaim it.
        resumed = VersionBPaperRuntime(
            db_path=self.db_path, run_id="RT", resume=True, initial_balance=1000,
            strategy=FixtureStrategy(), fee_rate=0.0, slippage_rate=0.0, holder=holder)
        resumed.start()
        self.addCleanup(resumed.close)
        self.assertEqual(resumed.service.positions[SYMBOL].lifecycle.trade_id,
                         pre_crash["positions"][0]["trade_id"])
        self.assertAlmostEqual(resumed.service.balance, pre_crash["balance"])
        resumed.shutdown()


# ─────────────────────────────────────────────────────────────────────
# 4. Duplicate events and duplicate execution attempts
# ─────────────────────────────────────────────────────────────────────
class DuplicateHandlingTests(OperationalTestCase):
    def test_a_redelivered_event_is_not_executed_twice(self):
        runtime = self.make_runtime()
        runtime.start()
        self.addCleanup(runtime.close)
        opened_at = self.feed_until(
            runtime, self.events, lambda outcome, i: outcome == EventOutcome.EXECUTED)
        entry_event = self.events[opened_at]
        trades_before = len(runtime.engine._trade_rows())
        decisions_before = len(runtime.engine.decisions)
        fills_before = len(self.reopen_store().fills_for_trade(
            runtime.operational_state()["positions"][0]["trade_id"]))

        self.assertEqual(runtime.on_event(entry_event), EventOutcome.DUPLICATE_IGNORED)
        self.assertEqual(runtime.on_event(entry_event), EventOutcome.DUPLICATE_IGNORED)

        self.assertEqual(len(runtime.engine.decisions), decisions_before)
        self.assertEqual(len(runtime.engine._trade_rows()), trades_before)
        store = self.reopen_store()
        self.assertEqual(
            len(store.fills_for_trade(runtime.operational_state()["positions"][0]["trade_id"])),
            fills_before)
        self.assertEqual(len(runtime.service.positions), 1)
        runtime.shutdown()

    def test_a_second_entry_while_open_is_refused_not_duplicated(self):
        runtime = self.make_runtime(strategy=AlwaysApproveStrategy())
        runtime.start()
        self.addCleanup(runtime.close)
        tally = runtime.run_stream(self.events)
        # The strategy approves on every cycle, yet at most one position exists
        # at a time: further approvals are recorded as POSITION_OPEN, never
        # executed as a second concurrent trade.
        self.assertLessEqual(len(runtime.service.positions), 1)
        self.assertGreaterEqual(tally[EventOutcome.POSITION_OPEN], 20)
        store = self.reopen_store()
        # Approvals while a position is open are recorded, never executed as a
        # second concurrent trade: one trade row per EXECUTED outcome.
        self.assertEqual(len(TradeLifecycleRecord.select()), tally[EventOutcome.EXECUTED])
        self.assertEqual(store.audit_trail("RT", severity=AuditSeverity.CRITICAL.value), [])
        runtime.shutdown()


# ─────────────────────────────────────────────────────────────────────
# 5/6/7. Venue degradation: UNKNOWN, partial fill, protection failure
# ─────────────────────────────────────────────────────────────────────
class VenueDegradationTests(OperationalTestCase):
    def _first_executing_decision(self):
        """Index of the decision event that actually opens a position.

        Runs on its own database: a probe sharing the runtime's store would
        mark events consumed and the real run would skip them as duplicates.
        """
        probe = VersionBPaperRuntime(
            db_path=Path(self.tmp.name) / "probe.sqlite", run_id="PROBE",
            initial_balance=1000, strategy=FixtureStrategy(), fee_rate=0.0,
            slippage_rate=0.0, symbol=SYMBOL, direction="long")
        probe.start()
        try:
            for index, event in enumerate(self.events):
                if probe.on_event(event) == EventOutcome.EXECUTED:
                    return index
        finally:
            probe.shutdown()
            probe.close()
        raise AssertionError("no executing decision in the stream")

    def _entry_client_order_id(self, runtime, events):
        """Find the client order id the runtime will use for its entry."""
        for index, event in enumerate(events):
            if not event.is_decision_boundary or event.entry_open is None:
                continue
            return index, f"{runtime.run_id}-{runtime.engine._signal_id(runtime.run_id, SYMBOL, 'long', event.decision_time)}-ENTRY"
        raise AssertionError("no decision boundary in the stream")

    def test_execution_timeout_becomes_unknown_then_reconciles(self):
        index = self._first_executing_decision()
        exchange = DeterministicExchangeDouble()
        runtime = self.make_runtime(exchange=exchange)
        runtime.start()
        self.addCleanup(runtime.close)
        for cid in self.all_entry_client_order_ids(runtime):
            exchange.will_time_out(cid)
        for event in self.events[:index]:
            runtime.on_event(event)

        outcome = runtime.on_event(self.events[index])
        self.assertEqual(outcome, EventOutcome.NO_SIGNAL)
        # No fill was fabricated from a lost response.
        self.assertEqual(runtime.service.positions, {})
        self.assertEqual(len(runtime.pending_unknown_intents), 1)
        self.assertEqual(runtime.health.execution.status, HealthStatus.UNKNOWN)
        alerts = [(a.severity, a.code) for a in runtime.store.audit_trail("RT")]
        self.assertIn((AuditSeverity.CRITICAL.value, "ENTRY_SUBMIT_UNKNOWN"), alerts)

        # Reconciliation only resolves on concrete evidence.
        self.assertEqual(
            runtime.reconcile_pending()[0]["outcome"], "STILL_UNKNOWN")
        self.assertEqual(len(runtime.pending_unknown_intents), 1)

        from core.external_execution import Acknowledgement, ExchangeOrderStatus

        # Resolve the intent that was actually submitted, not an arbitrary one.
        submitted = runtime.pending_unknown_intents[0]["client_order_id"]
        exchange.resolve_after_timeout(submitted, Acknowledgement(
            status=ExchangeOrderStatus.NOT_FOUND, exchange_order_id=None))
        resolved = runtime.reconcile_pending()
        self.assertEqual(resolved[0]["client_order_id"], submitted)
        self.assertEqual(resolved[0]["outcome"], "RECONCILED_NOT_FOUND")
        self.assertEqual(runtime.pending_unknown_intents, [])
        self.assertIn((AuditSeverity.CRITICAL.value, "INTENT_RECONCILED_NOT_FOUND"),
                      [(a.severity, a.code) for a in runtime.store.audit_trail("RT")])
        runtime.shutdown()

    def test_partial_fill_is_recorded_and_alerted_not_silently_resized(self):
        index = self._first_executing_decision()
        exchange = DeterministicExchangeDouble()
        runtime = self.make_runtime(exchange=exchange)
        runtime.start()
        self.addCleanup(runtime.close)
        cid = (f"{runtime.run_id}-{runtime.engine._signal_id(runtime.run_id, SYMBOL, 'long', self.events[index].decision_time)}-ENTRY")
        exchange.will_fill_partially(cid, 4.0)

        for event in self.events[:index]:
            runtime.on_event(event)
        self.assertEqual(runtime.on_event(self.events[index]), EventOutcome.EXECUTED)
        accepted = runtime.engine.signals[-1]
        self.assertEqual(accepted["submitted_quantity"], 4.0)
        self.assertEqual(accepted["position_size"]["quantity"], 10.0,
                         "canonical risk sizing is not silently overridden")
        self.assertEqual(runtime.health.execution.status, HealthStatus.DEGRADED)
        self.assertIn((AuditSeverity.WARNING.value, "ENTRY_PARTIALLY_FILLED"),
                      [(a.severity, a.code) for a in runtime.store.audit_trail("RT")])
        runtime.shutdown()

    def test_unconfirmed_protection_fails_closed_with_an_emergency_exit(self):
        index = self._first_executing_decision()
        exchange = DeterministicExchangeDouble()
        runtime = self.make_runtime(exchange=exchange)
        runtime.start()
        self.addCleanup(runtime.close)
        # The venue rejects the resting stop for the trade the entry will create.
        exchange.will_reject(f"{runtime.run_id}-VB-BTCUSDT-000001-STOP_LOSS")

        for event in self.events[:index]:
            runtime.on_event(event)
        runtime.on_event(self.events[index])
        self.assertEqual(runtime.service.positions, {},
                         "an unprotected position must not be carried")
        codes = [a.code for a in runtime.store.audit_trail("RT")]
        self.assertIn("PROTECTION_NOT_CONFIRMED", codes)
        self.assertIn("EMERGENCY_FLATTEN", codes)
        self.assertEqual(runtime.health.execution.status, HealthStatus.DEGRADED)

        store = self.reopen_store()
        trade = TradeLifecycleRecord.select().first()
        self.assertEqual(trade.state, "CLOSED")
        self.assertEqual(trade.final_exit_reason, "EMERGENCY_EXIT")
        runtime.shutdown()


# ─────────────────────────────────────────────────────────────────────
# 8/9. Bad data: stale, gapped, out of order
# ─────────────────────────────────────────────────────────────────────
class BadDataTests(OperationalTestCase):
    def _first_valid_decision_index(self, runtime):
        """Index of the first decision event with valid warm-up."""
        for index, event in enumerate(self.events):
            if not event.is_decision_boundary:
                continue
            runtime.on_event(event)
            if runtime.engine.decisions and runtime.engine.decisions[-1]["outcome"] != "DATA_REJECTED":
                return index
        raise AssertionError("no valid decision found")

    def test_stale_data_refuses_a_new_entry_and_opens_the_breaker(self):
        runtime = self.make_runtime(stale_after_seconds=1800.0, data_fault_threshold=1)
        runtime.start()
        self.addCleanup(runtime.close)
        # Feed real history in order, then present a decision far in the future
        # relative to the newest closed bar.
        for event in self.events[:200]:
            runtime.on_event(event)
        newest = runtime.frames[DECISION_TIMEFRAME].index[-1]
        self.assertIsNotNone(newest)
        stale_decision_time = newest + pd.Timedelta("15min") + pd.Timedelta("3h")
        stale_event = MarketEvent(
            event_id="stale-decision", symbol=SYMBOL, timeframe=DECISION_TIMEFRAME,
            open_time=newest, bar=runtime.frames[DECISION_TIMEFRAME].iloc[-1].to_dict(),
            emitted_at=stale_decision_time, decision_time=stale_decision_time,
            entry_open=100.0,
        )
        with self.assertRaises(StaleDataRejected):
            runtime.on_event(stale_event)

        self.assertEqual(runtime.service.positions, {}, "no entry on stale data")
        self.assertTrue(runtime.health.circuit_open)
        self.assertEqual(runtime.health.circuit_reason, "STALE_DATA")
        self.assertEqual(runtime.health.data.status, HealthStatus.STALE)
        codes = [a.code for a in runtime.store.audit_trail("RT")]
        self.assertIn("STALE_DATA", codes)
        self.assertIn("CIRCUIT_BREAKER_OPEN", codes)

        # The breaker blocks entries but never blocks getting out.
        self.assertEqual(runtime.health.circuit_open, True)
        runtime.shutdown()

    def test_an_open_circuit_breaker_blocks_entries_but_not_exits(self):
        runtime = self.make_runtime(data_fault_threshold=1)
        runtime.start()
        self.addCleanup(runtime.close)
        opened_at = self.feed_until(
            runtime, self.events, lambda outcome, i: outcome == EventOutcome.EXECUTED)
        runtime.health.circuit_open = True
        runtime.health.circuit_reason = "TEST"

        # Feed the rest of the stream in order.  Decisions are refused while
        # exits keep working, because getting out is never what you disable.
        outcomes = [runtime.on_event(event) for event in self.events[opened_at + 1:]]
        self.assertIn(EventOutcome.CIRCUIT_OPEN_REJECTED, outcomes)
        self.assertIn(EventOutcome.EXIT_PROCESSED, outcomes)
        self.assertIn(EventOutcome.CIRCUIT_OPEN_REJECTED, outcomes)
        self.assertIn((AuditSeverity.WARNING.value, "ENTRY_BLOCKED_CIRCUIT_OPEN"),
                      [(a.severity, a.code) for a in runtime.store.audit_trail("RT")])
        # The breaker reason is one that does not auto-clear, so it stayed open.
        self.assertTrue(runtime.health.circuit_open)
        runtime.shutdown()

    def test_out_of_order_events_are_refused_and_never_traded_on(self):
        runtime = self.make_runtime()
        runtime.start()
        self.addCleanup(runtime.close)
        for event in self.events[:60]:
            runtime.on_event(event)
        newest_five = runtime.frames[EXIT_TIMEFRAME].index[-1]
        old = self.events[5]
        stale_bar = MarketEvent(
            event_id="out-of-order-1", symbol=SYMBOL, timeframe=EXIT_TIMEFRAME,
            open_time=old.open_time, bar=dict(old.bar), emitted_at=newest_five,
        )
        self.assertLess(stale_bar.open_time, newest_five)
        self.assertEqual(runtime.on_event(stale_bar), EventOutcome.OUT_OF_ORDER_REJECTED)
        # The old bar did not enter the working set.
        self.assertEqual(runtime.frames[EXIT_TIMEFRAME].index[-1], newest_five)
        self.assertIn((AuditSeverity.WARNING.value, "OUT_OF_ORDER_EVENT"),
                      [(a.severity, a.code) for a in runtime.store.audit_trail("RT")])
        runtime.shutdown()

    def test_a_data_gap_blocks_the_decision_instead_of_using_bad_data(self):
        frames = {name: frame.copy() for name, frame in self.frames.items()}
        # Punch a hole in the 1h history.
        hole = frames["1h"].index[10]
        frames["1h"] = frames["1h"].drop(index=hole)
        feed = DeterministicMarketFeed(frames, symbol=SYMBOL)
        runtime = VersionBPaperRuntime(
            db_path=self.db_path, run_id="RT", initial_balance=1000,
            strategy=FixtureStrategy(), fee_rate=0.0, slippage_rate=0.0,
            data_fault_threshold=1)
        runtime.start()
        self.addCleanup(runtime.close)
        runtime.run_stream(feed.events())
        self.assertEqual(runtime.service.positions, {}, "no entry on gapped data")
        self.assertEqual(len(runtime.engine._trade_rows()), 0)
        self.assertEqual(len(runtime.engine.signals), 0)
        codes = [a.code for a in runtime.store.audit_trail("RT")]
        self.assertIn("DATA_NOT_VALID", codes)
        self.assertIn("CIRCUIT_BREAKER_OPEN", codes)
        # The gap was classified as a gap, not silently accepted as warm-up.
        details = [a for a in runtime.store.audit_trail("RT") if a.code == "DATA_NOT_VALID"]
        self.assertTrue(any("DATA_GAP" in a.detail_json for a in details),
                        [a.detail_json for a in details][:3])
        runtime.shutdown()


# ─────────────────────────────────────────────────────────────────────
# 11/12. Single instance and legacy fallback closure
# ─────────────────────────────────────────────────────────────────────
class SingleInstanceAndLegacyTests(OperationalTestCase):
    def test_a_second_instance_of_the_same_run_is_refused(self):
        first = self.make_runtime(run_id="RT", holder="proc-A")
        first.start()
        self.addCleanup(first.close)
        store = self.reopen_store()
        self.assertEqual(store.lock_holder("RT").holder, "proc-A")

        second = self.make_runtime(run_id="RT", holder="proc-B")
        self.addCleanup(second.close)
        with self.assertRaises(NotPrimaryInstance):
            second.start()
        self.assertFalse(second.started)
        self.assertEqual(store.lock_holder("RT").holder, "proc-A",
                         "the refused instance must not steal ownership")
        first.shutdown()
        self.assertIsNone(store.lock_holder("RT"))

    def test_after_a_graceful_shutdown_a_new_instance_may_take_over(self):
        first = self.make_runtime(run_id="RT", holder="proc-A")
        first.start()
        first.run_stream(self.events[:50])
        first.shutdown()
        first.close()

        second = self.make_runtime(run_id="RT", holder="proc-B")
        second.start()
        self.addCleanup(second.close)
        self.assertTrue(second.started)
        self.assertEqual(self.reopen_store().lock_holder("RT").holder, "proc-B")
        second.shutdown()

    def test_the_runtime_cannot_reach_a_legacy_or_real_exchange_path(self):
        offenders = {}
        for relative in RUNTIME_MODULES:
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
        self.assertEqual(offenders, {}, f"runtime reaches a legacy path: {offenders}")

    def test_legacy_paper_still_cannot_be_an_automatic_fallback(self):
        from core.bot import LegacyPaperPathDisabled, TradingBot

        os.environ["TRADING_MODE"] = "paper"
        with self.assertRaises(LegacyPaperPathDisabled):
            TradingBot()
        import inspect

        self.assertFalse(
            inspect.signature(TradingBot.__init__).parameters["allow_legacy_paper"].default,
            "allow_legacy_paper must default to False")


# ─────────────────────────────────────────────────────────────────────
# Operational controls
# ─────────────────────────────────────────────────────────────────────
class OperationalControlsTests(OperationalTestCase):
    def test_health_state_is_stored_and_survives_a_restart(self):
        runtime = self.make_runtime(data_fault_threshold=1)
        runtime.start()
        for event in self.events[:60]:
            runtime.on_event(event)
        runtime.health.circuit_open = True
        runtime.health.circuit_reason = "TEST_REASON"
        runtime.persist_state()
        runtime.shutdown()
        runtime.close()
        del runtime

        store = self.reopen_store()
        health = store.operational_state("RT")["health"]
        self.assertTrue(health["circuit_open"])
        self.assertEqual(health["circuit_reason"], "TEST_REASON")
        self.assertEqual(health["data"]["status"], HealthStatus.HEALTHY.value)

        resumed = VersionBPaperRuntime(
            db_path=self.db_path, run_id="RT", resume=True, initial_balance=1000,
            strategy=FixtureStrategy(), fee_rate=0.0, slippage_rate=0.0)
        self.addCleanup(resumed.close)
        self.assertTrue(resumed.health.circuit_open,
                        "a restarted runtime must inherit the open breaker")
        self.assertEqual(resumed.health.circuit_reason, "TEST_REASON")

    def test_component_failures_produce_audit_events(self):
        runtime = self.make_runtime()
        runtime.start()
        self.addCleanup(runtime.close)
        runtime.audit("execution", AuditSeverity.CRITICAL, "TEST_ALERT", {"why": "test"})
        trail = runtime.store.audit_trail("RT")
        self.assertIn("TEST_ALERT", [a.code for a in trail])
        self.assertEqual(
            runtime.store.audit_trail("RT", severity=AuditSeverity.CRITICAL.value)[0].code,
            "TEST_ALERT")
        runtime.shutdown()

    def test_graceful_shutdown_keeps_the_position_open_and_releases_the_lock(self):
        runtime = self.make_runtime()
        runtime.start()
        self.feed_until(runtime, self.events, lambda o, i: o == EventOutcome.EXECUTED)
        trade_id = runtime.operational_state()["positions"][0]["trade_id"]
        runtime.shutdown()
        store = self.reopen_store()
        self.assertIsNone(store.lock_holder("RT"))
        self.assertEqual(store.get_trade(trade_id).state, "PARTIALLY_CLOSED"
                         if store.get_trade(trade_id).remaining_quantity < 10 else "OPEN")
        self.assertIn("RUNTIME_SHUTDOWN", [a.code for a in store.audit_trail("RT")])
        runtime.close()

    def test_end_of_data_close_is_explicit_and_not_part_of_shutdown(self):
        runtime = self.make_runtime()
        runtime.start()
        self.feed_until(runtime, self.events, lambda o, i: o == EventOutcome.EXECUTED)
        runtime.shutdown()
        store = self.reopen_store()
        self.assertNotIn("END_OF_DATA", [e.event_type for e in LifecycleEventRecord.select()])
        runtime.close()


# ─────────────────────────────────────────────────────────────────────
# Parity: the same stream, event-by-event vs the replay oracle
# ─────────────────────────────────────────────────────────────────────
class EventDrivenReplayParityTests(OperationalTestCase):
    def _run_both(self):
        runtime = self.make_runtime(run_id="PARITY")
        runtime.start()
        runtime.run_stream(self.events)
        runtime.close_at_end_of_data(100.0, self.events[-1].emitted_at.to_pydatetime())
        runtime.shutdown()
        runtime_state = runtime.operational_state()
        runtime_decisions = list(runtime.engine.decisions)
        runtime.close()

        oracle = VersionBPaperPipeline(
            db_path=Path(self.tmp.name) / "oracle.sqlite", initial_balance=1000,
            strategy=FixtureStrategy(), fee_rate=0.0, slippage_rate=0.0)
        self.addCleanup(oracle.close)
        report = oracle.run(self.frames["1h"], self.frames["15m"], self.frames["5m"],
                            symbol=SYMBOL)
        oracle.close()
        return runtime_state, runtime_decisions, report

    def test_decisions_intents_fills_lifecycle_and_pnl_all_match_the_oracle(self):
        runtime_state, runtime_decisions, report = self._run_both()

        # Decisions: same count, same outcomes in the same order.
        store = self.reopen_store()
        durable = list(DecisionRecord.select().where(DecisionRecord.run == "PARITY")
                       .order_by(DecisionRecord.decision_time))
        self.assertEqual(len(durable), len(report["decisions"]))
        self.assertEqual([d.outcome for d in durable],
                         [d["outcome"] for d in report["decisions"]])
        self.assertEqual([d.reason for d in durable],
                         [d["reason"] for d in report["decisions"]])

        # Order intents: same purposes, quantities, prices, and end states.
        trade_id = report["trades"][0]["trade_id"]
        intents = {i.purpose: (i.intended_quantity, round(i.intended_price, 6), i.status)
                   for i in store.order_intents_for_trade(trade_id)}
        self.assertEqual(intents, {
            "ENTRY": (10.0, 100.0, "FILLED"),
            "TAKE_PROFIT_1": (5.0, 102.0, "FILLED"),
            "TAKE_PROFIT_2": (5.0, 104.5, "FILLED"),
            # Amended to breakeven after TP1: the durable intent must show the
            # level actually resting, not the pre-TP1 one.
            "STOP_LOSS": (5.0, 100.0, "SUBMITTED"),
        })

        # Fills: same roles, prices, quantities.
        fills = [(f.role, round(f.price, 6), f.quantity) for f in store.fills_for_trade(trade_id)]
        self.assertEqual(fills, [("entry", 100.0, 10.0), ("exit", 102.0, 5.0),
                                 ("exit", 104.5, 5.0)])

        # Lifecycle transitions in order.
        self.assertEqual([e.event_type for e in store.events_for_trade(trade_id)],
                         ["ENTRY_FILLED", "PROTECTION_PLACED", "TAKE_PROFIT_1",
                          "BE_UPDATED", "TAKE_PROFIT_2", "TRADE_CLOSED"])

        # Accounting.
        self.assertAlmostEqual(report["total_profit"], 32.5)
        self.assertAlmostEqual(store.get_trade(trade_id).net_pnl, report["total_profit"])
        self.assertAlmostEqual(runtime_state["balance"], report["final_equity"])
        self.assertAlmostEqual(runtime_state["closed_trade_count"], report["total_trades"])

    def test_a_degraded_venue_diverges_from_the_oracle_and_says_so(self):
        """The only allowed divergence from the oracle is one that is recorded."""
        exchange = DeterministicExchangeDouble()
        runtime = self.make_runtime(run_id="DEGRADED", exchange=exchange)
        runtime.start()
        self.addCleanup(runtime.close)
        for i, event in enumerate(self.events):
            cid = (f"DEGRADED-{runtime.engine._signal_id('DEGRADED', SYMBOL, 'long', event.decision_time)}-ENTRY"
                   if event.is_decision_boundary and event.decision_time else "")
            if cid:
                exchange.will_time_out(cid)
            runtime.on_event(event)
        self.assertEqual(runtime.engine._trade_rows(), [],
                         "a venue that never confirms produces no trade")
        codes = [a.code for a in runtime.store.audit_trail("DEGRADED")]
        self.assertIn("ENTRY_SUBMIT_UNKNOWN", codes,
                      "the divergence from the oracle must be explained in the audit trail")
        runtime.shutdown()


if __name__ == "__main__":
    unittest.main()
