"""Pure Version B risk and accounting definitions.

No strategy decision is made here.  These functions are deterministic and are
shared by RiskManager, Paper, Backtest, and reporting code.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal


Side = Literal["long", "short"]


@dataclass(frozen=True)
class PlannedRisk:
    """Modeled loss to the initial stop, including modeled costs."""

    side: str
    entry_price: float
    stop_price: float
    quantity: float
    gross_loss: float
    entry_fee: float
    stop_exit_fee: float
    stop_slippage: float
    total: float
    percent_of_equity: float


@dataclass(frozen=True)
class FillLeg:
    """A normalized entry/exit fill used for lifecycle PnL accounting."""

    role: Literal["entry", "exit"]
    side: Side
    price: float
    quantity: float
    fee: float = 0.0
    slippage: float = 0.0


@dataclass(frozen=True)
class PositionRiskInput:
    """Position state required for portfolio risk-at-stop."""

    symbol: str
    side: Side
    entry_price: float
    active_stop: float
    quantity: float


def calculate_equity(
    wallet_value: float,
    margin_used: float,
    unrealized_pnl: float,
    accrued_costs: float = 0.0,
) -> float:
    """Calculate account equity without confusing it with free balance."""
    return wallet_value + margin_used + unrealized_pnl - accrued_costs


def calculate_planned_risk(
    *,
    side: Side,
    entry_price: float,
    stop_price: float,
    quantity: float,
    equity: float,
    fee_rate: float = 0.0,
    stop_slippage_rate: float = 0.0,
) -> PlannedRisk:
    """Calculate modeled initial-stop loss from the actual entry fill.

    The entry price is already the actual fill, so entry slippage is not added
    a second time.  Stop slippage is modeled adversely.  Gap risk beyond the
    modeled stop remains a separately reported execution risk.
    """
    if side not in ("long", "short"):
        raise ValueError(f"unsupported side: {side}")
    if entry_price <= 0 or stop_price <= 0 or quantity <= 0:
        raise ValueError("entry_price, stop_price, and quantity must be positive")
    if fee_rate < 0 or stop_slippage_rate < 0:
        raise ValueError("fee and slippage rates cannot be negative")
    if side == "long" and stop_price >= entry_price:
        raise ValueError("long stop must be below entry")
    if side == "short" and stop_price <= entry_price:
        raise ValueError("short stop must be above entry")

    if side == "long":
        adverse_stop = stop_price * (1.0 - stop_slippage_rate)
        gross_loss = max(0.0, (entry_price - adverse_stop) * quantity)
    else:
        adverse_stop = stop_price * (1.0 + stop_slippage_rate)
        gross_loss = max(0.0, (adverse_stop - entry_price) * quantity)

    entry_notional = entry_price * quantity
    stop_notional = adverse_stop * quantity
    entry_fee = entry_notional * fee_rate
    stop_exit_fee = stop_notional * fee_rate
    stop_slippage = abs(stop_price - adverse_stop) * quantity
    total = gross_loss + entry_fee + stop_exit_fee
    percent = total / equity * 100.0 if equity > 0 else 0.0

    return PlannedRisk(
        side=side,
        entry_price=entry_price,
        stop_price=stop_price,
        quantity=quantity,
        gross_loss=gross_loss,
        entry_fee=entry_fee,
        stop_exit_fee=stop_exit_fee,
        stop_slippage=stop_slippage,
        total=total,
        percent_of_equity=percent,
    )


def calculate_realized_net_pnl(
    fills: Iterable[FillLeg],
    *,
    funding: float = 0.0,
) -> float:
    """Calculate complete lifecycle net PnL from entry/exit fills.

    Multiple entry and exit fills are aggregated in one lifecycle.  The
    weighted average entry price is used for the lifecycle's price PnL.
    Fees/funding are deducted; slippage is retained for attribution because it
    is already embedded in the executed fill prices.
    """
    legs = list(fills)
    entries = [fill for fill in legs if fill.role == "entry"]
    exits = [fill for fill in legs if fill.role == "exit"]
    if not entries or not exits:
        raise ValueError("a realized lifecycle needs entry and exit fills")
    sides = {fill.side for fill in entries + exits}
    if len(sides) != 1:
        raise ValueError("all lifecycle fills must use one side")
    side = entries[0].side
    entry_quantity = sum(fill.quantity for fill in entries)
    exit_quantity = sum(fill.quantity for fill in exits)
    if entry_quantity <= 0 or exit_quantity <= 0 or exit_quantity > entry_quantity + 1e-12:
        raise ValueError("exit quantity must be positive and no greater than entry quantity")

    average_entry = sum(fill.price * fill.quantity for fill in entries) / entry_quantity
    if side == "long":
        gross = sum(fill.price * fill.quantity for fill in exits) - average_entry * exit_quantity
    else:
        gross = average_entry * exit_quantity - sum(fill.price * fill.quantity for fill in exits)
    fees = sum(fill.fee for fill in legs)
    # FillLeg.price is the executed fill price.  Slippage is already embedded
    # in gross price PnL; the field is retained as an attribution value and
    # must not be subtracted a second time.
    return gross - fees - funding


def calculate_daily_loss(start_of_day_equity: float, current_equity: float) -> float:
    """Return conservative equity loss; gains produce zero loss."""
    return max(0.0, start_of_day_equity - current_equity)


def calculate_portfolio_risk_at_stop(
    positions: Iterable[PositionRiskInput],
    *,
    candidate: PositionRiskInput | None = None,
    equity: float,
    fee_rate: float = 0.0,
    stop_slippage_rate: float = 0.0,
) -> float:
    """Aggregate modeled loss to each position's current active stop."""
    items = list(positions)
    if candidate is not None:
        items.append(candidate)
    total = 0.0
    for position in items:
        total += calculate_planned_risk(
            side=position.side,
            entry_price=position.entry_price,
            stop_price=position.active_stop,
            quantity=position.quantity,
            equity=equity,
            fee_rate=fee_rate,
            stop_slippage_rate=stop_slippage_rate,
        ).total
    return total
