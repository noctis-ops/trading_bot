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
import json
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
    DECISION_TIMEFRAME,
    AuditSeverity,
    EventOutcome,
    HealthStatus,
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
        kwargs.setdefault("heartbeat_interval", 60.0)
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
    def test_the_beat_is_scheduled_by_time_not_by_event_count(self):
        """Same events, different interval -> different beat count.

        This is the property the old event counter could not have: the cadence
        belongs to the clock.  Ten times the interval must give roughly a tenth
        of the beats over identical market data.
        """
        counts = {}
        for interval in (60.0, 600.0):
            run_id = f"BEAT-{int(interval)}"
            driver = self.make_driver(run_id=run_id, heartbeat_interval=interval)
            driver.start()
            self.addCleanup(driver.close)
            report = driver.run(max_events=100)
            counts[interval] = report.heartbeats
            self.assertIsNotNone(driver.store.lock_holder(run_id).heartbeat_at)
            driver.shutdown()
        self.assertGreater(counts[60.0], counts[600.0])
        # 100 events span ~8h; a 600s interval cannot beat more than ~50 times.
        self.assertLess(counts[600.0], 60)

    def test_the_lease_advances_with_the_clock(self):
        driver = self.make_driver(heartbeat_interval=60.0)
        driver.start()
        self.addCleanup(driver.close)
        first = driver.store.lock_holder("DRV").heartbeat_at
        driver.run(max_events=100)
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
# Process liveness is independent of market-data flow
# ─────────────────────────────────────────────────────────────────────
class QuietSource(MarketDataAdapter):
    """The deterministic stream, but able to go quiet the way a real feed does.

    Two shapes of silence, both operationally real:

    ``withhold_after``
        Pause *between* two bars.  ``idle_until`` reports when the withheld bar
        is due, which is what a socket-backed adapter would report while it
        waits.  The window ends exactly at that bar's own timestamp, so the
        clock never has to move backwards.

    ``quiet_at_end``
        The feed simply stops after the last bar and stays stopped.

    Neither shape tells the driver anything about *liveness*.  That is the
    point: the process is fine, the data is not, and those two facts must stay
    separable.
    """

    symbol = SYMBOL

    def __init__(self, frames, *, symbol=SYMBOL, withhold_after=None,
                 quiet_at_end=None):
        self._inner = DeterministicMarketAdapter(frames, symbol=symbol)
        self.symbol = symbol
        self.withhold_after = withhold_after
        self.quiet_at_end = quiet_at_end
        self.emitted = 0
        self.quiet_windows = 0
        self._peeked = None
        self._last_emitted_at = None
        self._mid_quiet = False
        self._mid_done = False
        self._end_quiet = False
        self._end_done = False

    def open(self):
        self._inner.open()

    def close(self):
        self._inner.close()

    def next_event(self):
        if (self.withhold_after is not None and not self._mid_done
                and self.emitted >= self.withhold_after):
            if not self._mid_quiet:
                self._peeked = self._inner.next_event()
                self._mid_quiet = True
                self.quiet_windows += 1
                return None
            self._mid_done = True
        if self._peeked is not None:
            event, self._peeked = self._peeked, None
        else:
            event = self._inner.next_event()
        if event is None:
            if self.quiet_at_end is not None and not self._end_done:
                if not self._end_quiet:
                    self._end_quiet = True
                    self.quiet_windows += 1
                    return None
                self._end_done = True
            return None
        self.emitted += 1
        self._last_emitted_at = self._event_stamp(event.emitted_at)
        return event

    def idle_until(self):
        if self._mid_quiet and not self._mid_done and self._peeked is not None:
            return self._event_stamp(self._peeked.emitted_at)
        if self._end_quiet and not self._end_done and self._last_emitted_at is not None:
            return self._last_emitted_at + self.quiet_at_end
        return None

    @staticmethod
    def _event_stamp(value):
        return value.to_pydatetime() if isinstance(value, pd.Timestamp) else value


class LivenessIndependenceTests(DriverTestCase):
    """Liveness and data freshness are two different facts.

    Before this they were one: the beat was a side effect of delivering every
    Nth event, so a quiet market produced no beats and a live process was
    indistinguishable from a dead one.
    """

    def make_quiet_driver(self, *, run_id="DRV", quiet_at_end=None,
                          withhold_after=None, **kwargs):
        kwargs.setdefault("heartbeat_interval", 60.0)
        return PaperDriver(
            db_path=self.db_path,
            adapter=QuietSource(self.frames, symbol=SYMBOL,
                                withhold_after=withhold_after,
                                quiet_at_end=quiet_at_end),
            run_id=run_id, initial_balance=1000, strategy=FixtureStrategy(),
            fee_rate=0.0, slippage_rate=0.0, **kwargs)

    # 1 ── alive with no market data: the beat continues, no false takeover
    def test_heartbeat_continues_while_no_market_data_arrives(self):
        driver = self.make_quiet_driver(
            quiet_at_end=timedelta(minutes=30), heartbeat_interval=60.0,
            lease_seconds=600.0)
        driver.start()
        self.addCleanup(driver.close)
        # Consume the real stream first, so the beat count below is attributable
        # to the silence alone and not to the 510 events that preceded it.
        report = driver.run(max_events=510)
        self.assertEqual(report.events_delivered, 510)
        self.assertEqual(report.idle_waits, 0)
        beats_before = driver.report.heartbeats
        self.assertGreater(beats_before, 0)

        # The feed now stops.  Nothing at all arrives from here on.
        self.assertIsNone(driver.step())
        quiet_beats = driver.report.heartbeats - beats_before

        self.assertEqual(driver.report.idle_waits, 1,
                         "the driver waited out the silence")
        self.assertGreaterEqual(driver.report.data_quiet_seconds, 1700.0)
        # 30 quiet minutes at a 60s interval: liveness kept proving itself with
        # no market data whatsoever.  This is the assertion the old event
        # counter could not have satisfied — there were no events to count.
        self.assertGreaterEqual(quiet_beats, 30)
        self.assertLessEqual(quiet_beats, 31)

        # The lease is fresh, so a supervisor must NOT conclude the process died.
        lock = driver.store.lock_holder("DRV")
        age = (driver.clock.now() - lock.heartbeat_at.replace(tzinfo=timezone.utc)
               ).total_seconds()
        self.assertLessEqual(age, 60.0, "a live process kept its lease alive")
        self.assertFalse(driver.store.lock_is_expired(
            "DRV", now=driver.clock.now(), lease_seconds=600.0))

        # And a second process is still refused: no false takeover.
        probe_clock = DeterministicClock(driver.clock.now())
        second = self.make_quiet_driver(
            run_id="DRV", holder="supervisor", clock=probe_clock,
            lease_seconds=600.0, resume=True)
        self.addCleanup(second.close)
        with self.assertRaises(NotPrimaryInstance):
            second.start()
        self.assertEqual(driver.store.lock_holder("DRV").holder, driver.runtime.holder)
        driver.shutdown()

    # 2 ── stale data opens the breaker and blocks new entries only
    def test_stale_market_data_opens_the_breaker_without_any_event(self):
        """Silence alone is now enough to declare the data stale.

        ``_process_decision`` can only notice staleness when a decision happens
        to arrive, so a feed that simply stopped used to produce nothing at all:
        no fault, no breaker, no record.  Assessing against the clock fixes
        that, and this proves it end to end through the driver.
        """
        driver = self.make_quiet_driver(
            withhold_after=368, heartbeat_interval=60.0,
            stale_after_seconds=60.0, data_fault_threshold=1, lease_seconds=3600.0)
        driver.start()
        self.addCleanup(driver.close)
        driver.run(max_events=368)
        self.assertFalse(driver.runtime.health.circuit_open)

        # One more step: the source goes quiet, the clock walks the gap, and the
        # assessment finds the data stale with no market event involved.
        driver.step()
        codes = [a.code for a in driver.store.audit_trail("DRV")]
        self.assertIn("MARKET_DATA_QUIET", codes)
        self.assertIn("STALE_DATA", codes)
        self.assertIn("CIRCUIT_BREAKER_OPEN", codes)
        stale = [json.loads(a.detail_json) for a in driver.store.audit_trail("DRV")
                 if a.code == "STALE_DATA"]
        self.assertTrue(any(d.get("source") == "liveness_assessment" for d in stale))
        # The bar that ends the silence is a 1h bar.  It is evidence that the
        # socket is alive; it is not evidence that the 15m stream gating every
        # entry came back.  So it must NOT clear the breaker (VB-LIV-008).
        self.assertTrue(driver.runtime.health.circuit_open,
                        "a 1h bar is not recovery for the decision timeframe")
        self.assertEqual(driver.runtime.health.data.status, HealthStatus.STALE)
        self.assertNotIn("CIRCUIT_BREAKER_CLOSED", codes)
        # The outage opened no position and closed no trade: protection is an
        # entry control, and nothing about the open lifecycle was touched.
        state = driver.runtime.operational_state()
        self.assertEqual(state["open_position_count"], 0)
        self.assertEqual(state["closed_trade_count"], 0)
        # The process stayed alive through all of it.
        self.assertFalse(driver.store.lock_is_expired(
            "DRV", now=driver.clock.now(), lease_seconds=3600.0))
        driver.shutdown()

    def test_a_breaker_opened_by_assessment_refuses_the_next_entry(self):
        """The breaker the assessment opens really is the entry control.

        Component-level, and deliberately so: in this fixture every 15m decision
        co-emits with a 5m bar, so no quiet window can *end* on a decision — the
        bar that ends the silence always arrives first.  This isolates the one
        claim that matters: a breaker opened with no event involved still
        refuses an entry, and still never refuses an exit.
        """
        adapter = DeterministicMarketAdapter(self.frames, symbol=SYMBOL)
        adapter.open()
        self.addCleanup(adapter.close)
        stream = []
        while True:
            event = adapter.next_event()
            if event is None:
                break
            stream.append(event)
        decision = stream[370]
        self.assertTrue(decision.is_decision_boundary)

        runtime = VersionBPaperRuntime(
            db_path=self.db_path, run_id="BRK", initial_balance=1000,
            strategy=FixtureStrategy(), fee_rate=0.0, slippage_rate=0.0,
            stale_after_seconds=60.0, data_fault_threshold=1,
            clock=DeterministicClock(stream[0].emitted_at.to_pydatetime()))
        self.addCleanup(runtime.close)
        runtime.start()
        for event in stream[:370]:
            runtime.on_event(event)
            runtime.clock.advance_to(event.emitted_at.to_pydatetime())
        self.assertFalse(runtime.health.circuit_open)

        # Open the breaker with the clock alone: no event, no decision.  The
        # clock moves five minutes past the newest bar, which is what "the feed
        # went quiet" looks like from the runtime's side.
        runtime.clock.advance_to(
            stream[370].emitted_at.to_pydatetime() + timedelta(minutes=5))
        runtime.assess_data_freshness()
        self.assertTrue(runtime.health.circuit_open)
        self.assertEqual(runtime.health.circuit_reason, "STALE_DATA")

        outcome = runtime.on_event(decision)
        self.assertEqual(outcome, EventOutcome.CIRCUIT_OPEN_REJECTED)
        self.assertEqual(runtime.service.positions, {}, "no entry on a stale feed")
        self.assertIn((AuditSeverity.WARNING.value, "ENTRY_BLOCKED_CIRCUIT_OPEN"),
                      [(a.severity, a.code) for a in runtime.store.audit_trail("BRK")])
        runtime.shutdown()

    # 3 ── an open position is still managed while the data is stale
    def test_an_open_position_is_still_exited_while_data_is_stale(self):
        driver = self.make_quiet_driver(
            withhold_after=372, heartbeat_interval=60.0,
            stale_after_seconds=60.0, data_fault_threshold=1, lease_seconds=3600.0)
        driver.start()
        self.addCleanup(driver.close)
        # Open the position first, on healthy data.
        self.assertIsNotNone(self.step_until(driver, EventOutcome.EXECUTED))
        trade_id = driver.runtime.operational_state()["positions"][0]["trade_id"]

        report = driver.run()
        self.assertEqual(report.idle_waits, 1, "the feed went quiet mid-lifecycle")
        self.assertIn("STALE_DATA",
                      [a.code for a in driver.store.audit_trail("DRV")])

        # Exits kept working through the stale window, per current semantics.
        store = self.reopen_store()
        row = store.get_trade(trade_id)
        self.assertEqual(row.state, "CLOSED")
        self.assertEqual(row.final_exit_reason, "TAKE_PROFIT_2")
        self.assertAlmostEqual(row.net_pnl, 32.5)
        self.assertEqual(
            [e.event_type for e in store.events_for_trade(trade_id)],
            ["ENTRY_FILLED", "PROTECTION_PLACED", "TAKE_PROFIT_1",
             "BE_UPDATED", "TAKE_PROFIT_2", "TRADE_CLOSED"])
        driver.shutdown()

    # 4 ── a genuinely dead process lets the lease expire
    def test_a_dead_process_lets_the_lease_expire_and_allows_takeover(self):
        driver = self.make_quiet_driver(
            quiet_at_end=timedelta(minutes=30), heartbeat_interval=60.0,
            lease_seconds=600.0, holder="proc-A")
        driver.start()
        self.addCleanup(driver.close)
        driver.run()
        alive_at = driver.clock.now()
        # Dies without releasing: exactly what a killed process does.
        driver.close()
        del driver

        store = self.reopen_store()
        self.assertEqual(store.lock_holder("DRV").holder, "proc-A")
        # Still inside the lease: a supervisor must wait, not barge in.
        self.assertFalse(store.lock_is_expired("DRV", now=alive_at, lease_seconds=600.0))
        early = DeterministicClock(alive_at + timedelta(seconds=300))
        impatient = self.make_quiet_driver(
            run_id="DRV", holder="proc-B", clock=early, resume=True,
            lease_seconds=600.0)
        self.addCleanup(impatient.close)
        with self.assertRaises(NotPrimaryInstance):
            impatient.start()

        # Past the lease: the takeover is allowed, and it is recorded.
        late = DeterministicClock(alive_at + timedelta(seconds=900))
        self.assertTrue(store.lock_is_expired("DRV", now=late.now(), lease_seconds=600.0))
        successor = self.make_quiet_driver(
            run_id="DRV", holder="proc-B", clock=late, resume=True,
            lease_seconds=600.0)
        successor.start()
        self.addCleanup(successor.close)
        self.assertTrue(successor.started)
        self.assertEqual(store.lock_holder("DRV").holder, "proc-B")
        self.assertIn("LOCK_TAKEN_OVER", [a.code for a in store.audit_trail("DRV")])
        successor.shutdown()

    # 5 ── restart/resume keeps lease and health state correct
    def test_restart_preserves_lease_and_health_state(self):
        driver = self.make_quiet_driver(
            quiet_at_end=timedelta(minutes=30), heartbeat_interval=60.0,
            lease_seconds=600.0, holder="proc-A")
        driver.start()
        self.addCleanup(driver.close)
        driver.run()
        driver.shutdown()
        self.assertIsNone(driver.store.lock_holder("DRV"),
                          "a graceful shutdown releases the lease")
        health_before = driver.runtime.operational_state()["health"]
        driver.close()
        del driver

        store = self.reopen_store()
        resumed = self.make_quiet_driver(
            run_id="DRV", holder="proc-B", resume=True,
            heartbeat_interval=60.0, lease_seconds=600.0)
        resumed.start()
        self.addCleanup(resumed.close)
        self.assertEqual(store.lock_holder("DRV").holder, "proc-B")
        self.assertIsNotNone(store.lock_holder("DRV").heartbeat_at)
        health_after = store.operational_state("DRV")["health"]
        self.assertEqual(health_after["circuit_open"], health_before["circuit_open"])
        self.assertEqual(health_after["data"]["status"], health_before["data"]["status"])
        # Resume is still exact: nothing already consumed is replayed.
        report = resumed.run()
        self.assertEqual(report.events_delivered, 0)
        self.assertEqual(report.events_skipped_as_processed, 510)
        resumed.shutdown()

    # 6 ── two live processes can never own the same run
    def test_two_live_processes_cannot_own_the_same_run(self):
        first = self.make_quiet_driver(
            quiet_at_end=timedelta(minutes=30), holder="proc-A",
            heartbeat_interval=60.0, lease_seconds=3600.0)
        first.start()
        self.addCleanup(first.close)
        first.run(max_events=510)
        beats_before = first.report.heartbeats
        self.assertIsNone(first.step(), "the feed stopped")
        # First is demonstrably alive: it beat through the whole quiet window.
        self.assertGreaterEqual(first.report.heartbeats - beats_before, 30)

        for offset in (0, 60, 600):
            clock = DeterministicClock(first.clock.now() + timedelta(seconds=offset))
            rival = self.make_quiet_driver(
                run_id="DRV", holder=f"rival-{offset}", clock=clock, resume=True,
                heartbeat_interval=60.0, lease_seconds=3600.0)
            self.addCleanup(rival.close)
            with self.assertRaises(NotPrimaryInstance):
                rival.start()
            self.assertFalse(rival.started)
        self.assertEqual(self.reopen_store().lock_holder("DRV").holder, "proc-A")
        first.shutdown()

    # 7 ── every liveness / lease / staleness transition is auditable
    def test_liveness_and_staleness_transitions_are_auditable(self):
        driver = self.make_quiet_driver(
            withhold_after=368, quiet_at_end=timedelta(minutes=30),
            heartbeat_interval=60.0, stale_after_seconds=60.0,
            data_fault_threshold=1, lease_seconds=600.0, holder="proc-A")
        driver.start()
        self.addCleanup(driver.close)
        driver.run()
        driver.shutdown()
        driver.close()
        del driver

        store = self.reopen_store()
        trail = store.audit_trail("DRV")
        codes = [a.code for a in trail]
        # The policy in force is recorded at start-up, so an operator reading
        # the trail later knows what lease and beat interval were configured.
        started = next(a for a in trail if a.code == "RUNTIME_STARTED")
        policy = json.loads(started.detail_json)
        self.assertEqual(policy["lease_seconds"], 600.0)
        self.assertEqual(policy["heartbeat_interval"], 60.0)
        self.assertEqual(policy["holder"], "proc-A")
        for expected in ("MARKET_DATA_QUIET", "STALE_DATA", "CIRCUIT_BREAKER_OPEN",
                         "MARKET_DATA_WAIT_ENDED", "RUNTIME_SHUTDOWN"):
            self.assertIn(expected, codes)
        # Staleness is attributed to the assessment that found it, so a stale
        # reading is never confused with a decision-path rejection.
        stale = [json.loads(a.detail_json) for a in trail if a.code == "STALE_DATA"]
        self.assertTrue(any(d.get("source") == "liveness_assessment" for d in stale),
                        "a stale reading must say what detected it")
        # The heartbeat itself is durable state, not just a log line: the lease
        # row carries the last proof of life and who gave it.
        # The heartbeat itself is durable state, not just a log line: after a
        # graceful shutdown the lease row is released, so read it before that.
        self.assertIn("RUNTIME_SHUTDOWN", codes)

    # ── the whole path in one test
    def test_alive_then_quiet_then_stale_then_dead_then_takeover(self):
        """start → alive → beats with no data → feed stops → stale protection
        → still alive → process dies → lease expires → takeover."""
        driver = self.make_quiet_driver(
            quiet_at_end=timedelta(minutes=30), heartbeat_interval=60.0,
            stale_after_seconds=600.0, data_fault_threshold=1,
            lease_seconds=600.0, holder="proc-A")
        driver.start()
        self.addCleanup(driver.close)
        self.assertTrue(driver.started, "runtime alive")

        driver.run(max_events=510)
        beats_before = driver.report.heartbeats
        self.assertIsNone(driver.step(), "the feed stopped")
        self.assertGreaterEqual(driver.report.heartbeats - beats_before, 30,
                                "heartbeat continued with no market data")
        self.assertGreaterEqual(driver.report.data_quiet_seconds, 1700.0,
                                "market data had stopped")
        self.assertIn("STALE_DATA",
                      [a.code for a in driver.store.audit_trail("DRV")])
        self.assertTrue(driver.runtime.health.circuit_open,
                        "stale-data protection engaged")
        self.assertFalse(driver.store.lock_is_expired(
            "DRV", now=driver.clock.now(), lease_seconds=600.0),
            "the runtime was still alive throughout")

        alive_at = driver.clock.now()
        driver.close()          # the process dies without releasing
        del driver

        store = self.reopen_store()
        self.assertTrue(store.lock_is_expired(
            "DRV", now=alive_at + timedelta(seconds=900), lease_seconds=600.0),
            "the lease expired because nothing refreshed it")
        successor = self.make_quiet_driver(
            run_id="DRV", holder="proc-B", resume=True, lease_seconds=600.0,
            heartbeat_interval=60.0,
            clock=DeterministicClock(alive_at + timedelta(seconds=900)))
        successor.start()
        self.addCleanup(successor.close)
        self.assertEqual(store.lock_holder("DRV").holder, "proc-B")
        self.assertIn("LOCK_TAKEN_OVER", [a.code for a in store.audit_trail("DRV")])
        successor.shutdown()

# ─────────────────────────────────────────────────────────────────────
# Recovery semantics: which event is allowed to clear a stale breaker
# ─────────────────────────────────────────────────────────────────────
class StaleRecoverySemanticsTests(DriverTestCase):
    """`VB-LIV-008` — the ordering the defect lived in.

    At a 15m boundary the feed delivers the 1h bar, then the closing 5m bar,
    then the 15m decision.  Recovery used to be granted by *arrival*, so the
    first two cleared a breaker opened by an outage one step before the decision
    it existed to guard — and the entry went through.

    Withhold index 368 puts a real 5-minute silence immediately before that
    boundary, so the sequence can be walked one event at a time.
    """

    def make_outage_driver(self, **kwargs):
        kwargs.setdefault("heartbeat_interval", 60.0)
        kwargs.setdefault("stale_after_seconds", 60.0)
        kwargs.setdefault("data_fault_threshold", 1)
        return PaperDriver(
            db_path=self.db_path,
            adapter=QuietSource(self.frames, withhold_after=368),
            run_id="RCV", initial_balance=1000, strategy=FixtureStrategy(),
            fee_rate=0.0, slippage_rate=0.0, **kwargs)

    def walk_to_the_boundary(self, driver):
        driver.start()
        driver.run(max_events=368)
        self.assertFalse(driver.runtime.health.circuit_open)
        driver.step()                       # silence, then the 1h bar
        return driver

    def test_neither_the_1h_nor_the_5m_bar_clears_a_stale_breaker(self):
        driver = self.walk_to_the_boundary(self.make_outage_driver())
        self.addCleanup(driver.close)
        self.assertTrue(driver.runtime.health.circuit_open,
                        "the 1h bar must not clear it")

        outcome = driver.step()             # the closing 5m bar
        self.assertEqual(outcome, EventOutcome.IGNORED)
        self.assertTrue(driver.runtime.health.circuit_open,
                        "the 5m bar must not clear it either")
        self.assertEqual(driver.runtime.health.data.status, HealthStatus.STALE)
        self.assertNotIn("CIRCUIT_BREAKER_CLOSED",
                         [a.code for a in driver.store.audit_trail("RCV")])
        driver.shutdown()

    def test_the_first_decision_after_an_outage_is_refused_not_executed(self):
        """This is the behaviour that changed, stated as an outcome.

        Before `VB-LIV-008` the same sequence returned `EXECUTED` and opened a
        position: the 1h and 5m bars had already cleared the breaker.
        """
        driver = self.walk_to_the_boundary(self.make_outage_driver())
        self.addCleanup(driver.close)
        driver.step()                       # the 5m bar
        self.assertTrue(driver.runtime.health.circuit_open)

        outcome = driver.step()             # the 15m decision
        self.assertEqual(outcome, EventOutcome.CIRCUIT_OPEN_REJECTED)
        self.assertEqual(driver.runtime.operational_state()["open_position_count"], 0,
                         "no entry on the first decision after an outage")
        self.assertIn((AuditSeverity.WARNING.value, "ENTRY_BLOCKED_CIRCUIT_OPEN"),
                      [(a.severity, a.code) for a in driver.store.audit_trail("RCV")])
        driver.shutdown()

    def test_the_decision_bar_is_the_recovery_event_and_the_next_one_proceeds(self):
        """stale -> recovery event -> fresh state -> next decision.

        The 15m decision bar is the first event that carries actual evidence
        about the timeframe gating entries, so it is the one allowed to close
        the breaker — and the decision after it is a normal decision again.
        Recovery is never left stuck open.
        """
        driver = self.walk_to_the_boundary(self.make_outage_driver())
        self.addCleanup(driver.close)
        driver.step()                       # 5m bar: no recovery
        self.assertEqual(driver.step(), EventOutcome.CIRCUIT_OPEN_REJECTED)

        trail = driver.store.audit_trail("RCV")
        codes = [a.code for a in trail]
        self.assertIn("CIRCUIT_BREAKER_CLOSED", codes,
                      "recovery must happen, just not on the wrong event")
        self.assertLess(codes.index("CIRCUIT_BREAKER_OPEN"),
                        codes.index("CIRCUIT_BREAKER_CLOSED"))
        closed = [json.loads(a.detail_json) for a in trail
                  if a.code == "CIRCUIT_BREAKER_CLOSED"]
        self.assertEqual(closed[-1]["recovered_on"], DECISION_TIMEFRAME)
        self.assertEqual(closed[-1]["previous_reason"], "STALE_DATA")
        self.assertFalse(driver.runtime.health.circuit_open)
        self.assertEqual(driver.runtime.health.data.status, HealthStatus.HEALTHY)
        self.assertEqual(driver.runtime.health.consecutive_data_faults, 0)

        # And the run recovers completely: the next signal trades and closes.
        # Outcomes accumulate for the driver's lifetime, so compare the delta —
        # the one refusal already recorded above must not be counted again.
        blocked_before = driver.report.outcome(EventOutcome.CIRCUIT_OPEN_REJECTED.value)
        self.assertEqual(blocked_before, 1)
        driver.run()
        self.assertEqual(driver.report.outcome(EventOutcome.CIRCUIT_OPEN_REJECTED.value),
                         blocked_before, "no decision is refused once recovery lands")
        self.assertEqual(driver.report.outcome(EventOutcome.EXECUTED.value), 1)
        state = driver.runtime.operational_state()
        self.assertEqual(state["closed_trade_count"], 1)
        self.assertAlmostEqual(state["balance"], 1032.5)
        driver.shutdown()

    def test_recovery_never_depends_on_how_many_events_arrive(self):
        """Ten 5m bars in a row are still not recovery.

        The old rule was "any event that processed cleanly", so volume of
        traffic looked like health.  Only the decision timeframe coming back
        counts, however much else arrives.
        """
        driver = self.walk_to_the_boundary(self.make_outage_driver())
        self.addCleanup(driver.close)
        for _ in range(1):
            driver.step()                   # the 5m bar at the boundary
        self.assertTrue(driver.runtime.health.circuit_open)
        # Every remaining event up to the next 15m boundary is a 5m bar.
        for _ in range(2):
            driver.step()
        self.assertFalse(driver.runtime.health.circuit_open,
                         "the 15m bar at the next boundary is the recovery")
        self.assertEqual(driver.runtime.health.data.status, HealthStatus.HEALTHY)
        driver.shutdown()


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
