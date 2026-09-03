"""Persistent-shaped trade lifecycle state machine for Version B."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from core.contracts import EventRecord
from core.risk_model import FillLeg, calculate_realized_net_pnl


class LifecycleState(str, Enum):
    CREATED = "CREATED"
    OPEN = "OPEN"
    PARTIALLY_CLOSED = "PARTIALLY_CLOSED"
    CLOSED = "CLOSED"


@dataclass
class TradeLifecycle:
    """One trade_id from entry through final close.

    Partial exits are events and fill legs on this object; they never create a
    second trade lifecycle.
    """

    trade_id: str
    symbol: str
    side: str
    state: LifecycleState = LifecycleState.CREATED
    events: list[EventRecord] = field(default_factory=list)
    fills: list[FillLeg] = field(default_factory=list)
    initial_quantity: float = 0.0
    remaining_quantity: float = 0.0
    _tp1_processed: bool = False

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)

    def _append_event(self, event_type: str, payload: dict[str, Any] | None = None, event_time: datetime | None = None) -> EventRecord:
        if self.state == LifecycleState.CLOSED:
            raise ValueError("cannot append an event to a closed lifecycle")
        event = EventRecord(
            event_id=f"{self.trade_id}:{len(self.events) + 1}:{event_type}",
            trade_id=self.trade_id,
            event_type=event_type,
            event_time=event_time or self._now(),
            sequence=len(self.events) + 1,
            payload=payload or {},
        )
        self.events.append(event)
        return event

    def record_entry_fill(self, fill: FillLeg, *, event_time: datetime | None = None) -> EventRecord:
        if fill.role != "entry":
            raise ValueError("record_entry_fill requires an entry FillLeg")
        if fill.side != self.side:
            raise ValueError("fill side does not match lifecycle side")
        if self.state == LifecycleState.CLOSED:
            raise ValueError("cannot add entry to a closed lifecycle")
        if fill.quantity <= 0:
            raise ValueError("entry quantity must be positive")
        self.fills.append(fill)
        self.initial_quantity += fill.quantity
        self.remaining_quantity += fill.quantity
        self.state = LifecycleState.OPEN
        return self._append_event(
            "ENTRY_FILLED",
            {"price": fill.price, "quantity": fill.quantity},
            event_time,
        )

    def record_management_event(
        self,
        event_type: str,
        payload: dict[str, Any] | None = None,
        *,
        event_time: datetime | None = None,
    ) -> EventRecord:
        if event_type not in {"PROTECTION_PLACED", "BE_UPDATED", "TRAILING_UPDATED", "REVERSAL_SIGNAL"}:
            raise ValueError(f"unsupported management event: {event_type}")
        return self._append_event(event_type, payload, event_time)

    def record_exit_fill(
        self,
        fill: FillLeg,
        event_type: str,
        *,
        event_time: datetime | None = None,
    ) -> EventRecord:
        if fill.role != "exit":
            raise ValueError("record_exit_fill requires an exit FillLeg")
        if fill.side != self.side:
            raise ValueError("fill side does not match lifecycle side")
        if self.state not in {LifecycleState.OPEN, LifecycleState.PARTIALLY_CLOSED}:
            raise ValueError("lifecycle is not open")
        if fill.quantity <= 0 or fill.quantity > self.remaining_quantity + 1e-12:
            raise ValueError("exit quantity must be positive and no greater than remaining quantity")
        if event_type == "TAKE_PROFIT_1":
            if self._tp1_processed:
                raise ValueError("TP1 has already been processed for this lifecycle")
            self._tp1_processed = True
        allowed = {
            "TAKE_PROFIT_1", "TAKE_PROFIT_2", "STOP_LOSS", "REVERSAL_EXIT",
            "EMERGENCY_EXIT", "END_OF_DATA", "MANUAL_EXIT",
        }
        if event_type not in allowed:
            raise ValueError(f"unsupported exit event: {event_type}")

        self.fills.append(fill)
        self.remaining_quantity = max(0.0, self.remaining_quantity - fill.quantity)
        event = self._append_event(
            event_type,
            {"price": fill.price, "quantity": fill.quantity, "remaining_quantity": self.remaining_quantity},
            event_time,
        )
        if self.remaining_quantity <= 1e-12:
            self.remaining_quantity = 0.0
            self.state = LifecycleState.CLOSED
            self.events.append(EventRecord(
                event_id=f"{self.trade_id}:{len(self.events) + 1}:TRADE_CLOSED",
                trade_id=self.trade_id,
                event_type="TRADE_CLOSED",
                event_time=event_time or self._now(),
                sequence=len(self.events) + 1,
                payload={"exit_reason": event_type},
            ))
        else:
            self.state = LifecycleState.PARTIALLY_CLOSED
        return event

    @classmethod
    def from_records(
        cls,
        *,
        trade_id: str,
        symbol: str,
        side: str,
        state: str,
        initial_quantity: float,
        remaining_quantity: float,
        events: list[EventRecord],
        fills: list[FillLeg],
    ) -> "TradeLifecycle":
        """Rebuild a lifecycle from durable rows after a restart.

        Reconstruction is validated rather than trusted: a durable state that
        does not describe a coherent lifecycle is refused instead of being
        quietly accepted as a live position.
        """
        if side not in ("long", "short"):
            raise ValueError(f"unsupported side: {side}")
        if initial_quantity <= 0:
            raise ValueError("initial_quantity must be positive")
        ordered = sorted(events, key=lambda event: event.sequence)
        for expected, event in enumerate(ordered, start=1):
            if event.sequence != expected:
                raise ValueError(
                    f"lifecycle event sequence gap in {trade_id}: expected {expected}, got {event.sequence}"
                )
            if event.event_id != f"{trade_id}:{event.sequence}:{event.event_type}":
                raise ValueError(f"lifecycle event identity mismatch in {trade_id}: {event.event_id}")
        exit_quantity = sum(fill.quantity for fill in fills if fill.role == "exit")
        expected_remaining = max(0.0, initial_quantity - exit_quantity)
        if abs(expected_remaining - remaining_quantity) > 1e-9:
            raise ValueError(
                f"lifecycle quantity mismatch in {trade_id}: "
                f"remaining={remaining_quantity}, fills imply {expected_remaining}"
            )
        if remaining_quantity <= 1e-12:
            expected_state = LifecycleState.CLOSED
        elif exit_quantity > 0:
            expected_state = LifecycleState.PARTIALLY_CLOSED
        else:
            expected_state = LifecycleState.OPEN
        if state != expected_state.value:
            raise ValueError(
                f"lifecycle state mismatch in {trade_id}: stored {state}, fills imply {expected_state.value}"
            )
        lifecycle = cls(trade_id=trade_id, symbol=symbol, side=side)
        lifecycle.events = ordered
        lifecycle.fills = list(fills)
        lifecycle.initial_quantity = float(initial_quantity)
        lifecycle.remaining_quantity = float(remaining_quantity)
        lifecycle.state = expected_state
        lifecycle._tp1_processed = any(event.event_type == "TAKE_PROFIT_1" for event in ordered)
        return lifecycle

    @property
    def tp1_processed(self) -> bool:
        return self._tp1_processed

    @property
    def realized_net_pnl(self) -> float:
        if self.state != LifecycleState.CLOSED:
            raise ValueError("net PnL is final only after lifecycle close")
        return calculate_realized_net_pnl(self.fills)

    def to_dict(self) -> dict[str, Any]:
        return {
            "trade_id": self.trade_id,
            "symbol": self.symbol,
            "side": self.side,
            "state": self.state.value,
            "initial_quantity": self.initial_quantity,
            "remaining_quantity": self.remaining_quantity,
            "tp1_processed": self.tp1_processed,
            "event_ids": [event.event_id for event in self.events],
            "events": [event.to_dict() for event in self.events],
        }
