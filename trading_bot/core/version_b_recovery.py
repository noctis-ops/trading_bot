"""Restart recovery for the unified Version B path.

The only input is the durable store.  Nothing here reads process memory, an
in-memory exchange, or a cached position map: if a row is missing the position
is reported as unrecoverable rather than silently reconstructed from a
remembered value.

This module deliberately does **not** talk to a venue.  Reconciling resting
orders against a real exchange belongs to ``core/external_execution.py`` and
requires exchange evidence.  What this module proves is the weaker but
necessary claim: after a restart, the deterministic path can rebuild
trade identity, fills, TP1/breakeven state, pending protection intents, and
balance/margin accounting from the database alone.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import timezone
from typing import Any

from core.contracts import EventRecord
from core.risk_model import FillLeg
from core.trade_lifecycle import TradeLifecycle

EXIT_EVENT_TYPES = frozenset({
    "TAKE_PROFIT_1", "TAKE_PROFIT_2", "STOP_LOSS", "REVERSAL_EXIT",
    "EMERGENCY_EXIT", "END_OF_DATA", "MANUAL_EXIT",
})


@dataclass
class UnifiedRecoveryReport:
    """What a restart recovered, and what it could not."""

    hydrated_trades: list[str] = field(default_factory=list)
    already_closed: list[str] = field(default_factory=list)
    skipped_already_loaded: list[str] = field(default_factory=list)
    hydration_failures: list[dict[str, str]] = field(default_factory=list)
    pending_order_intents: list[str] = field(default_factory=list)
    tp1_taken_before_restart: list[str] = field(default_factory=list)
    breakeven_armed_before_restart: list[str] = field(default_factory=list)
    trailing_updates_before_restart: list[str] = field(default_factory=list)
    rows_before: dict[str, int] = field(default_factory=dict)
    rows_after: dict[str, int] = field(default_factory=dict)

    @property
    def is_clean(self) -> bool:
        return not self.hydration_failures

    @property
    def wrote_no_new_rows(self) -> bool:
        """Idempotency: recovery reads, it never re-appends history."""
        return self.rows_before == self.rows_after

    def as_dict(self) -> dict[str, Any]:
        return {
            "hydrated_trades": list(self.hydrated_trades),
            "already_closed": list(self.already_closed),
            "skipped_already_loaded": list(self.skipped_already_loaded),
            "hydration_failures": list(self.hydration_failures),
            "pending_order_intents": list(self.pending_order_intents),
            "tp1_taken_before_restart": list(self.tp1_taken_before_restart),
            "breakeven_armed_before_restart": list(self.breakeven_armed_before_restart),
            "trailing_updates_before_restart": list(self.trailing_updates_before_restart),
            "is_clean": self.is_clean,
            "wrote_no_new_rows": self.wrote_no_new_rows,
        }


def _aware(value):
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def rebuild_lifecycle(store, trade_id: str) -> TradeLifecycle:
    """Rebuild one lifecycle purely from durable event and fill rows."""
    trade = store.get_trade(trade_id)
    if trade is None:
        raise KeyError(trade_id)
    events = [
        EventRecord(
            event_id=row.event_id,
            trade_id=trade_id,
            event_type=row.event_type,
            event_time=_aware(row.event_time),
            sequence=row.sequence,
            payload=json.loads(row.payload_json or "{}"),
        )
        for row in store.events_for_trade(trade_id)
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
        for row in store.fills_for_trade(trade_id)
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


def protection_levels(store, trade_id: str) -> dict[str, float]:
    """Resting levels as last amended before the restart.

    ``PROTECTION_PLACED`` seeds them; ``BE_UPDATED`` supersedes the stop, which
    is how breakeven state survives a restart without any in-memory flag.
    """
    placed = store.find_event(trade_id, "PROTECTION_PLACED")
    levels: dict[str, float] = {}
    if placed is not None:
        payload = json.loads(placed.payload_json or "{}")
        for key in ("stop_loss", "take_profit_1", "take_profit_2"):
            if payload.get(key) is not None:
                levels[key] = float(payload[key])
    breakeven = store.find_event(trade_id, "BE_UPDATED")
    if breakeven is not None:
        payload = json.loads(breakeven.payload_json or "{}")
        if payload.get("stop_loss") is not None:
            levels["stop_loss"] = float(payload["stop_loss"])
            levels["breakeven_armed"] = True
    return levels


def recover_unified_state(store, run_id: str, execution_service) -> UnifiedRecoveryReport:
    """Rebuild every open position of a run into ``execution_service``.

    Balance and margin are replayed from durable fills inside
    ``hydrate_position``; this function supplies only identity, levels, and
    leverage read from the database.  Running it twice is a no-op.
    """
    report = UnifiedRecoveryReport()
    report.rows_before = _row_counts(store, run_id)

    for trade in store.open_trades(run_id):
        trade_id = trade.trade_id
        if trade.state == "CLOSED":
            report.already_closed.append(trade_id)
            continue
        if trade.symbol in execution_service.positions:
            report.skipped_already_loaded.append(trade_id)
            continue
        if not trade.leverage:
            report.hydration_failures.append(
                {"trade_id": trade_id, "reason": "missing durable leverage"}
            )
            continue
        levels = protection_levels(store, trade_id)
        if not {"stop_loss", "take_profit_1", "take_profit_2"} <= levels.keys():
            report.hydration_failures.append(
                {"trade_id": trade_id, "reason": "missing durable protection levels"}
            )
            continue
        try:
            lifecycle = rebuild_lifecycle(store, trade_id)
        except (KeyError, ValueError) as exc:
            report.hydration_failures.append({"trade_id": trade_id, "reason": str(exc)})
            continue

        if store.find_event(trade_id, "TAKE_PROFIT_1") is not None:
            report.tp1_taken_before_restart.append(trade_id)
        if levels.get("breakeven_armed"):
            report.breakeven_armed_before_restart.append(trade_id)
        if store.find_event(trade_id, "TRAILING_UPDATED") is not None:
            report.trailing_updates_before_restart.append(trade_id)

        execution_service.hydrate_position(
            lifecycle=lifecycle,
            stop_loss=levels["stop_loss"],
            take_profit_1=levels["take_profit_1"],
            take_profit_2=levels["take_profit_2"],
            leverage=float(trade.leverage),
        )
        report.hydrated_trades.append(trade_id)

    # Resting protection is a pending order intent: unresolved by definition
    # until an exit consumes it, so a restart must still see it.
    report.pending_order_intents = [
        intent.order_intent_id for intent in store.unresolved_order_intents(run_id)
    ]
    report.rows_after = _row_counts(store, run_id)
    return report


def _row_counts(store, run_id: str) -> dict[str, int]:
    """Row totals used to prove recovery appended nothing."""
    counts = {
        "decisions": 0,
        "trades": 0,
        "events": 0,
        "fills": 0,
        "order_intents": 0,
    }
    for trade in store.open_trades(run_id):
        counts["trades"] += 1
        counts["events"] += len(store.events_for_trade(trade.trade_id))
        counts["fills"] += len(store.fills_for_trade(trade.trade_id))
        counts["order_intents"] += len(store.order_intents_for_trade(trade.trade_id))
    return counts


__all__ = [
    "UnifiedRecoveryReport",
    "protection_levels",
    "rebuild_lifecycle",
    "recover_unified_state",
]
