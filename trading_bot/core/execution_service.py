"""Shared deterministic execution/accounting service for Version B adapters."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from core.execution_model import EXECUTION_MODEL_VERSION, IntrabarDecision, evaluate_intrabar
from core.risk_model import FillLeg, calculate_realized_net_pnl
from core.trade_lifecycle import LifecycleState, TradeLifecycle


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
        exit_events = [event for event in lifecycle.events if event.event_type in {
            "TAKE_PROFIT_1", "TAKE_PROFIT_2", "STOP_LOSS", "REVERSAL_EXIT",
            "EMERGENCY_EXIT", "END_OF_DATA", "MANUAL_EXIT",
        }]
        exit_index = 0
        for index, fill in enumerate(lifecycle.fills, start=1):
            fill_id = f"{lifecycle.trade_id}:fill:{index}"
            if fill_id in self._persisted_fills:
                if fill.role == "exit":
                    exit_index += 1
                continue
            fill_time = lifecycle.events[0].event_time
            if fill.role == "exit":
                if exit_index < len(exit_events):
                    fill_time = exit_events[exit_index].event_time
                exit_index += 1
            self.store.record_fill(
                fill_id=fill_id,
                trade=lifecycle.trade_id,
                order_intent=None,
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
                 if event.event_type in {"TAKE_PROFIT_1", "TAKE_PROFIT_2", "STOP_LOSS", "REVERSAL_EXIT", "EMERGENCY_EXIT", "END_OF_DATA", "MANUAL_EXIT"}),
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
            )
            self._persist_lifecycle(lifecycle)
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
