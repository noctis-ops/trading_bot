"""External (exchange) execution + restart recovery contract for Version B.

This module owns the path::

    Order Intent -> Execution Service -> External Adapter -> VersionBStore -> TradeLifecycle

It never imports an exchange client and never decides entries.  See
``VERSION_B_EXTERNAL_EXECUTION.md`` for the full contract, including the parts
that still require real exchange evidence.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import json
from typing import Any, Callable, Mapping

from core.trade_lifecycle import LifecycleState, TradeLifecycle
from core.contracts import EventRecord
from core.risk_model import FillLeg, calculate_realized_net_pnl


PURPOSE_ENTRY = "ENTRY"
PURPOSE_STOP_LOSS = "STOP_LOSS"
PURPOSE_TAKE_PROFIT_1 = "TAKE_PROFIT_1"
PURPOSE_TAKE_PROFIT_2 = "TAKE_PROFIT_2"
PURPOSE_EMERGENCY_CLOSE = "EMERGENCY_CLOSE"

PROTECTION_PURPOSES = (PURPOSE_STOP_LOSS, PURPOSE_TAKE_PROFIT_1, PURPOSE_TAKE_PROFIT_2)
CRITICAL_PROTECTION_PURPOSES = (PURPOSE_STOP_LOSS,)

_ACCEPTING_INTENT_STATUSES = ("ACCEPTED", "PARTIALLY_FILLED", "FILLED")

# A durable fill implies a lifecycle event.  Recovery uses this to rebuild an
# event that a crash dropped between the fill write and the event write.
_INTENT_EVENT_TYPES = {
    PURPOSE_ENTRY: "ENTRY_FILLED",
    PURPOSE_STOP_LOSS: "STOP_LOSS",
    PURPOSE_TAKE_PROFIT_1: "TAKE_PROFIT_1",
    PURPOSE_TAKE_PROFIT_2: "TAKE_PROFIT_2",
    PURPOSE_EMERGENCY_CLOSE: "EMERGENCY_EXIT",
}


class ExchangeOrderStatus(str, Enum):
    """Normalized exchange order state as reported by an adapter."""

    NEW = "NEW"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCELED = "CANCELED"
    NOT_FOUND = "NOT_FOUND"
    UNKNOWN = "UNKNOWN"


class IntentStatus(str, Enum):
    CREATED = "CREATED"
    SUBMITTED = "SUBMITTED"
    ACCEPTED = "ACCEPTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCELED = "CANCELED"
    UNKNOWN = "UNKNOWN"

    @property
    def unresolved(self) -> bool:
        return self.value in {"CREATED", "SUBMITTED", "UNKNOWN"}


# `NOT_FOUND` and `UNKNOWN` deliberately map to None: absence of evidence is
# never converted into a rejection or a success.
_ACK_TO_INTENT: dict[ExchangeOrderStatus, IntentStatus | None] = {
    ExchangeOrderStatus.NEW: IntentStatus.ACCEPTED,
    ExchangeOrderStatus.PARTIALLY_FILLED: IntentStatus.PARTIALLY_FILLED,
    ExchangeOrderStatus.FILLED: IntentStatus.FILLED,
    ExchangeOrderStatus.REJECTED: IntentStatus.REJECTED,
    ExchangeOrderStatus.CANCELED: IntentStatus.CANCELED,
    ExchangeOrderStatus.NOT_FOUND: None,
    ExchangeOrderStatus.UNKNOWN: None,
}


class ExternalExecutionError(RuntimeError):
    """Base class for external execution failures."""


class LostResponse(ExternalExecutionError):
    """The request may or may not have reached the exchange."""


class UnresolvedOrderState(ExternalExecutionError):
    """Exchange state is unknown and could not be reconciled.  Never success."""


class OrderRejected(ExternalExecutionError):
    """The exchange explicitly rejected the order."""


class ProtectionNotConfirmed(ExternalExecutionError):
    """No position may be accepted without confirmed protection."""


@dataclass(frozen=True)
class OrderIntent:
    """The durable, write-ahead identity of one intended exchange order."""

    order_intent_id: str
    client_order_id: str
    trade_id: str
    symbol: str
    side: str
    order_type: str
    purpose: str
    intended_quantity: float
    intended_price: float | None = None


@dataclass(frozen=True)
class Acknowledgement:
    """A normalized adapter reply.  Adapters must not fabricate these."""

    status: ExchangeOrderStatus
    exchange_order_id: str | None = None
    filled_quantity: float = 0.0
    average_fill_price: float | None = None
    fee: float | None = None
    raw: Mapping[str, Any] = field(default_factory=dict)

    @property
    def resolved(self) -> bool:
        return _ACK_TO_INTENT[self.status] is not None


class ExternalOrderAdapter(ABC):
    """The only surface this module uses to reach an exchange."""

    @abstractmethod
    def submit(self, intent: OrderIntent) -> Acknowledgement:
        """Send the order.  Raise ``LostResponse`` if the outcome is unknown."""

    @abstractmethod
    def fetch(self, intent: OrderIntent) -> Acknowledgement:
        """Query the order.  Raise ``LostResponse`` if the query itself failed."""

    def fetch_open_orders(self, symbol: str) -> list[Acknowledgement]:
        return []

    def fetch_positions(self, symbol: str) -> list[Mapping[str, Any]]:
        return []


@dataclass
class SubmissionResult:
    intent: OrderIntent
    status: IntentStatus
    filled_quantity: float
    average_fill_price: float | None
    exchange_order_id: str | None
    fill_id: str | None
    attempts: int

    @property
    def resolved(self) -> bool:
        return not self.status.unresolved


@dataclass
class EntryResult:
    trade_id: str
    intent: OrderIntent
    status: IntentStatus
    filled_quantity: float
    average_fill_price: float
    fee: float
    event_id: str


@dataclass
class ProtectionResult:
    confirmed: bool
    levels: dict[str, float]
    statuses: dict[str, str]
    missing: list[str]
    event_id: str | None


@dataclass
class RecoveryReport:
    run_id: str | None
    resolved: list[dict[str, Any]] = field(default_factory=list)
    unresolved: list[dict[str, Any]] = field(default_factory=list)
    hydrated_trades: list[str] = field(default_factory=list)
    protection_unconfirmed: list[str] = field(default_factory=list)
    hydration_failures: list[dict[str, Any]] = field(default_factory=list)
    observed_offline_fills: list[str] = field(default_factory=list)
    pending_management: list[str] = field(default_factory=list)
    duplicate_fills_prevented: int = 0
    duplicate_events_prevented: int = 0

    @property
    def is_clean(self) -> bool:
        """A report is clean only when nothing is left unknown or unprotected."""
        return not self.unresolved and not self.protection_unconfirmed and not self.hydration_failures


class ExternalExecutionService:
    """Durable intent -> adapter -> store -> lifecycle orchestration.

    Every outbound order is preceded by a durable intent row.  Retries reuse the
    same identity, so a retry is never a second order.  Unknown exchange state
    is preserved as unknown; it is never reported as success.
    """

    def __init__(
        self,
        *,
        adapter: ExternalOrderAdapter,
        store: Any,
        run_id: str,
        fee_rate: float = 0.0004,
        max_submit_attempts: int = 3,
        now: Callable[[], datetime] | None = None,
    ):
        if max_submit_attempts < 1:
            raise ValueError("max_submit_attempts must be positive")
        if fee_rate < 0:
            raise ValueError("fee_rate cannot be negative")
        self.adapter = adapter
        self.store = store
        self.run_id = run_id
        self.fee_rate = float(fee_rate)
        self.max_submit_attempts = int(max_submit_attempts)
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._suppressed_duplicate_fills = 0
        self._suppressed_duplicate_events = 0

    # ── identity ──────────────────────────────────────────────────────────
    def intent_id(self, trade_id: str, purpose: str) -> str:
        return f"{trade_id}:{purpose}"

    def client_order_id(self, trade_id: str, purpose: str) -> str:
        """Stable across retries: the exchange dedupes on this key."""
        return f"{self.run_id}-{trade_id}-{purpose}"

    # ── durable scaffolding ───────────────────────────────────────────────
    def begin_trade(
        self,
        *,
        trade_id: str,
        symbol: str,
        side: str,
        signal_id: str | None = None,
        leverage: float | None = None,
        event_time: datetime | None = None,
    ) -> None:
        if side not in ("long", "short"):
            raise ValueError(f"unsupported side: {side}")
        self.store.get_or_create_trade(
            trade_id=trade_id,
            run_id=self.run_id,
            signal_id=signal_id,
            symbol=symbol,
            side=side,
            state="CREATED",
            initial_quantity=0.0,
            remaining_quantity=0.0,
            opened_at=event_time,
        )
        if leverage is not None:
            self.store.update_trade(trade_id, leverage=float(leverage))

    def prepare_intent(
        self,
        *,
        trade_id: str,
        symbol: str,
        side: str,
        purpose: str,
        quantity: float,
        order_type: str,
        price: float | None = None,
    ) -> OrderIntent:
        """Write the intent row before any network call (write-ahead)."""
        if quantity <= 0:
            raise ValueError("intended quantity must be positive")
        intent = OrderIntent(
            order_intent_id=self.intent_id(trade_id, purpose),
            client_order_id=self.client_order_id(trade_id, purpose),
            trade_id=trade_id,
            symbol=symbol,
            side=side,
            order_type=order_type,
            purpose=purpose,
            intended_quantity=float(quantity),
            intended_price=price,
        )
        self.store.get_or_create_order_intent(
            order_intent_id=intent.order_intent_id,
            trade=trade_id,
            client_order_id=intent.client_order_id,
            exchange_order_id=None,
            symbol=symbol,
            side=side,
            order_type=order_type,
            intended_quantity=intent.intended_quantity,
            intended_price=price,
            status=IntentStatus.CREATED.value,
            created_at=self._now(),
            purpose=purpose,
            exchange_status=None,
            filled_quantity=0.0,
            average_fill_price=None,
            acknowledged_at=None,
            attempt_count=0,
            last_error=None,
        )
        return intent

    # ── acknowledgement handling ──────────────────────────────────────────
    def _record_acknowledged_fill(
        self,
        intent: OrderIntent,
        ack: Acknowledgement,
        *,
        role: str,
        side: str,
    ) -> str | None:
        """Record only the newly acknowledged quantity.  Never a duplicate."""
        recorded = self.store.fills_for_intent(intent.order_intent_id)
        recorded_quantity = sum(fill.quantity for fill in recorded)
        filled = float(ack.filled_quantity or 0.0)
        if filled <= recorded_quantity + 1e-12:
            if filled > 0:
                self._suppressed_duplicate_fills += 1
            return None
        delta = filled - recorded_quantity
        price = float(ack.average_fill_price or intent.intended_price or 0.0)
        if price <= 0:
            raise UnresolvedOrderState(
                f"{intent.order_intent_id}: acknowledged fill without a price"
            )
        fee = float(ack.fee) if ack.fee is not None else price * delta * self.fee_rate
        fill_id = f"{intent.order_intent_id}:fill:{len(recorded) + 1}"
        if self.store.get_fill(fill_id) is not None:
            self._suppressed_duplicate_fills += 1
            return fill_id
        self.store.record_fill(
            fill_id=fill_id,
            trade=intent.trade_id,
            order_intent=intent.order_intent_id,
            role=role,
            side=side,
            fill_time=self._now(),
            price=price,
            quantity=delta,
            fee=fee,
            slippage=0.0,
            funding=0.0,
        )
        return fill_id

    def _apply_acknowledgement(
        self,
        intent: OrderIntent,
        ack: Acknowledgement,
        *,
        role: str,
        lifecycle_side: str,
    ) -> tuple[IntentStatus, str | None]:
        record = self.store.get_order_intent(intent.order_intent_id)
        target = _ACK_TO_INTENT[ack.status]
        values: dict[str, Any] = {
            "exchange_status": ack.status.value,
            "acknowledged_at": self._now(),
            "last_error": None,
        }
        if ack.exchange_order_id:
            values["exchange_order_id"] = ack.exchange_order_id
        if target is not None:
            values["status"] = target.value
            if ack.filled_quantity:
                values["filled_quantity"] = float(ack.filled_quantity)
                values["average_fill_price"] = ack.average_fill_price
        self.store.update_order_intent(intent.order_intent_id, **values)
        status = IntentStatus(self.store.get_order_intent(intent.order_intent_id).status)
        fill_id = None
        if status.value in ("PARTIALLY_FILLED", "FILLED"):
            fill_id = self._record_acknowledged_fill(intent, ack, role=role, side=lifecycle_side)
        return status, fill_id

    def _append_lifecycle_event(
        self,
        trade_id: str,
        event_type: str,
        payload: Mapping[str, Any] | None = None,
        *,
        event_time: datetime | None = None,
    ) -> tuple[str, bool]:
        existing = self.store.find_event(trade_id, event_type)
        if existing is not None:
            self._suppressed_duplicate_events += 1
            return existing.event_id, False
        sequence = self.store.max_event_sequence(trade_id) + 1
        event_id = f"{trade_id}:{sequence}:{event_type}"
        self.store.append_event(
            event_id=event_id,
            trade_id=trade_id,
            sequence=sequence,
            event_type=event_type,
            event_time=event_time or self._now(),
            payload=dict(payload or {}),
        )
        return event_id, True

    # ── submission ────────────────────────────────────────────────────────
    def _result(self, intent: OrderIntent, status: IntentStatus, fill_id: str | None, attempts: int) -> SubmissionResult:
        record = self.store.get_order_intent(intent.order_intent_id)
        return SubmissionResult(
            intent=intent,
            status=status,
            filled_quantity=float(record.filled_quantity or 0.0),
            average_fill_price=record.average_fill_price,
            exchange_order_id=record.exchange_order_id,
            fill_id=fill_id,
            attempts=attempts,
        )

    def _downgrade_if_invisible(self, intent: OrderIntent, ack: Acknowledgement) -> None:
        """A resting order that can no longer be seen is unknown, not accepted.

        ``NOT_FOUND`` after acceptance is evidence loss, not a cancellation, so
        the durable state moves to ``UNKNOWN`` and must be reconciled again.
        """
        if ack.resolved:
            return
        record = self.store.get_order_intent(intent.order_intent_id)
        if record is not None and record.status in _ACCEPTING_INTENT_STATUSES:
            self.store.update_order_intent(
                intent.order_intent_id,
                status=IntentStatus.UNKNOWN.value,
                last_error=f"order not visible to confirmation query: {ack.status.value}",
            )

    def _try_reconcile(
        self,
        intent: OrderIntent,
        *,
        role: str,
        lifecycle_side: str,
    ) -> SubmissionResult | None:
        """Resolve an intent by query.  Returns None when still unknown."""
        try:
            ack = self.adapter.fetch(intent)
        except LostResponse:
            return None
        status, fill_id = self._apply_acknowledgement(intent, ack, role=role, lifecycle_side=lifecycle_side)
        if status.unresolved:
            return None
        return self._result(intent, status, fill_id, attempts=0)

    def submit(
        self,
        intent: OrderIntent,
        *,
        role: str,
        lifecycle_side: str,
        max_attempts: int | None = None,
    ) -> SubmissionResult:
        """Submit with retry on the same identity, resolving lost responses.

        An intent that is already resolved is never resubmitted.  An intent in
        ``UNKNOWN`` is never resubmitted either: it must be reconciled first.
        """
        record = self.store.get_order_intent(intent.order_intent_id)
        status = IntentStatus(record.status)
        if not status.unresolved:
            return self._result(intent, status, None, attempts=0)
        if status == IntentStatus.UNKNOWN:
            reconciled = self._try_reconcile(intent, role=role, lifecycle_side=lifecycle_side)
            if reconciled is None:
                raise UnresolvedOrderState(
                    f"{intent.order_intent_id} is UNKNOWN and reconciliation failed; "
                    "it will not be resubmitted"
                )
            return reconciled

        limit = max_attempts or self.max_submit_attempts
        last_error = "no attempt made"
        for attempt in range(1, limit + 1):
            self.store.update_order_intent(
                intent.order_intent_id,
                status=IntentStatus.SUBMITTED.value,
                attempt_count=(record.attempt_count or 0) + attempt,
            )
            try:
                ack = self.adapter.submit(intent)
            except LostResponse as exc:
                last_error = f"lost response on attempt {attempt}: {exc}"
                self.store.update_order_intent(intent.order_intent_id, last_error=last_error)
                reconciled = self._try_reconcile(intent, role=role, lifecycle_side=lifecycle_side)
                if reconciled is not None:
                    return reconciled
                continue
            status, fill_id = self._apply_acknowledgement(
                intent, ack, role=role, lifecycle_side=lifecycle_side
            )
            if status.unresolved:
                last_error = f"unresolved acknowledgement on attempt {attempt}: {ack.status.value}"
                self.store.update_order_intent(intent.order_intent_id, last_error=last_error)
                continue
            return self._result(intent, status, fill_id, attempts=attempt)

        self.store.update_order_intent(
            intent.order_intent_id,
            status=IntentStatus.UNKNOWN.value,
            last_error=last_error,
        )
        raise UnresolvedOrderState(
            f"{intent.order_intent_id} unresolved after {limit} attempts: {last_error}"
        )

    # ── entry ─────────────────────────────────────────────────────────────
    def open_entry(
        self,
        *,
        trade_id: str,
        symbol: str,
        side: str,
        quantity: float,
        order_type: str = "MARKET",
        price: float | None = None,
        signal_id: str | None = None,
        leverage: float | None = None,
        event_time: datetime | None = None,
    ) -> EntryResult:
        self.begin_trade(
            trade_id=trade_id,
            symbol=symbol,
            side=side,
            signal_id=signal_id,
            leverage=leverage,
            event_time=event_time,
        )
        intent = self.prepare_intent(
            trade_id=trade_id,
            symbol=symbol,
            side="buy" if side == "long" else "sell",
            purpose=PURPOSE_ENTRY,
            quantity=quantity,
            order_type=order_type,
            price=price,
        )
        result = self.submit(intent, role="entry", lifecycle_side=side)
        if result.status == IntentStatus.REJECTED:
            raise OrderRejected(f"entry order rejected: {intent.order_intent_id}")
        if result.status == IntentStatus.CANCELED:
            raise ExternalExecutionError(f"entry order canceled: {intent.order_intent_id}")
        if result.status.unresolved:
            raise UnresolvedOrderState(f"entry order unresolved: {intent.order_intent_id}")
        if result.filled_quantity <= 0 or result.average_fill_price is None:
            raise UnresolvedOrderState(
                f"entry order {intent.order_intent_id} resolved without a usable fill"
            )

        filled = float(result.filled_quantity)
        self.store.update_trade(
            trade_id,
            state=LifecycleState.OPEN.value,
            initial_quantity=filled,
            remaining_quantity=filled,
            opened_at=event_time or self._now(),
        )
        event_id, _ = self._append_lifecycle_event(
            trade_id,
            "ENTRY_FILLED",
            {
                "price": result.average_fill_price,
                "quantity": filled,
                "order_intent_id": intent.order_intent_id,
                "intended_quantity": intent.intended_quantity,
                "exchange_order_id": result.exchange_order_id,
            },
            event_time=event_time,
        )
        fill_rows = self.store.fills_for_intent(intent.order_intent_id)
        fee = sum(fill.fee for fill in fill_rows)
        return EntryResult(
            trade_id=trade_id,
            intent=intent,
            status=result.status,
            filled_quantity=filled,
            average_fill_price=float(result.average_fill_price),
            fee=fee,
            event_id=event_id,
        )

    # ── protection ────────────────────────────────────────────────────────
    @staticmethod
    def _closing_side(side: str) -> str:
        return "sell" if side == "long" else "buy"

    def place_protection(
        self,
        *,
        trade_id: str,
        symbol: str,
        side: str,
        filled_quantity: float,
        stop_loss: float,
        take_profit_1: float,
        take_profit_2: float,
        order_type: str = "STOP_MARKET",
    ) -> dict[str, SubmissionResult]:
        """Size protective orders from the acknowledged filled quantity."""
        if filled_quantity <= 0:
            raise ValueError("protection requires a filled quantity")
        closing_side = self._closing_side(side)
        tp1_quantity = filled_quantity * 0.5
        tp2_quantity = filled_quantity - tp1_quantity
        plan = {
            PURPOSE_STOP_LOSS: (stop_loss, filled_quantity),
            PURPOSE_TAKE_PROFIT_1: (take_profit_1, tp1_quantity),
            PURPOSE_TAKE_PROFIT_2: (take_profit_2, tp2_quantity),
        }
        results: dict[str, SubmissionResult] = {}
        for purpose, (price, quantity) in plan.items():
            intent = self.prepare_intent(
                trade_id=trade_id,
                symbol=symbol,
                side=closing_side,
                purpose=purpose,
                quantity=quantity,
                order_type=order_type,
                price=price,
            )
            try:
                results[purpose] = self.submit(intent, role="exit", lifecycle_side=side)
            except (UnresolvedOrderState, OrderRejected):
                # A failed protective order is recorded, not swallowed: the
                # durable intent keeps its UNKNOWN/REJECTED state and the
                # caller sees it through ProtectionResult.missing.
                results[purpose] = self._result(intent, IntentStatus.UNKNOWN, None, attempts=0)
        return results

    def confirm_protection(
        self,
        *,
        trade_id: str,
        symbol: str,
        side: str,
        filled_quantity: float,
        stop_loss: float,
        take_profit_1: float,
        take_profit_2: float,
        event_time: datetime | None = None,
    ) -> ProtectionResult:
        """Require a fetch/ack for every protective intent, not a create reply."""
        levels = {
            PURPOSE_STOP_LOSS: stop_loss,
            PURPOSE_TAKE_PROFIT_1: take_profit_1,
            PURPOSE_TAKE_PROFIT_2: take_profit_2,
        }
        results = self.place_protection(
            trade_id=trade_id,
            symbol=symbol,
            side=side,
            filled_quantity=filled_quantity,
            stop_loss=stop_loss,
            take_profit_1=take_profit_1,
            take_profit_2=take_profit_2,
        )
        statuses: dict[str, str] = {}
        missing: list[str] = []
        for purpose in PROTECTION_PURPOSES:
            intent_id = self.intent_id(trade_id, purpose)
            record = self.store.get_order_intent(intent_id)
            intent = self._intent_from_record(record) if record is not None else OrderIntent(
                order_intent_id=intent_id,
                client_order_id=self.client_order_id(trade_id, purpose),
                trade_id=trade_id,
                symbol=symbol,
                side=self._closing_side(side),
                order_type="STOP_MARKET",
                purpose=purpose,
                intended_quantity=filled_quantity,
                intended_price=levels[purpose],
            )
            if record is None or IntentStatus(record.status).unresolved:
                resolved = self._try_reconcile(intent, role="exit", lifecycle_side=side)
                status = resolved.status.value if resolved is not None else (
                    record.status if record is not None else "MISSING"
                )
            else:
                # Confirmation is a query, never the create response.
                try:
                    ack = self.adapter.fetch(intent)
                except LostResponse:
                    self._downgrade_if_invisible(
                        intent, Acknowledgement(status=ExchangeOrderStatus.UNKNOWN)
                    )
                    status = IntentStatus.UNKNOWN.value
                else:
                    applied, _ = self._apply_acknowledgement(
                        intent, ack, role="exit", lifecycle_side=side
                    )
                    self._downgrade_if_invisible(intent, ack)
                    # NOT_FOUND / UNKNOWN never confirms protection.
                    status = applied.value if ack.resolved else IntentStatus.UNKNOWN.value
            statuses[purpose] = status
            if status not in _ACCEPTING_INTENT_STATUSES:
                missing.append(purpose)

        confirmed = not missing
        event_id = None
        if confirmed:
            event_id, _ = self._append_lifecycle_event(
                trade_id,
                "PROTECTION_PLACED",
                {
                    "stop_loss": stop_loss,
                    "take_profit_1": take_profit_1,
                    "take_profit_2": take_profit_2,
                    "confirmed_by": "fetch/ack",
                },
                event_time=event_time,
            )
        return ProtectionResult(
            confirmed=confirmed,
            levels=levels,
            statuses=statuses,
            missing=missing,
            event_id=event_id,
        )

    # ── emergency handling ────────────────────────────────────────────────
    def emergency_flatten(
        self,
        *,
        trade_id: str,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        reason: str = "PROTECTION_UNCONFIRMED",
        event_time: datetime | None = None,
    ) -> SubmissionResult:
        """Flatten a filled-but-unprotected position.  Never claims success blindly."""
        intent = self.prepare_intent(
            trade_id=trade_id,
            symbol=symbol,
            side=self._closing_side(side),
            purpose=PURPOSE_EMERGENCY_CLOSE,
            quantity=quantity,
            order_type="MARKET",
            price=price,
        )
        result = self.submit(intent, role="exit", lifecycle_side=side)
        if result.status.unresolved:
            raise UnresolvedOrderState(
                f"emergency close unresolved for {trade_id}; position may still be open and unprotected"
            )
        if result.status == IntentStatus.REJECTED:
            raise OrderRejected(f"emergency close rejected for {trade_id}")
        filled = float(result.filled_quantity)
        fills = self.store.fills_for_trade(trade_id)
        entry_quantity = sum(fill.quantity for fill in fills if fill.role == "entry")
        remaining = max(0.0, entry_quantity - sum(fill.quantity for fill in fills if fill.role == "exit"))
        values: dict[str, Any] = {"remaining_quantity": remaining}
        if remaining <= 1e-12:
            values.update({
                "state": LifecycleState.CLOSED.value,
                "closed_at": event_time or self._now(),
                "final_exit_reason": "EMERGENCY_EXIT",
            })
        else:
            values["state"] = LifecycleState.PARTIALLY_CLOSED.value
        self.store.update_trade(trade_id, **values)
        self._append_lifecycle_event(
            trade_id,
            "EMERGENCY_EXIT",
            {"price": price, "quantity": filled, "reason": reason},
            event_time=event_time,
        )
        if remaining <= 1e-12:
            self._append_lifecycle_event(
                trade_id,
                "TRADE_CLOSED",
                {"exit_reason": "EMERGENCY_EXIT"},
                event_time=event_time,
            )
        return result

    def open_position_with_protection(
        self,
        *,
        trade_id: str,
        symbol: str,
        side: str,
        quantity: float,
        stop_loss: float,
        take_profit_1: float,
        take_profit_2: float,
        leverage: float,
        order_type: str = "MARKET",
        price: float | None = None,
        signal_id: str | None = None,
        event_time: datetime | None = None,
    ) -> tuple[EntryResult, ProtectionResult]:
        """Entry is accepted only with confirmed protection, else flatten and fail."""
        entry = self.open_entry(
            trade_id=trade_id,
            symbol=symbol,
            side=side,
            quantity=quantity,
            order_type=order_type,
            price=price,
            signal_id=signal_id,
            leverage=leverage,
            event_time=event_time,
        )
        protection = self.confirm_protection(
            trade_id=trade_id,
            symbol=symbol,
            side=side,
            filled_quantity=entry.filled_quantity,
            stop_loss=stop_loss,
            take_profit_1=take_profit_1,
            take_profit_2=take_profit_2,
            event_time=event_time,
        )
        if not protection.confirmed:
            self.emergency_flatten(
                trade_id=trade_id,
                symbol=symbol,
                side=side,
                quantity=entry.filled_quantity,
                price=entry.average_fill_price,
                reason=f"protection unconfirmed: {','.join(protection.missing)}",
                event_time=event_time,
            )
            raise ProtectionNotConfirmed(
                f"{trade_id}: protection not confirmed for {protection.missing}; "
                f"statuses={protection.statuses}"
            )
        return entry, protection

    # ── restart recovery ──────────────────────────────────────────────────
    def _intent_from_record(self, record: Any) -> OrderIntent:
        return OrderIntent(
            order_intent_id=record.order_intent_id,
            client_order_id=record.client_order_id,
            trade_id=record.trade_id,
            symbol=record.symbol,
            side=record.side,
            order_type=record.order_type,
            purpose=record.purpose or PURPOSE_ENTRY,
            intended_quantity=float(record.intended_quantity or 0.0),
            intended_price=record.intended_price,
        )

    def reconstruct_lifecycle(self, trade_id: str) -> TradeLifecycle:
        """Rebuild the in-memory lifecycle for one trade from durable rows."""
        trade = self.store.get_trade(trade_id)
        if trade is None:
            raise KeyError(trade_id)
        events = [
            EventRecord(
                event_id=row.event_id,
                trade_id=trade_id,
                event_type=row.event_type,
                event_time=row.event_time.replace(tzinfo=timezone.utc) if row.event_time.tzinfo is None else row.event_time,
                sequence=row.sequence,
                payload={},
            )
            for row in self.store.events_for_trade(trade_id)
        ]
        fills = [
            FillLeg(
                role=row.role,
                side=row.side,
                price=row.price,
                quantity=row.quantity,
                fee=row.fee,
                slippage=row.slippage,
            )
            for row in self.store.fills_for_trade(trade_id)
        ]
        return TradeLifecycle.from_records(
            trade_id=trade_id,
            symbol=trade.symbol,
            side=trade.side,
            state=trade.state,
            initial_quantity=float(trade.initial_quantity or 0.0),
            remaining_quantity=float(trade.remaining_quantity or 0.0),
            events=events,
            fills=fills,
        )

    def _protection_levels(self, trade_id: str) -> dict[str, float]:
        protection = self.store.find_event(trade_id, "PROTECTION_PLACED")
        levels: dict[str, float] = {}
        if protection is not None:
            payload = json.loads(protection.payload_json)
            for key in ("stop_loss", "take_profit_1", "take_profit_2"):
                if payload.get(key) is not None:
                    levels[key] = float(payload[key])
        for record in self.store.order_intents_for_trade(trade_id):
            key = {
                PURPOSE_STOP_LOSS: "stop_loss",
                PURPOSE_TAKE_PROFIT_1: "take_profit_1",
                PURPOSE_TAKE_PROFIT_2: "take_profit_2",
            }.get(record.purpose or "")
            if key and key not in levels and record.intended_price is not None:
                levels[key] = float(record.intended_price)
        return levels

    def _protection_is_confirmed(self, trade_id: str) -> bool:
        for purpose in PROTECTION_PURPOSES:
            record = self.store.get_order_intent(self.intent_id(trade_id, purpose))
            if record is None or record.status not in _ACCEPTING_INTENT_STATUSES:
                return False
        return True

    def _reconcile_trade_row(self, trade_id: str) -> Any:
        """Recompute trade aggregates from durable fills after a crash.

        A crash can land between an acknowledged fill and its lifecycle event.
        Recovery derives the missing event from the durable fill and recomputes
        quantity/state from fills, so the reconstructed lifecycle is coherent
        instead of merely plausible.
        """
        trade = self.store.get_trade(trade_id)
        if trade is None:
            return None
        fills = self.store.fills_for_trade(trade_id)
        if not fills:
            return trade
        for fill in fills:
            intent = self.store.get_order_intent(fill.order_intent_id) if fill.order_intent_id else None
            if intent is None:
                continue
            event_type = _INTENT_EVENT_TYPES.get(intent.purpose or "")
            if event_type is None or self.store.find_event(trade_id, event_type) is not None:
                continue
            self._append_lifecycle_event(
                trade_id,
                event_type,
                {
                    "price": fill.price,
                    "quantity": fill.quantity,
                    "order_intent_id": intent.order_intent_id,
                },
                event_time=fill.fill_time,
            )
        entry_quantity = sum(fill.quantity for fill in fills if fill.role == "entry")
        exit_quantity = sum(fill.quantity for fill in fills if fill.role == "exit")
        remaining = max(0.0, entry_quantity - exit_quantity)
        values: dict[str, Any] = {}
        if entry_quantity > 0 and float(trade.initial_quantity or 0.0) <= 0:
            values["initial_quantity"] = entry_quantity
            if trade.opened_at is None:
                values["opened_at"] = fills[0].fill_time
        if abs(float(trade.remaining_quantity or 0.0) - remaining) > 1e-9:
            values["remaining_quantity"] = remaining
        if entry_quantity > 0:
            expected_state = (
                LifecycleState.CLOSED.value if remaining <= 1e-12
                else (LifecycleState.PARTIALLY_CLOSED.value if exit_quantity > 0 else LifecycleState.OPEN.value)
            )
            if trade.state != expected_state:
                values["state"] = expected_state
            if expected_state == LifecycleState.CLOSED.value and trade.closed_at is None:
                values["closed_at"] = fills[-1].fill_time
                values["final_exit_reason"] = values.get("final_exit_reason") or trade.final_exit_reason
        if values:
            self.store.update_trade(trade_id, **values)
        return self.store.get_trade(trade_id)

    def recover(
        self,
        *,
        run_id: str | None = None,
        execution_service: Any | None = None,
    ) -> RecoveryReport:
        """Reconcile unresolved intents, then hydrate open positions.

        Recovery is idempotent: running it again writes no new intent, fill, or
        event and leaves equity unchanged.  Anything still unknown is reported
        instead of being assumed successful.
        """
        target_run = run_id or self.run_id
        report = RecoveryReport(run_id=target_run)
        fills_before = self._suppressed_duplicate_fills
        events_before = self._suppressed_duplicate_events

        for record in self.store.reconcilable_order_intents(target_run):
            intent = self._intent_from_record(record)
            trade = self.store.get_trade(record.trade_id)
            lifecycle_side = trade.side if trade is not None else "long"
            role = "entry" if (record.purpose or PURPOSE_ENTRY) == PURPOSE_ENTRY else "exit"
            previous_status = record.status
            entry = {
                "order_intent_id": intent.order_intent_id,
                "purpose": record.purpose,
                "status_before": previous_status,
            }
            if previous_status in _ACCEPTING_INTENT_STATUSES:
                # A resting order may have filled while the process was down.
                try:
                    ack = self.adapter.fetch(intent)
                except LostResponse:
                    report.unresolved.append({**entry, "status_after": previous_status, "reason": "query failed"})
                    continue
                if not ack.resolved:
                    self._downgrade_if_invisible(intent, ack)
                    report.unresolved.append({
                        **entry,
                        "status_after": IntentStatus.UNKNOWN.value,
                        "reason": f"resting order not visible: {ack.status.value}",
                    })
                    continue
                applied, _fill_id = self._apply_acknowledgement(
                    intent, ack, role=role, lifecycle_side=lifecycle_side
                )
                if applied.value in ("PARTIALLY_FILLED", "FILLED"):
                    report.observed_offline_fills.append(intent.order_intent_id)
                continue
            resolved = self._try_reconcile(intent, role=role, lifecycle_side=lifecycle_side)
            if resolved is None:
                refreshed = self.store.get_order_intent(intent.order_intent_id)
                entry["status_after"] = refreshed.status
                report.unresolved.append(entry)
                continue
            entry["status_after"] = resolved.status.value
            entry["filled_quantity"] = resolved.filled_quantity
            report.resolved.append(entry)

        if execution_service is not None:
            for trade in self.store.open_trades(target_run):
                trade = self._reconcile_trade_row(trade.trade_id) or trade
                if trade.state == LifecycleState.CLOSED.value:
                    continue
                if not self._protection_is_confirmed(trade.trade_id):
                    # The position is real, so it is still reported; it is never
                    # treated as protected or tradable.
                    report.protection_unconfirmed.append(trade.trade_id)
                    continue
                levels = self._protection_levels(trade.trade_id)
                if not {"stop_loss", "take_profit_1", "take_profit_2"} <= levels.keys():
                    report.hydration_failures.append(
                        {"trade_id": trade.trade_id, "reason": "missing durable protection levels"}
                    )
                    continue
                stop_loss = levels["stop_loss"]
                breakeven = self.store.find_event(trade.trade_id, "BE_UPDATED")
                if breakeven is not None:
                    payload = json.loads(breakeven.payload_json)
                    if payload.get("stop_loss") is not None:
                        stop_loss = float(payload["stop_loss"])
                if not trade.leverage:
                    report.hydration_failures.append(
                        {"trade_id": trade.trade_id, "reason": "missing durable leverage"}
                    )
                    continue
                try:
                    lifecycle = self.reconstruct_lifecycle(trade.trade_id)
                except ValueError as exc:
                    report.hydration_failures.append({"trade_id": trade.trade_id, "reason": str(exc)})
                    continue
                if trade.symbol in execution_service.positions:
                    report.hydrated_trades.append(trade.trade_id)
                    continue
                execution_service.hydrate_position(
                    lifecycle=lifecycle,
                    stop_loss=stop_loss,
                    take_profit_1=levels["take_profit_1"],
                    take_profit_2=levels["take_profit_2"],
                    leverage=float(trade.leverage),
                )
                report.hydrated_trades.append(trade.trade_id)
                if (
                    self.store.find_event(trade.trade_id, "TAKE_PROFIT_1") is not None
                    and breakeven is None
                ):
                    # TP1 filled while offline: the stop still has to be amended
                    # on the exchange.  Recovery reports it instead of assuming
                    # the amendment already happened.
                    report.pending_management.append(trade.trade_id)

        report.duplicate_fills_prevented = self._suppressed_duplicate_fills - fills_before
        report.duplicate_events_prevented = self._suppressed_duplicate_events - events_before
        return report

    def net_pnl(self, trade_id: str) -> float | None:
        """Lifecycle net PnL from durable fills, or None while still open."""
        lifecycle = self.reconstruct_lifecycle(trade_id)
        if lifecycle.state != LifecycleState.CLOSED:
            return None
        self.store.update_trade(trade_id, net_pnl=calculate_realized_net_pnl(lifecycle.fills))
        return calculate_realized_net_pnl(lifecycle.fills)
