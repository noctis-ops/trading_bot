"""Operational acceptance for the Paper driver layer.

These tests drive the **driver**, not the runtime's internals.  The path under
test is:

    start → clock → market event → decision → risk → intent → execution
          → fill → lifecycle → DB → restart → resume

Nothing here calls ``VersionBPaperRuntime`` methods directly to produce a
result; the runtime is reached only through ``PaperDriver``, which is the same
way an operator reaches it.

Two negative controls are the point of the module: handing the runtime a whole
window must fail, and moving time outside the event boundary must fail.  A
driver that quietly accepted either would be a batch replay wearing an
operational costume.
"""

import ast
import inspect
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from core.version_b_clock import (
    ClockCannotRewind,
    DeterministicClock,
    WallClock,
)
from core.version_b_paper import VersionBPaperPipeline
from core.version_b_paper_driver import (
    DeterministicMarketAdapter,
    LiveMarketAdapter,
    MarketDataAdapter,
    PaperDriver,
    TIMEFRAMES,
)
from core.version_b_runtime import (
    AuditSeverity,
    EventOutcome,
    MarketEvent,
    NotPrimaryInstance,
    VersionBPaperRuntime,
    WindowRejected,
)
from database.version_b_store import (
    DecisionRecord,
    FillRecord,
    LifecycleEventRecord,
    OrderIntentRecord,
    RuntimeEventRecord,
    RuntimeLockRecord,
    TradeLifecycleRecord,
    VersionBStore,
)
from test_version_b_backtest import FixtureStrategy
from test_version_b_replay_parity import replay_frames

SYMBOL = "BTC/USDT"
REPO_ROOT = Path(__file__).resolve().parent


class DriverTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = Path(self.tmp.name) / "driver.sqlite"
        self.frames = replay_frames()

    def make_driver(self, *, run_id="DRV", resume=False, holder=None, **kwargs):
        kwargs.setdefault("heartbeat_every", 25)
        return PaperDriver(
            db_path=self.db_path,
            adapter=DeterministicMarketAdapter(self.frames, symbol=SYMBOL),
            run_id=run_id,
            initial_balance=1000,
            strategy=FixtureStrategy(),
            fee_rate=0.0,
            slippage_rate=0.0,
            resume=resume,
            holder=holder,
            **kwargs,
        )

    def step_until(self, driver, outcome, limit=None):
        """Deliver events one at a time until one produces `outcome`."""
        steps = 0
        while True:
            if limit is not None and steps >= limit:
                return None
            result = driver.step()
            steps += 1
            if result is None:
                return None
            if result == outcome:
                return steps

    def reopen_store(self):
        store = VersionBStore(self.db_path)
        self.addCleanup(store.close)
        return store


# ─────────────────────────────────────────────────────────────────────
# The one test that matters: the whole path through the driver
# ─────────────────────────────────────────────────────────────────────
class DriverEndToEndTests(DriverTestCase):
    def test_start_clock_event_decision_risk_intent_fill_lifecycle_db_restart_resume(self):
        driver = self.make_driver()
        self.assertIsNone(driver.runtime, "nothing operational exists before start")

        driver.start()
        self.addCleanup(driver.close)
        self.assertTrue(driver.started)
        self.assertIsNotNone(driver.store.lock_holder("DRV"), "start takes the lock")

        # 1. clock is deterministic and seeded from the market, not the wall
        self.assertIsInstance(driver.clock, DeterministicClock)
        seeded = driver.clock.now()
        self.assertLess(seeded, datetime(2026, 2, 1, tzinfo=timezone.utc))

        # 2. one event at a time; the clock follows the event
        first = driver.step()
        self.assertIsNotNone(first)
        self.assertGreaterEqual(driver.clock.now(), seeded)

        # 3. decision → risk → intent → fill → lifecycle, all durable
        steps = self.step_until(driver, EventOutcome.EXECUTED)
        self.assertIsNotNone(steps, "the fixture signal was never executed")
        state = driver.runtime.operational_state()
        trade_id = state["positions"][0]["trade_id"]
        self.assertEqual(state["open_position_count"], 1)
        events_before = state["events_processed"]

        store = self.reopen_store()
        self.assertIsNotNone(store.get_trade(trade_id))
        self.assertEqual(
            {i.purpose for i in store.order_intents_for_trade(trade_id)},
            {"ENTRY", "STOP_LOSS", "TAKE_PROFIT_1", "TAKE_PROFIT_2"},
        )
        self.assertEqual(
            [e.event_type for e in store.events_for_trade(trade_id)],
            ["ENTRY_FILLED", "PROTECTION_PLACED"],
        )
        self.assertEqual(
            [f.role for f in store.fills_for_trade(trade_id)], ["entry"])
        self.assertGreater(DecisionRecord.select().count(), 0)

        # 4. crash: no shutdown, so the lock is still held by a dead holder
        holder = driver.runtime.holder
        clock_at_crash = driver.clock.now()
        driver.close()
        del driver
        self.assertIsNotNone(store.lock_holder("DRV"))

        # 5. restart through a fresh driver, resuming from durable state only
        resumed = self.make_driver(resume=True, holder="supervisor")
        resumed.start(takeover=True)
        self.addCleanup(resumed.close)
        self.assertEqual(resumed.report.resumed_after_event,
                         store.last_processed_event("DRV").event_id)
        revived = resumed.runtime.service.positions[SYMBOL]
        self.assertEqual(revived.lifecycle.trade_id, trade_id)
        self.assertAlmostEqual(revived.remaining_quantity, 10.0)

        # 6. the clock did not rewind across the restart, and time continues
        resumed.clock.advance_to(clock_at_crash)

        # 7. resume: already-consumed events are skipped, not replayed
        report = resumed.run()
        self.assertGreater(report.events_skipped_as_processed, 0)
        self.assertEqual(report.outcome(EventOutcome.EXECUTED.value), 0,
                         "a resumed run must not open the trade a second time")

        # 8. and the trade still completes correctly after the restart
        row = store.get_trade(trade_id)
        self.assertEqual(row.state, "CLOSED")
        self.assertEqual(row.final_exit_reason, "TAKE_PROFIT_2")
        self.assertAlmostEqual(row.net_pnl, 32.5)
        self.assertEqual(
            [e.event_type for e in store.events_for_trade(trade_id)],
            ["ENTRY_FILLED", "PROTECTION_PLACED", "TAKE_PROFIT_1",
             "BE_UPDATED", "TAKE_PROFIT_2", "TRADE_CLOSED"],
        )
        self.assertEqual(TradeLifecycleRecord.select().count(), 1)
        resumed.shutdown()

    def test_shutdown_does_not_lose_the_event_position(self):
        driver = self.make_driver()
        driver.start()
        report = driver.run(max_events=200)
        self.assertEqual(report.stopped_reason, "max_events")
        consumed = driver.store.processed_event_count("DRV")
        last_event = driver.store.last_processed_event("DRV").event_id
        driver.shutdown()
        driver.close()
        del driver

        store = self.reopen_store()
        self.assertEqual(store.processed_event_count("DRV"), consumed)
        self.assertEqual(store.last_processed_event("DRV").event_id, last_event)
        self.assertIsNone(store.lock_holder("DRV"), "graceful shutdown releases the lease")

        resumed = self.make_driver(resume=True, holder="proc-2")
        resumed.start()
        self.addCleanup(resumed.close)
        self.assertEqual(resumed.report.resumed_after_event, last_event)
        second = resumed.run()
        self.assertEqual(second.events_skipped_as_processed, consumed)
        self.assertEqual(second.events_delivered, 510 - consumed)
        resumed.shutdown()

    def test_the_driver_never_holds_a_window(self):
        """The adapter owns the frames; the runtime only ever sees one event."""
        driver = self.make_driver()
        driver.start()
        self.addCleanup(driver.close)
        for _ in range(120):
            if driver.step() is None:
                break
            runtime = driver.runtime
            for timeframe, frame in runtime.frames.items():
                if frame.empty:
                    continue
                closed_at = frame.index[-1] + pd.Timedelta(
                    {"1h": "1h", "15m": "15min", "5m": "5min"}[timeframe])
                self.assertLessEqual(closed_at, driver.clock.now(),
                                     f"{timeframe} leaked a bar past the clock")
        driver.shutdown()


# ─────────────────────────────────────────────────────────────────────
# The adapter boundary
# ─────────────────────────────────────────────────────────────────────
class MarketDataAdapterBoundaryTests(DriverTestCase):
    def test_the_deterministic_feed_is_only_one_implementation(self):
        self.assertTrue(issubclass(DeterministicMarketAdapter, MarketDataAdapter))
        required = {"open", "next_event", "close"}
        self.assertTrue(required <= set(MarketDataAdapter.__abstractmethods__))
        # No method on the interface could hand back a frame.
        for name in required:
            signature = inspect.signature(getattr(MarketDataAdapter, name))
            self.assertNotIn("frame", signature.parameters)
            self.assertNotIn("frames", signature.parameters)
        source = (REPO_ROOT / "core/version_b_paper_driver.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        adapter_class = next(
            node for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "MarketDataAdapter")
        returned = set()
        for node in ast.walk(adapter_class):
            if isinstance(node, ast.Return) and node.value is not None:
                returned.add(ast.dump(node.value))
        self.assertFalse(any("DataFrame" in item for item in returned))

    def test_a_custom_adapter_drives_the_same_runtime_unchanged(self):
        """Swapping the source is a constructor argument, not a code change."""

        class CountingAdapter(MarketDataAdapter):
            symbol = SYMBOL

            def __init__(self, inner):
                self.inner = inner
                self.opened = 0

            def open(self):
                self.opened += 1
                self.inner.open()

            def next_event(self):
                return self.inner.next_event()

            def close(self):
                self.inner.close()

        adapter = CountingAdapter(DeterministicMarketAdapter(self.frames, symbol=SYMBOL))
        driver = PaperDriver(
            db_path=self.db_path, adapter=adapter, run_id="CUSTOM",
            initial_balance=1000, strategy=FixtureStrategy(),
            fee_rate=0.0, slippage_rate=0.0)
        driver.start()
        self.addCleanup(driver.close)
        self.assertEqual(adapter.opened, 1)
        report = driver.run()
        self.assertEqual(report.outcome(EventOutcome.EXECUTED.value), 1)
        self.assertAlmostEqual(driver.runtime.operational_state()["closed_trade_count"], 1)
        driver.shutdown()

    def test_the_adapter_yields_events_one_at_a_time(self):
        adapter = DeterministicMarketAdapter(self.frames, symbol=SYMBOL)
        adapter.open()
        self.addCleanup(adapter.close)
        seen = 0
        while True:
            event = adapter.next_event()
            if event is None:
                break
            self.assertIsInstance(event, MarketEvent)
            seen += 1
        self.assertEqual(seen, 510)
        self.assertEqual(adapter.delivered, 510)

    def test_the_live_adapter_boundary_refuses_to_be_constructed(self):
        with self.assertRaises(NotImplementedError) as caught:
            LiveMarketAdapter()
        self.assertIn("No live market data feed is authorised", str(caught.exception))

    def test_frames_can_come_from_local_csv_without_a_socket(self):
        directory = Path(self.tmp.name) / "frames"
        directory.mkdir()
        for name in TIMEFRAMES:
            frame = self.frames[name].copy()
            frame.insert(0, "timestamp",
                         frame.index.tz_convert("UTC").tz_localize(None))
            frame.reset_index(drop=True).to_csv(directory / f"{name}.csv", index=False)

        adapter = DeterministicMarketAdapter.from_directory(directory, symbol=SYMBOL)
        adapter.open()
        self.addCleanup(adapter.close)
        self.assertEqual(adapter.next_event().symbol, SYMBOL)

        with self.assertRaises(FileNotFoundError):
            DeterministicMarketAdapter.from_directory(Path(self.tmp.name) / "nope")

    def test_csv_frames_drive_the_same_runtime(self):
        directory = Path(self.tmp.name) / "frames"
        directory.mkdir()
        for name in TIMEFRAMES:
            frame = self.frames[name].copy()
            frame.insert(0, "timestamp",
                         frame.index.tz_convert("UTC").tz_localize(None))
            frame.reset_index(drop=True).to_csv(directory / f"{name}.csv", index=False)

        driver = PaperDriver(
            db_path=self.db_path,
            adapter=DeterministicMarketAdapter.from_directory(directory, symbol=SYMBOL),
            run_id="CSV", initial_balance=1000, strategy=FixtureStrategy(),
            fee_rate=0.0, slippage_rate=0.0)
        driver.start()
        self.addCleanup(driver.close)
        report = driver.run()
        self.assertEqual(report.outcome(EventOutcome.EXECUTED.value), 1)
        self.assertAlmostEqual(driver.runtime.operational_state()["closed_trade_count"], 1)
        driver.shutdown()


# ─────────────────────────────────────────────────────────────────────
# Clock separation
# ─────────────────────────────────────────────────────────────────────
class ClockSeparationTests(DriverTestCase):
    def test_the_deterministic_clock_never_reads_the_wall_clock(self):
        clock = DeterministicClock(datetime(2020, 1, 1, tzinfo=timezone.utc))
        self.assertEqual(clock.now().year, 2020)
        clock.advance_to(datetime(2020, 1, 2, tzinfo=timezone.utc))
        self.assertEqual(clock.now().day, 2)
        self.assertEqual(clock.advances, 1)
        # Idempotent: advancing to the same instant is not an advance.
        clock.advance_to(datetime(2020, 1, 2, tzinfo=timezone.utc))
        self.assertEqual(clock.advances, 1)

    def test_the_clock_refuses_to_move_backwards(self):
        clock = DeterministicClock(datetime(2020, 1, 5, tzinfo=timezone.utc))
        with self.assertRaises(ClockCannotRewind):
            clock.advance_to(datetime(2020, 1, 4, tzinfo=timezone.utc))

    def test_the_driver_refuses_an_event_outside_the_event_boundary(self):
        """Bypassing the clock is a failure, not a tolerance."""
        driver = self.make_driver()
        driver.start()
        self.addCleanup(driver.close)
        for _ in range(50):
            driver.step()
        now = driver.clock.now()
        past = MarketEvent(
            event_id="rewind-attempt", symbol=SYMBOL, timeframe="5m",
            open_time=pd.Timestamp(now - timedelta(hours=6)),
            bar={"open": 100.0, "high": 100.1, "low": 99.9, "close": 100.0, "volume": 1.0},
            emitted_at=pd.Timestamp(now - timedelta(hours=6)),
        )
        with self.assertRaises(ClockCannotRewind):
            driver.clock.advance_to(driver._event_time(past))
        driver.shutdown()

    def test_durable_timestamps_come_from_the_injected_clock(self):
        driver = self.make_driver()
        driver.start()
        self.addCleanup(driver.close)
        driver.run(max_events=40)
        lock = driver.store.lock_holder("DRV")
        # The heartbeat is stamped by the deterministic clock, so it sits inside
        # the event timeline rather than at "whenever the test ran".
        self.assertLessEqual(lock.heartbeat_at.replace(tzinfo=timezone.utc)
                             if lock.heartbeat_at.tzinfo is None else lock.heartbeat_at,
                             driver.clock.now() + timedelta(seconds=1))
        self.assertGreater(lock.heartbeat_at.year, 2020)
        self.assertLess(lock.heartbeat_at.year, 2027)
        driver.shutdown()

    def test_a_wall_clock_can_be_substituted_without_touching_the_runtime(self):
        driver = self.make_driver(clock=WallClock())
        driver.start()
        self.addCleanup(driver.close)
        self.assertIsInstance(driver.clock, WallClock)
        self.assertIs(driver.runtime.clock, driver.clock)
        report = driver.run(max_events=20)
        self.assertEqual(report.events_delivered, 20)
        driver.shutdown()


# ─────────────────────────────────────────────────────────────────────
# Negative controls
# ─────────────────────────────────────────────────────────────────────
class WindowAndBoundaryNegativeControlTests(DriverTestCase):
    def test_handing_the_runtime_a_window_fails(self):
        runtime = VersionBPaperRuntime(
            db_path=self.db_path, run_id="W", initial_balance=1000,
            strategy=FixtureStrategy(), fee_rate=0.0, slippage_rate=0.0)
        self.addCleanup(runtime.close)
        with self.assertRaises(WindowRejected) as caught:
            runtime.run(self.frames["1h"], self.frames["15m"], self.frames["5m"])
        self.assertIn("event-driven", str(caught.exception))
        # Nothing was executed by the refused call.
        self.assertEqual(len(runtime.engine._trade_rows()), 0)
        self.assertEqual(DecisionRecord.select().count(), 0)
        self.assertEqual(FillRecord.select().count(), 0)

    def test_the_runtime_exposes_no_batch_ingestion_path(self):
        """Structural guard: no public method may accept a frame or frames."""
        offenders = []
        for name, member in inspect.getmembers(VersionBPaperRuntime,
                                               predicate=inspect.isfunction):
            if name.startswith("_"):
                continue
            for parameter in inspect.signature(member).parameters:
                if parameter in {"frame", "frames", "df", "df_1h", "df_15m", "df_5m"}:
                    offenders.append(f"{name}({parameter})")
        self.assertEqual(offenders, [],
                         f"batch ingestion paths exist: {offenders}")

    def test_the_driver_has_no_window_entry_point_either(self):
        for name, member in inspect.getmembers(PaperDriver, predicate=inspect.isfunction):
            if name.startswith("_"):
                continue
            for parameter in inspect.signature(member).parameters:
                self.assertNotIn(parameter, {"frames", "df_1h", "df_15m", "df_5m"},
                                 f"PaperDriver.{name} accepts a window")

    def test_an_adapter_that_returns_a_window_is_not_a_valid_source(self):
        """A source with no one-at-a-time contract cannot drive the runtime."""

        class WindowAdapter(MarketDataAdapter):
            symbol = SYMBOL

            def open(self):
                pass

            def next_event(self):
                # Returns the whole frame instead of one event.
                return replay_frames()["5m"]

            def close(self):
                pass

        store = self.reopen_store()   # opens the database the assertions read
        driver = PaperDriver(
            db_path=self.db_path, adapter=WindowAdapter(), run_id="BAD",
            initial_balance=1000, strategy=FixtureStrategy(),
            fee_rate=0.0, slippage_rate=0.0)
        self.addCleanup(driver.close)
        with self.assertRaises(Exception):
            driver.start()
            driver.run(max_events=5)
            driver.shutdown()
        # Whatever it raises, the point is that it cannot silently trade a window.
        self.assertEqual(TradeLifecycleRecord.select().count(), 0)
        self.assertEqual(FillRecord.select().count(), 0)
        self.assertEqual(DecisionRecord.select().count(), 0)
        self.assertIsNone(store.lock_holder("BAD"),
                          "a source that is not a stream must not leave a held lease")

    def test_a_second_driver_cannot_run_the_same_paper_account(self):
        first = self.make_driver(holder="proc-A")
        first.start()
        self.addCleanup(first.close)
        second = self.make_driver(holder="proc-B")
        self.addCleanup(second.close)
        with self.assertRaises(NotPrimaryInstance):
            second.start()
        self.assertFalse(second.started)
        first.shutdown()


# ─────────────────────────────────────────────────────────────────────
# Lease / heartbeat is live, not dead code
# ─────────────────────────────────────────────────────────────────────
class LeaseAndHeartbeatTests(DriverTestCase):
    def test_the_driver_heartbeats_and_the_lease_advances(self):
        driver = self.make_driver(heartbeat_every=10)
        driver.start()
        self.addCleanup(driver.close)
        first = driver.store.lock_holder("DRV").heartbeat_at
        report = driver.run(max_events=100)
        self.assertEqual(report.heartbeats, 10)
        last = driver.store.lock_holder("DRV").heartbeat_at
        self.assertGreater(last, first, "the heartbeat actually moved")
        driver.shutdown()

    def test_a_holder_inside_its_lease_is_never_displaced(self):
        first = self.make_driver(holder="proc-A", lease_seconds=3600)
        first.start()
        self.addCleanup(first.close)
        first.run(max_events=10)

        second = self.make_driver(holder="proc-B", lease_seconds=3600)
        self.addCleanup(second.close)
        with self.assertRaises(NotPrimaryInstance):
            second.start()
        self.assertEqual(self.reopen_store().lock_holder("DRV").holder, "proc-A")

    def test_a_holder_past_its_lease_can_be_replaced(self):
        first = self.make_driver(holder="proc-A", lease_seconds=1)
        first.start()
        self.addCleanup(first.close)
        first.run(max_events=10)
        # Kill it without releasing, then move the clock past the lease.
        first.close()
        del first

        store = self.reopen_store()
        self.assertEqual(store.lock_holder("DRV").holder, "proc-A")

        future = DeterministicClock(datetime(2030, 1, 1, tzinfo=timezone.utc))
        second = PaperDriver(
            db_path=self.db_path,
            adapter=DeterministicMarketAdapter(self.frames, symbol=SYMBOL),
            run_id="DRV", initial_balance=1000, strategy=FixtureStrategy(),
            fee_rate=0.0, slippage_rate=0.0, resume=True, holder="proc-B",
            clock=future, lease_seconds=1)
        second.start()  # no explicit takeover: the lease expired
        self.addCleanup(second.close)
        self.assertTrue(second.started)
        self.assertEqual(store.lock_holder("DRV").holder, "proc-B")
        self.assertIn("LOCK_TAKEN_OVER",
                      [a.code for a in store.audit_trail("DRV")])
        second.shutdown()


# ─────────────────────────────────────────────────────────────────────
# Parity and preserved guarantees
# ─────────────────────────────────────────────────────────────────────
class DriverParityAndPreservationTests(DriverTestCase):
    def test_driver_output_matches_the_batch_replay_oracle(self):
        driver = self.make_driver(run_id="PARITY")
        driver.start()
        self.addCleanup(driver.close)
        driver.run()
        driver.runtime.close_at_end_of_data(100.0, driver.clock.now())
        driver.shutdown()

        oracle = VersionBPaperPipeline(
            db_path=Path(self.tmp.name) / "oracle.sqlite", initial_balance=1000,
            strategy=FixtureStrategy(), fee_rate=0.0, slippage_rate=0.0)
        self.addCleanup(oracle.close)
        report = oracle.run(self.frames["1h"], self.frames["15m"], self.frames["5m"],
                            symbol=SYMBOL)
        oracle.close()

        store = self.reopen_store()
        trade_id = report["trades"][0]["trade_id"]
        durable = list(DecisionRecord.select().where(DecisionRecord.run == "PARITY")
                       .order_by(DecisionRecord.decision_time))
        self.assertEqual([d.outcome for d in durable],
                         [d["outcome"] for d in report["decisions"]])
        self.assertEqual([d.reason for d in durable],
                         [d["reason"] for d in report["decisions"]])
        self.assertEqual(
            [(f.role, round(f.price, 6), f.quantity)
             for f in store.fills_for_trade(trade_id)],
            [("entry", 100.0, 10.0), ("exit", 102.0, 5.0), ("exit", 104.5, 5.0)])
        self.assertEqual(
            [e.event_type for e in store.events_for_trade(trade_id)],
            ["ENTRY_FILLED", "PROTECTION_PLACED", "TAKE_PROFIT_1",
             "BE_UPDATED", "TAKE_PROFIT_2", "TRADE_CLOSED"])
        self.assertAlmostEqual(store.get_trade(trade_id).net_pnl, report["total_profit"])
        self.assertAlmostEqual(report["total_profit"], 32.5)

    def test_every_guarantee_proven_before_the_driver_still_holds(self):
        """The driver layer must not weaken persistence, idempotency, or health."""
        driver = self.make_driver()
        driver.start()
        self.addCleanup(driver.close)
        executed_at = self.step_until(driver, EventOutcome.EXECUTED)
        self.assertIsNotNone(executed_at)
        trade_id = driver.runtime.operational_state()["positions"][0]["trade_id"]

        # idempotency: redelivering the entry event executes nothing new
        feed = DeterministicMarketAdapter(self.frames, symbol=SYMBOL)
        feed.open()
        entry_event = None
        for _ in range(executed_at):
            entry_event = feed.next_event()
        feed.close()
        fills_before = len(driver.store.fills_for_trade(trade_id))
        self.assertEqual(driver.runtime.on_event(entry_event),
                         EventOutcome.DUPLICATE_IGNORED)
        self.assertEqual(len(driver.store.fills_for_trade(trade_id)), fills_before)

        # health and audit are still live through the driver
        driver.runtime.health.circuit_open = True
        driver.runtime.health.circuit_reason = "DRIVER_TEST"
        driver.runtime.persist_state()
        driver.runtime.audit("runtime", AuditSeverity.WARNING,
                             "DRIVER_TEST_ALERT", {"why": "preserved"})
        self.assertIn("DRIVER_TEST_ALERT",
                      [a.code for a in driver.store.audit_trail("DRV")])

        # single instance and lock still enforced
        self.assertIsNotNone(driver.store.lock_holder("DRV"))
        driver.shutdown()

        store = self.reopen_store()
        health = store.operational_state("DRV")["health"]
        self.assertTrue(health["circuit_open"])
        self.assertEqual(health["circuit_reason"], "DRIVER_TEST")

    def test_single_symbol_remains_an_explicit_known_limit(self):
        """Documented, not hidden: one runtime owns one symbol."""
        driver = self.make_driver()
        driver.start()
        self.addCleanup(driver.close)
        self.assertEqual(driver.runtime.symbol, SYMBOL)
        other = MarketEvent(
            event_id="ETH/USDT:5m:x", symbol="ETH/USDT", timeframe="5m",
            open_time=pd.Timestamp("2025-12-31 00:00", tz="UTC"),
            bar={"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0},
            emitted_at=pd.Timestamp("2025-12-31 00:05", tz="UTC"),
        )
        self.assertEqual(driver.runtime.on_event(other), EventOutcome.IGNORED)
        source = (REPO_ROOT / "core/version_b_paper_driver.py").read_text(encoding="utf-8")
        self.assertIn("one symbol", source,
                      "the single-symbol limit must stay documented in the driver")
        driver.shutdown()


if __name__ == "__main__":
    unittest.main()
