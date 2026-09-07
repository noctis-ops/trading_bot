"""Shared deterministic execution/accounting service for Version B adapters."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from core.execution_model import EXECUTION_MODEL_VERSION, IntrabarDecision, evaluate_intrabar
from core.risk_model import FillLeg, calculate_realized_net_pnl
from core.trade_lifecycle import LifecycleState, TradeLifecycle

# Resting-protection purposes, identical to the external execution contract so
# one intent identity scheme covers both the deterministic and the venue path.
PURPOSE_ENTRY = "ENTRY"
PURPOSE_STOP_LOSS = "STOP_LOSS"
PURPOSE_TAKE_PROFIT_1 = "TAKE_PROFIT_1"
PURPOSE_TAKE_PROFIT_2 = "TAKE_PROFIT_2"

# An exit event that consumes a resting protection order.
_EXIT_EVENT_TO_PURPOSE = {
    "STOP_LOSS": PURPOSE_STOP_LOSS,
    "TAKE_PROFIT_1": PURPOSE_TAKE_PROFIT_1,
    "TAKE_PROFIT_2": PURPOSE_TAKE_PROFIT_2,
}

EXIT_EVENT_TYPES = frozenset({
    "TAKE_PROFIT_1", "TAKE_PROFIT_2", "STOP_LOSS", "REVERSAL_EXIT",
    "EMERGENCY_EXIT", "END_OF_DATA", "MANUAL_EXIT",
})



@dataclass
class ExecutionPosition:
    symbol: str
    side: str
    lifecycle: TradeLifecycle
    entry_price: float
    initial_quantity: float
    remaining_quantity: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: float
    leverage: float
    margin_locked: float
    tp1_hit: bool = False
    sl_moved_to_be: bool = False
    last_accounted_exit_pnl: float = 0.0


class VersionBExecutionService:
    """One execution state machine usable by Backtest and deterministic Paper.

    The service does not fetch market data and does not decide entries.  It
    receives an already-approved entry fill or a smallest-available OHLC bar.
    """

    execution_model_version = EXECUTION_MODEL_VERSION

    def __init__(
        self,
        initial_balance: float = 10_000.0,
        *,
        fee_rate: float | None = None,
        slippage_rate: float | None = None,
        move_sl_to_breakeven: bool | None = None,
        risk_engine: Any | None = None,
        store: Any | None = None,
        run_id: str | None = None,
    ):
        if initial_balance <= 0:
            raise ValueError("initial_balance must be positive")
        if fee_rate is None or slippage_rate is None or move_sl_to_breakeven is None:
            from core.runtime_config import load_runtime_config

            runtime = load_runtime_config()
            configured = runtime.get("execution", default={})
            if fee_rate is None:
                fee_rate = float(configured.get("fee_rate", 0.0004))
            if slippage_rate is None:
                slippage_rate = float(configured.get("slippage_rate", 0.0002))
            if move_sl_to_breakeven is None:
                move_sl_to_breakeven = bool(
                    runtime.get("risk_management", "move_sl_to_breakeven_after_tp1", default=True)
                )
        if move_sl_to_breakeven is None:
            move_sl_to_breakeven = True
        if fee_rate < 0 or slippage_rate < 0:
            raise ValueError("fee/slippage rates cannot be negative")
        self.initial_balance = float(initial_balance)
        self.balance = float(initial_balance)
        self.fee_rate = float(fee_rate)
        self.slippage_rate = float(slippage_rate)
        if (store is None) != (run_id is None):
            raise ValueError("store and run_id must be supplied together")
        self.move_sl_to_breakeven = bool(move_sl_to_breakeven)
        self.risk_engine = risk_engine
        self.store = store
        self.run_id = run_id
        self.positions: dict[str, ExecutionPosition] = {}
        self.closed_lifecycles: list[TradeLifecycle] = []
        self._trade_counter = 0
        self._persisted_events: set[str] = set()
        self._persisted_fills: set[str] = set()

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    def _new_trade_id(self, symbol: str) -> str:
        self._trade_counter += 1
        return f"VB-{symbol.replace('/', '')}-{self._trade_counter:06d}"

    # ── durable order intents (unified path) ──────────────────────────────
    # A protection order that exists only in memory is not a protection order:
    # after a restart nobody knows it was resting.  The unified path therefore
    # records the same intent rows the venue path records, using the same
    # identity scheme, so restart recovery can rebuild pending protection.

    def _intent_id(self, trade_id: str, purpose: str) -> str:
        return f"{trade_id}:{purpose}"

    def _client_order_id(self, trade_id: str, purpose: str) -> str:
        return f"{self.run_id}-{trade_id}-{purpose}"

    def _record_intent(
        self,
        *,
        lifecycle: TradeLifecycle,
        purpose: str,
        order_type: str,
        quantity: float,
        price: float | None,
        status: str,
        event_time: datetime | None,
    ) -> str:
        """Write one intent row idempotently and return its identity."""
        if self.store is None or self.run_id is None:
            return self._intent_id(lifecycle.trade_id, purpose)
        order_intent_id = self._intent_id(lifecycle.trade_id, purpose)
        self.store.get_or_create_order_intent(
            order_intent_id=order_intent_id,
            trade=lifecycle.trade_id,
            client_order_id=self._client_order_id(lifecycle.trade_id, purpose),
            symbol=lifecycle.symbol,
            side=lifecycle.side,
            order_type=order_type,
            purpose=purpose,
            intended_quantity=float(quantity),
            intended_price=None if price is None else float(price),
            status=status,
            created_at=event_time,
        )
        return order_intent_id

    def _record_protection_intents(
        self,
        lifecycle: TradeLifecycle,
        *,
        quantity: float,
        entry_price: float,
        stop_loss: float,
        take_profit_1: float,
        take_profit_2: float,
        event_time: datetime | None,
    ) -> None:
        """Entry is filled inline; TP1/TP2/SL rest until an exit consumes them."""
        entry_intent = self._record_intent(
            lifecycle=lifecycle,
            purpose=PURPOSE_ENTRY,
            order_type="MARKET",
            quantity=quantity,
            price=entry_price,
            status="CREATED",
            event_time=event_time,
        )
        if self.store is not None and self.run_id is not None:
            self.store.update_order_intent(
                entry_intent,
                status="FILLED",
                filled_quantity=float(quantity),
                average_fill_price=float(entry_price),
                acknowledged_at=event_time,
            )
        for purpose, price, slice_quantity in (
            (PURPOSE_TAKE_PROFIT_1, take_profit_1, quantity * 0.5),
            (PURPOSE_TAKE_PROFIT_2, take_profit_2, quantity * 0.5),
            (PURPOSE_STOP_LOSS, stop_loss, quantity),
        ):
            self._record_intent(
                lifecycle=lifecycle,
                purpose=purpose,
                order_type="LIMIT" if purpose != PURPOSE_STOP_LOSS else "STOP_MARKET",
                quantity=slice_quantity,
                price=price,
                # SUBMITTED is unresolved by definition, so the row shows up in
                # unresolved_order_intents after a restart.
                status="SUBMITTED",
                event_time=event_time,
            )

    def _resolve_exit_intent(
        self,
        lifecycle: TradeLifecycle,
        *,
        event_type: str,
        fill_price: float,
        quantity: float,
        event_time: datetime | None,
    ) -> str | None:
        """Mark the intent that produced this exit as filled and return its id."""
        if self.store is None or self.run_id is None:
            return None
        purpose = _EXIT_EVENT_TO_PURPOSE.get(event_type, event_type)
        order_intent_id = self._record_intent(
            lifecycle=lifecycle,
            purpose=purpose,
            order_type="LIMIT" if event_type.startswith("TAKE_PROFIT") else "STOP_MARKET",
            quantity=quantity,
            price=fill_price,
            status="CREATED",
            event_time=event_time,
        )
        existing = self.store.get_order_intent(order_intent_id)
        if existing is not None and existing.status == "FILLED":
            return order_intent_id
        self.store.update_order_intent(
            order_intent_id,
            status="FILLED",
            filled_quantity=float(quantity),
            average_fill_price=float(fill_price),
            acknowledged_at=event_time,
        )
        return order_intent_id

    def _persist_lifecycle(self, lifecycle: TradeLifecycle) -> None:
        """Append only new lifecycle rows to the optional durable store."""
        if self.store is None or self.run_id is None:
            return
        # The trade row is created by open_position before its first event is
        # persisted.  This method is deliberately idempotent for restart/retry
        # paths.
        for event in lifecycle.events:
            if event.event_id in self._persisted_events:
                continue
            self.store.append_event(
                event_id=event.event_id,
                trade_id=lifecycle.trade_id,
                sequence=event.sequence,
                event_type=event.event_type,
                event_time=event.event_time,
                payload=event.payload,
            )
            self._persisted_events.add(event.event_id)
        exit_events = [event for event in lifecycle.events if event.event_type in EXIT_EVENT_TYPES]
        exit_index = 0
        for index, fill in enumerate(lifecycle.fills, start=1):
            fill_id = f"{lifecycle.trade_id}:fill:{index}"
            if fill_id in self._persisted_fills:
                if fill.role == "exit":
                    exit_index += 1
                continue
            fill_time = lifecycle.events[0].event_time
            order_intent_id = None
            if fill.role == "entry":
                order_intent_id = self._intent_id(lifecycle.trade_id, PURPOSE_ENTRY)
            if fill.role == "exit":
                if exit_index < len(exit_events):
                    fill_time = exit_events[exit_index].event_time
                    # Link the fill to the resting intent that produced it, so a
                    # restart can tell which protection was consumed.
                    order_intent_id = self._intent_id(
                        lifecycle.trade_id,
                        _EXIT_EVENT_TO_PURPOSE.get(
                            exit_events[exit_index].event_type,
                            exit_events[exit_index].event_type,
                        ),
                    )
                exit_index += 1
            self.store.record_fill(
                fill_id=fill_id,
                trade=lifecycle.trade_id,
                order_intent=order_intent_id,
                role=fill.role,
                side=fill.side,
                fill_time=fill_time,
                price=fill.price,
                quantity=fill.quantity,
                fee=fill.fee,
                slippage=fill.slippage,
                funding=0.0,
            )
            self._persisted_fills.add(fill_id)
        exits = [fill for fill in lifecycle.fills if fill.role == "exit"]
        entries = [fill for fill in lifecycle.fills if fill.role == "entry"]
        gross = 0.0
        if entries and exits:
            average_entry = sum(fill.price * fill.quantity for fill in entries) / sum(fill.quantity for fill in entries)
            if lifecycle.side == "long":
                gross = sum(fill.price * fill.quantity for fill in exits) - average_entry * sum(fill.quantity for fill in exits)
            else:
                gross = average_entry * sum(fill.quantity for fill in exits) - sum(fill.price * fill.quantity for fill in exits)
        values = {
            "state": lifecycle.state.value,
            "remaining_quantity": lifecycle.remaining_quantity,
            "gross_pnl": gross,
            "fees": sum(fill.fee for fill in lifecycle.fills),
            "slippage": sum(fill.slippage for fill in lifecycle.fills),
        }
        if lifecycle.state == LifecycleState.CLOSED:
            final_exit_reason = next(
                (event.event_type for event in reversed(lifecycle.events)
                 if event.event_type in EXIT_EVENT_TYPES),
                None,
            )
            values.update({
                "closed_at": lifecycle.events[-1].event_time,
                "final_exit_reason": final_exit_reason,
                "net_pnl": (
                    self.risk_engine.realized_net_pnl(lifecycle.fills)
                    if self.risk_engine is not None
                    else calculate_realized_net_pnl(lifecycle.fills)
                ),
            })
        self.store.update_trade(lifecycle.trade_id, **values)

    def open_position(
        self,
        *,
        symbol: str,
        side: str,
        entry_price: float,
        quantity: float,
        stop_loss: float,
        take_profit_1: float,
        take_profit_2: float,
        leverage: float,
        trade_id: str | None = None,
        signal_id: str | None = None,
        event_time: datetime | None = None,
    ) -> ExecutionPosition:
        if symbol in self.positions:
            raise ValueError(f"position already open for {symbol}")
        if side not in ("long", "short"):
            raise ValueError(f"unsupported side: {side}")
        if entry_price <= 0 or quantity <= 0 or leverage <= 0:
            raise ValueError("entry price, quantity, and leverage must be positive")
        if side == "long" and not (stop_loss < entry_price < take_profit_1 < take_profit_2):
            raise ValueError("invalid long levels")
        if side == "short" and not (stop_loss > entry_price > take_profit_1 > take_profit_2):
            raise ValueError("invalid short levels")

        notional = entry_price * quantity
        entry_fee = notional * self.fee_rate
        margin = notional / leverage
        if margin + entry_fee > self.balance:
            raise ValueError("insufficient available balance")
        self.balance -= margin + entry_fee

        lifecycle = TradeLifecycle(trade_id or self._new_trade_id(symbol), symbol, side)
        lifecycle.record_entry_fill(
            FillLeg("entry", side, entry_price, quantity, fee=entry_fee),
            event_time=event_time,
        )
        lifecycle.record_management_event(
            "PROTECTION_PLACED",
            {"stop_loss": stop_loss, "take_profit_1": take_profit_1, "take_profit_2": take_profit_2},
            event_time=event_time,
        )
        position = ExecutionPosition(
            symbol=symbol,
            side=side,
            lifecycle=lifecycle,
            entry_price=entry_price,
            initial_quantity=quantity,
            remaining_quantity=quantity,
            stop_loss=stop_loss,
            take_profit_1=take_profit_1,
            take_profit_2=take_profit_2,
            leverage=leverage,
            margin_locked=margin,
        )
        self.positions[symbol] = position
        if self.store is not None and self.run_id is not None:
            self.store.create_trade(
                trade_id=lifecycle.trade_id,
                run_id=self.run_id,
                signal_id=signal_id,
                symbol=symbol,
                side=side,
                state=lifecycle.state.value,
                initial_quantity=lifecycle.initial_quantity,
                remaining_quantity=lifecycle.remaining_quantity,
                opened_at=event_time,
                leverage=leverage,
            )
            self._record_protection_intents(
                lifecycle,
                quantity=quantity,
                entry_price=entry_price,
                stop_loss=stop_loss,
                take_profit_1=take_profit_1,
                take_profit_2=take_profit_2,
                event_time=event_time,
            )
            self._persist_lifecycle(lifecycle)
        return position

    def _restore_trade_counter(self, trade_id: str) -> None:
        """Keep generated trade ids from colliding with durable ones."""
        match = re.search(r"(\d+)$", trade_id)
        if match:
            self._trade_counter = max(self._trade_counter, int(match.group(1)))

    def _restore_persisted_identity(self, lifecycle: TradeLifecycle) -> None:
        """Mark durable rows as already written so a restart cannot re-append them."""
        for event in lifecycle.events:
            self._persisted_events.add(event.event_id)
        for index in range(1, len(lifecycle.fills) + 1):
            self._persisted_fills.add(f"{lifecycle.trade_id}:fill:{index}")

    def hydrate_position(
        self,
        *,
        lifecycle: TradeLifecycle,
        stop_loss: float,
        take_profit_1: float,
        take_profit_2: float,
        leverage: float,
        tp1_hit: bool | None = None,
        sl_moved_to_be: bool | None = None,
    ) -> ExecutionPosition:
        """Rebuild one open position from durable lifecycle rows after a restart.

        Balance, margin, and the persisted-identity sets are replayed from the
        recorded fills, so hydration is arithmetically identical to the
        pre-restart state and re-persisting the lifecycle writes no new rows.
        Hydration never creates a second trade for a symbol that is already
        loaded.
        """
        symbol = lifecycle.symbol
        if symbol in self.positions:
            raise ValueError(f"position already hydrated for {symbol}")
        entries = [fill for fill in lifecycle.fills if fill.role == "entry"]
        exits = [fill for fill in lifecycle.fills if fill.role == "exit"]
        if not entries:
            raise ValueError(f"cannot hydrate {lifecycle.trade_id} without an entry fill")
        if leverage <= 0:
            raise ValueError("leverage must be positive")

        entry_quantity = sum(fill.quantity for fill in entries)
        entry_price = sum(fill.price * fill.quantity for fill in entries) / entry_quantity
        margin_locked = 0.0
        remaining = 0.0
        for fill in entries:
            fill_margin = (fill.price * fill.quantity) / leverage
            margin_locked += fill_margin
            self.balance -= fill_margin + fill.fee
            remaining += fill.quantity
        for fill in exits:
            released = margin_locked * (fill.quantity / remaining) if remaining > 0 else 0.0
            if lifecycle.side == "long":
                gross = (fill.price - entry_price) * fill.quantity
            else:
                gross = (entry_price - fill.price) * fill.quantity
            margin_locked = max(0.0, margin_locked - released)
            remaining -= fill.quantity
            self.balance += released + (gross - fill.fee)

        self._restore_trade_counter(lifecycle.trade_id)
        self._restore_persisted_identity(lifecycle)
        if tp1_hit is None:
            tp1_hit = lifecycle.tp1_processed
        if sl_moved_to_be is None:
            sl_moved_to_be = any(event.event_type == "BE_UPDATED" for event in lifecycle.events)
        position = ExecutionPosition(
            symbol=symbol,
            side=lifecycle.side,
            lifecycle=lifecycle,
            entry_price=entry_price,
            initial_quantity=lifecycle.initial_quantity,
            remaining_quantity=lifecycle.remaining_quantity,
            stop_loss=stop_loss,
            take_profit_1=take_profit_1,
            take_profit_2=take_profit_2,
            leverage=leverage,
            margin_locked=margin_locked,
            tp1_hit=bool(tp1_hit),
            sl_moved_to_be=bool(sl_moved_to_be),
        )
        if lifecycle.state == LifecycleState.CLOSED:
            self.closed_lifecycles.append(lifecycle)
        else:
            self.positions[symbol] = position
        return position

    def _exit_delta(self, position: ExecutionPosition, fill_price: float, quantity: float, fee: float) -> float:
        if position.side == "long":
            gross = (fill_price - position.entry_price) * quantity
        else:
            gross = (position.entry_price - fill_price) * quantity
        return gross - fee

    def process_bar(
        self,
        symbol: str,
        bar: Mapping[str, Any],
        *,
        event_time: datetime | None = None,
    ) -> dict[str, Any] | None:
        """Process at most one exit event and return an execution event."""
        position = self.positions.get(symbol)
        if position is None:
            return None
        decision: IntrabarDecision = evaluate_intrabar(
            {
                "side": position.side,
                "stop_loss": position.stop_loss,
                "take_profit_1": position.take_profit_1,
                "take_profit_2": position.take_profit_2,
                "tp1_hit": position.tp1_hit,
            },
            bar,
            slippage_rate=self.slippage_rate,
        )
        if not decision.triggered:
            return None

        if decision.event_type == "TAKE_PROFIT_1":
            quantity = min(position.initial_quantity * 0.5, position.remaining_quantity)
        else:
            quantity = position.remaining_quantity
        if quantity <= 0:
            return None

        fill_price = float(decision.fill_price)
        fee = fill_price * quantity * self.fee_rate
        slippage_cost = abs(fill_price - float(decision.trigger_price)) * quantity
        position.lifecycle.record_exit_fill(
            FillLeg(
                "exit",
                position.side,
                fill_price,
                quantity,
                fee=fee,
                slippage=slippage_cost,
            ),
            decision.event_type,
            event_time=event_time,
        )
        realized_exit_pnl = self._exit_delta(position, fill_price, quantity, fee)
        position.last_accounted_exit_pnl += realized_exit_pnl
        remaining_before = position.remaining_quantity
        margin_before = position.margin_locked
        released_margin = margin_before * (quantity / remaining_before)
        position.remaining_quantity = position.lifecycle.remaining_quantity
        position.margin_locked = max(0.0, margin_before - released_margin)
        self.balance += released_margin + realized_exit_pnl

        if decision.event_type == "TAKE_PROFIT_1" and position.remaining_quantity > 0:
            position.tp1_hit = True
            if self.move_sl_to_breakeven:
                position.stop_loss = position.entry_price
                position.sl_moved_to_be = True
                position.lifecycle.record_management_event(
                    "BE_UPDATED", {"stop_loss": position.stop_loss}, event_time=event_time
                )
        if position.remaining_quantity <= 1e-12:
            position.remaining_quantity = 0.0
            self.positions.pop(symbol, None)
            self.closed_lifecycles.append(position.lifecycle)
        self._resolve_exit_intent(
            position.lifecycle,
            event_type=decision.event_type,
            fill_price=fill_price,
            quantity=quantity,
            event_time=event_time,
        )
        self._persist_lifecycle(position.lifecycle)

        return {
            "symbol": symbol,
            "trade_id": position.lifecycle.trade_id,
            "event_type": decision.event_type,
            "trigger_price": decision.trigger_price,
            "fill_price": fill_price,
            "quantity": quantity,
            "fee": fee,
            "slippage": slippage_cost,
            "remaining_quantity": position.remaining_quantity,
            "state": position.lifecycle.state.value,
            "execution_model_version": self.execution_model_version,
            "deferred_target": decision.deferred_target,
        }

    def force_close(
        self,
        symbol: str,
        price: float,
        *,
        event_type: str = "END_OF_DATA",
        event_time: datetime | None = None,
    ) -> dict[str, Any] | None:
        """Close the remaining quantity at a deterministic end-of-data price."""
        position = self.positions.get(symbol)
        if position is None:
            return None
        quantity = position.remaining_quantity
        fee = price * quantity * self.fee_rate
        position.lifecycle.record_exit_fill(
            FillLeg("exit", position.side, price, quantity, fee=fee),
            event_type,
            event_time=event_time,
        )
        realized_exit_pnl = self._exit_delta(position, price, quantity, fee)
        released_margin = position.margin_locked
        position.remaining_quantity = 0.0
        position.margin_locked = 0.0
        self.balance += released_margin + realized_exit_pnl
        self.positions.pop(symbol, None)
        self.closed_lifecycles.append(position.lifecycle)
        self._resolve_exit_intent(
            position.lifecycle,
            event_type=event_type,
            fill_price=price,
            quantity=quantity,
            event_time=event_time,
        )
        self._persist_lifecycle(position.lifecycle)
        return {
            "symbol": symbol,
            "trade_id": position.lifecycle.trade_id,
            "event_type": event_type,
            "trigger_price": price,
            "fill_price": price,
            "quantity": quantity,
            "fee": fee,
            "slippage": 0.0,
            "remaining_quantity": 0.0,
            "state": position.lifecycle.state.value,
            "execution_model_version": self.execution_model_version,
            "deferred_target": None,
        }

    def unrealized_pnl(self, symbol: str, mark_price: float) -> float:
        position = self.positions[symbol]
        if position.side == "long":
            return (mark_price - position.entry_price) * position.remaining_quantity
        return (position.entry_price - mark_price) * position.remaining_quantity

    def equity(self, marks: Mapping[str, float] | None = None) -> float:
        margin = sum(position.margin_locked for position in self.positions.values())
        unrealized = sum(
            self.unrealized_pnl(symbol, marks[symbol])
            for symbol in self.positions
            if marks is not None and symbol in marks
        )
        return self.balance + margin + unrealized

    def final_net_pnl(self, trade_id: str) -> float:
        for lifecycle in self.closed_lifecycles:
            if lifecycle.trade_id == trade_id:
                return (
                    self.risk_engine.realized_net_pnl(lifecycle.fills)
                    if self.risk_engine is not None
                    else calculate_realized_net_pnl(lifecycle.fills)
                )
        raise KeyError(trade_id)
