"""Clocks for the Version B Paper runtime.

Time is a dependency, not a global.  The runtime and its driver take a clock
instead of calling ``datetime.now()``, which is what makes an operational run
reproducible: every timestamp it writes — health, audit, lock heartbeat, state
snapshot — comes from the same source as the events it consumed.

``DeterministicClock`` is the one tests use.  It never reads the wall clock, and
it refuses to move backwards, so a test cannot accidentally produce an
audit trail that looks like it happened in an order it did not.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone

import pandas as pd


class ClockCannotRewind(ValueError):
    """A clock was asked to move backwards."""


class Clock(ABC):
    """The only source of "now" inside the operational path."""

    @abstractmethod
    def now(self) -> datetime:
        """Current time, timezone-aware UTC."""

    @abstractmethod
    def advance_to(self, when: datetime | pd.Timestamp) -> datetime:
        """Move the clock to ``when``.  Implementations may refuse."""


def _aware(value: datetime | pd.Timestamp) -> datetime:
    if isinstance(value, pd.Timestamp):
        value = value.to_pydatetime()
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


class WallClock(Clock):
    """Real time.  Cannot be commanded, because reality cannot be."""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def advance_to(self, when: datetime | pd.Timestamp) -> datetime:
        # Commanding real time is meaningless; report where it actually is.
        return self.now()


class DeterministicClock(Clock):
    """Time is whatever the last event said.  No wall clock is ever read."""

    def __init__(self, start: datetime | pd.Timestamp):
        self._now = _aware(start)
        self.advances = 0

    def now(self) -> datetime:
        return self._now

    def advance_to(self, when: datetime | pd.Timestamp) -> datetime:
        target = _aware(when)
        if target < self._now:
            raise ClockCannotRewind(
                f"clock cannot move backwards: {self._now.isoformat()} -> {target.isoformat()}"
            )
        if target > self._now:
            self._now = target
            self.advances += 1
        return self._now


__all__ = ["Clock", "ClockCannotRewind", "DeterministicClock", "WallClock"]
