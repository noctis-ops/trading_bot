"""The operational Paper driver: adapter → clock → runtime.

Three separate concerns, each replaceable on its own:

``MarketDataAdapter``
    Where events come from.  ``DeterministicMarketAdapter`` replays finished
    frames one bar at a time; a venue-backed adapter would implement the same
    three methods and nothing else would change.

``Clock``
    What time it is.  ``DeterministicClock`` is driven by the events themselves,
    so an operational run is reproducible down to its timestamps and no test
    depends on the wall clock.

``PaperDriver``
    What pulls them together.  It asks the adapter for one event, moves the
    clock to that event, hands it to the runtime, heartbeats the lease, and
    persists.  It never batches.

The runtime does not know which adapter or clock it has.  That is the point:
switching Paper from a deterministic feed to a live one is a constructor
argument, not a change to the decision, risk, execution, or persistence path.

No network is reachable from this module.  ``LiveMarketAdapter`` exists to mark
the boundary and refuses to be constructed until a real feed is authorised.

Known limitation, deliberate: a runtime owns **one symbol**.  Broadening that
means portfolio-level risk across concurrent positions — a design change, not a
loop — so multi-symbol stays out of this layer.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from core.version_b_clock import Clock, DeterministicClock, WallClock
from core.version_b_runtime import (
    DECISION_TIMEFRAME,
    EXIT_TIMEFRAME,
    DeterministicExchangeDouble,
    DeterministicMarketFeed,
    EventOutcome,
    MarketEvent,
    VersionBPaperRuntime,
)

TIMEFRAMES = ("1h", DECISION_TIMEFRAME, EXIT_TIMEFRAME)


# ── market data boundary ────────────────────────────────────────────────────


class MarketDataAdapter(ABC):
    """One source of market events, delivered one at a time in time order.

    An adapter must never hand over a window.  The whole operational contract
    rests on the engine seeing one closed bar at a time, at the moment it
    closed, so the interface has no method that could return a frame.
    """

    symbol: str = "BTC/USDT"

    @abstractmethod
    def open(self) -> None:
        """Prepare the source.  Idempotent."""

    @abstractmethod
    def next_event(self) -> MarketEvent | None:
        """The next event in time order, or None when the source is exhausted."""

    @abstractmethod
    def close(self) -> None:
        """Release the source."""

    def __enter__(self) -> "MarketDataAdapter":
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


class DeterministicMarketAdapter(MarketDataAdapter):
    """Replays finished frames as a live-shaped stream.

    The frames belong to the adapter; the runtime only ever sees the single
    event returned by ``next_event``.  Events are produced lazily so nothing
    downstream can observe the window.
    """

    def __init__(self, frames: Mapping[str, pd.DataFrame], *, symbol: str = "BTC/USDT"):
        missing = [name for name in TIMEFRAMES if name not in frames]
        if missing:
            raise ValueError(f"frames missing timeframes: {missing}")
        self.symbol = symbol
        self._feed = DeterministicMarketFeed(dict(frames), symbol=symbol)
        self._iterator = None
        self._opened = False
        self.delivered = 0

    @classmethod
    def from_directory(cls, directory: str | Path, *,
                       symbol: str = "BTC/USDT") -> "DeterministicMarketAdapter":
        """Load ``1h.csv`` / ``15m.csv`` / ``5m.csv`` — the supported offline source.

        Columns: ``timestamp,open,high,low,close`` and optional ``volume``.
        This is the boundary a real feed would replace; it reads local files and
        opens no socket.
        """
        root = Path(directory)
        frames: dict[str, pd.DataFrame] = {}
        for name in TIMEFRAMES:
            path = root / f"{name}.csv"
            if not path.exists():
                raise FileNotFoundError(f"missing frame file: {path}")
            frame = pd.read_csv(path)
            if "timestamp" not in frame.columns:
                raise ValueError(f"{path} must have a 'timestamp' column")
            frames[name] = frame
        return cls(frames, symbol=symbol)

    def open(self) -> None:
        if self._opened:
            return
        self._iterator = iter(self._feed)
        self._opened = True

    def next_event(self) -> MarketEvent | None:
        if not self._opened:
            raise RuntimeError("adapter is not open")
        event = next(self._iterator, None)
        if event is not None:
            self.delivered += 1
        return event

    def close(self) -> None:
        self._iterator = None
        self._opened = False


class LiveMarketAdapter(MarketDataAdapter):
    """The boundary a venue-backed feed would occupy.  Deliberately inert.

    Wiring this up means opening a socket to an exchange, which is out of scope
    for the deterministic phase.  It is kept as a named, importable type so the
    seam is explicit and so nothing can silently substitute a live source: an
    implementation has to be written, reviewed, and constructed on purpose.
    """

    def __init__(self, *args: Any, **kwargs: Any):
        raise NotImplementedError(
            "No live market data feed is authorised in this phase.  Implement "
            "MarketDataAdapter.open/next_event/close against the venue and "
            "construct that class instead; Paper currently runs on "
            "DeterministicMarketAdapter."
        )

    def open(self) -> None:  # pragma: no cover - construction is refused
        raise NotImplementedError

    def next_event(self) -> MarketEvent | None:  # pragma: no cover
        raise NotImplementedError

    def close(self) -> None:  # pragma: no cover
        raise NotImplementedError


# ── the driver ──────────────────────────────────────────────────────────────


@dataclass
class DriverReport:
    """What one driver run did.  Derived from the runtime, not from memory."""

    run_id: str = ""
    events_delivered: int = 0
    events_skipped_as_processed: int = 0
    resumed_after_event: str | None = None
    outcomes: dict[str, int] = field(default_factory=dict)
    stopped_reason: str = ""
    clock_start: datetime | None = None
    clock_end: datetime | None = None
    heartbeats: int = 0

    def outcome(self, name: str) -> int:
        return self.outcomes.get(name, 0)

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "events_delivered": self.events_delivered,
            "events_skipped_as_processed": self.events_skipped_as_processed,
            "resumed_after_event": self.resumed_after_event,
            "outcomes": dict(self.outcomes),
            "stopped_reason": self.stopped_reason,
            "clock_start": self.clock_start.isoformat() if self.clock_start else None,
            "clock_end": self.clock_end.isoformat() if self.clock_end else None,
            "heartbeats": self.heartbeats,
        }


class PaperDriver:
    """Drives one Paper run: adapter → clock → runtime, one event at a time.

    Nothing operational exists before :meth:`start` — no runtime, no lock, no
    store handle.  That ordering is the point: acquiring the single-instance
    lock is the first thing that happens, so two processes cannot both believe
    they own the account while they set up.
    """

    def __init__(
        self,
        *,
        db_path: str | Path,
        adapter: MarketDataAdapter,
        run_id: str | None = None,
        clock: Clock | None = None,
        strategy: Any | None = None,
        initial_balance: float = 10_000.0,
        fee_rate: float | None = None,
        slippage_rate: float | None = None,
        direction: str = "long",
        holder: str | None = None,
        exchange: DeterministicExchangeDouble | None = None,
        stale_after_seconds: float = 3600.0,
        data_fault_threshold: int = 2,
        lease_seconds: float | None = 600.0,
        heartbeat_every: int = 25,
        resume: bool = False,
    ):
        self.db_path = Path(db_path)
        self.adapter = adapter
        self.run_id = run_id
        self.heartbeat_every = max(1, int(heartbeat_every))
        self.lease_seconds = lease_seconds
        self.resume = resume
        # None means "seed a deterministic clock from the first event": an
        # offline run should not depend on when it happened to be started.
        self.clock = clock
        self._runtime_kwargs = dict(
            initial_balance=initial_balance, strategy=strategy, fee_rate=fee_rate,
            slippage_rate=slippage_rate, direction=direction, holder=holder,
            exchange=exchange, stale_after_seconds=stale_after_seconds,
            data_fault_threshold=data_fault_threshold,
        )
        self.runtime: VersionBPaperRuntime | None = None
        self.store = None
        self.report = DriverReport(run_id=run_id or "")
        self._pending: MarketEvent | None = None

    # ── lifecycle ─────────────────────────────────────────────────────────

    @property
    def started(self) -> bool:
        return self.runtime is not None and self.runtime.started

    def start(self, *, takeover: bool = False) -> None:
        """Open the source, build the runtime, and take the single-instance lock."""
        if self.runtime is not None:
            return
        self.adapter.open()
        if self.clock is None:
            first = self._next_unprocessed_event()
            if first is None:
                raise ValueError("market data source produced no events")
            self._pending = first
            self.clock = DeterministicClock(self._event_time(first))
        self.runtime = VersionBPaperRuntime(
            db_path=self.db_path,
            run_id=self.run_id,
            symbol=self.adapter.symbol,
            resume=self.resume,
            clock=self.clock,
            lease_seconds=self.lease_seconds,
            **self._runtime_kwargs,
        )
        self.store = self.runtime.store
        self.run_id = self.runtime.run_id
        self.report = DriverReport(run_id=self.run_id)
        self.runtime.start(takeover=takeover)
        last = self.store.last_processed_event(self.run_id)
        self.report.resumed_after_event = last.event_id if last else None
        self.report.clock_start = self.clock.now()

    def shutdown(self, *, graceful: bool = True) -> None:
        """Persist, release the lease, and close the adapter.

        The event position is already durable: every consumed event was recorded
        before the driver returned from it, so a restart resumes after it
        without the driver having to remember anything.
        """
        if self.runtime is None:
            self.adapter.close()
            return
        try:
            self.runtime.shutdown(graceful=graceful)
        finally:
            self.adapter.close()
        self.report.clock_end = self.clock.now()

    def close(self) -> None:
        if self.runtime is not None:
            self.runtime.close()

    # ── the loop ──────────────────────────────────────────────────────────

    def step(self) -> EventOutcome | None:
        """Deliver exactly one event.  Returns None when the source is done."""
        if not self.started:
            raise RuntimeError("driver is not started")
        event = self._take_event()
        if event is None:
            return None
        self.clock.advance_to(self._event_time(event))
        outcome = self.runtime.on_event(event)
        self.report.events_delivered += 1
        key = outcome.value
        self.report.outcomes[key] = self.report.outcomes.get(key, 0) + 1
        if self.report.events_delivered % self.heartbeat_every == 0:
            self.runtime.heartbeat()
            self.report.heartbeats += 1
        return outcome

    def run(
        self,
        *,
        max_events: int | None = None,
        until: datetime | pd.Timestamp | None = None,
    ) -> DriverReport:
        """Drive events until the source ends, a count is reached, or a time."""
        if not self.started:
            raise RuntimeError("driver is not started")
        delivered = 0
        while True:
            if max_events is not None and delivered >= max_events:
                self.report.stopped_reason = "max_events"
                break
            event = self._take_event()
            if event is None:
                self.report.stopped_reason = "source_exhausted"
                break
            if until is not None and self._event_time(event) > self._as_dt(until):
                # Not consumed: it belongs to the next run, so it is pushed back.
                self._pending = event
                self.report.stopped_reason = "until_reached"
                break
            self._deliver(event)
            delivered += 1
        self.report.clock_end = self.clock.now()
        return self.report

    # ── internals ─────────────────────────────────────────────────────────

    def _deliver(self, event: MarketEvent) -> EventOutcome:
        self.clock.advance_to(self._event_time(event))
        outcome = self.runtime.on_event(event)
        self.report.events_delivered += 1
        key = outcome.value
        self.report.outcomes[key] = self.report.outcomes.get(key, 0) + 1
        if self.report.events_delivered % self.heartbeat_every == 0:
            self.runtime.heartbeat()
            self.report.heartbeats += 1
        return outcome

    def _take_event(self) -> MarketEvent | None:
        """The next event to deliver, skipping ones a previous process consumed.

        This is what makes resume exact: the driver does not replay the window
        from the beginning, it walks forward to the first event the database has
        not seen.  Skipping is counted, never silent.
        """
        while True:
            if self._pending is not None:
                event, self._pending = self._pending, None
            else:
                event = self.adapter.next_event()
                if event is None:
                    return None
            if self.store is not None and self.store.is_event_processed(event.event_id):
                self.report.events_skipped_as_processed += 1
                continue
            return event

    def _next_unprocessed_event(self) -> MarketEvent | None:
        """Pre-store variant used only to seed the clock before the runtime exists."""
        if self._pending is not None:
            event, self._pending = self._pending, None
            return event
        return self.adapter.next_event()

    @staticmethod
    def _event_time(event: MarketEvent) -> datetime:
        value = event.emitted_at
        stamp = value.to_pydatetime() if isinstance(value, pd.Timestamp) else value
        return stamp if stamp.tzinfo is not None else stamp.replace(tzinfo=timezone.utc)

    @staticmethod
    def _as_dt(value: datetime | pd.Timestamp) -> datetime:
        stamp = value.to_pydatetime() if isinstance(value, pd.Timestamp) else value
        return stamp if stamp.tzinfo is not None else stamp.replace(tzinfo=timezone.utc)

    def __enter__(self) -> "PaperDriver":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.started:
            self.shutdown()
        self.close()


__all__ = [
    "DeterministicMarketAdapter",
    "DriverReport",
    "LiveMarketAdapter",
    "MarketDataAdapter",
    "PaperDriver",
    "TIMEFRAMES",
]
