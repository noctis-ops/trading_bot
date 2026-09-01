"""Single canonical Version B risk calculation path.

This module owns stops and position sizing for every Version B adapter.  The
legacy RiskManager may expose compatibility wrappers, but B code must call
this engine rather than reimplementing the formulas.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from core.risk_model import (
    FillLeg,
    PlannedRisk,
    calculate_daily_loss,
    calculate_planned_risk,
    calculate_portfolio_risk_at_stop,
    calculate_realized_net_pnl,
)
from core.runtime_config import load_runtime_config


@dataclass(frozen=True)
class PositionSize:
    side: str
    risk_amount: float
    stop_distance: float
    stop_distance_pct: float
    position_notional: float
    quantity: float
    leverage: float
    score_factor: float
    planned_risk: PlannedRisk

    def to_dict(self) -> dict[str, Any]:
        return {
            "side": self.side,
            "risk_amount": self.risk_amount,
            "stop_distance": self.stop_distance,
            "stop_distance_pct": self.stop_distance_pct,
            "position_notional": self.position_notional,
            "contract_size": self.quantity,
            "quantity": self.quantity,
            "leverage": self.leverage,
            "size_factor": self.score_factor,
            "planned_risk": self.planned_risk.total,
            "planned_risk_percent": self.planned_risk.percent_of_equity,
        }


class RiskEngine:
    """Canonical stops, sizing, and risk semantics for Version B."""

    def __init__(self, runtime_config: Any | None = None):
        self.config = runtime_config or load_runtime_config()
        self.risk = self.config.get("risk_management", default={})
        self.strategy = self.config.get("strategy", default={})
        self.execution = self.config.get("execution", default={})

    def calculate_stops(self, *, side: str, entry_price: float, atr: float) -> dict[str, Any]:
        if side not in {"long", "short"}:
            raise ValueError(f"unsupported side: {side}")
        if entry_price <= 0 or atr <= 0:
            return {"valid": False, "reason": "entry_price and atr must be positive"}
        volatility = self.strategy.get("volatility", {})
        sl_mult = float(volatility.get("atr_stop_loss_multiplier", 1.5))
        tp1_mult = float(volatility.get("atr_tp1_multiplier", 2.0))
        tp2_mult = float(volatility.get("atr_tp2_multiplier", 4.5))
        if side == "long":
            stop = entry_price - atr * sl_mult
            tp1 = entry_price + atr * tp1_mult
            tp2 = entry_price + atr * tp2_mult
        else:
            stop = entry_price + atr * sl_mult
            tp1 = entry_price - atr * tp1_mult
            tp2 = entry_price - atr * tp2_mult
        risk = abs(entry_price - stop)
        reward = (abs(tp1 - entry_price) + abs(tp2 - entry_price)) / 2.0
        ratio = reward / risk if risk else 0.0
        return {
            "valid": ratio >= float(self.risk.get("min_risk_reward_ratio", 2.0)),
            "stop_loss": stop,
            "take_profit_1": tp1,
            "take_profit_2": tp2,
            "sl_distance": risk,
            "tp1_distance": abs(tp1 - entry_price),
            "tp2_distance": abs(tp2 - entry_price),
            "risk": risk,
            "reward": reward,
            "risk_reward_ratio": ratio,
            "side": side,
            "entry_price": entry_price,
            "atr": atr,
        }

    def calculate_position_size(
        self,
        *,
        equity: float,
        entry_price: float,
        stop_price: float,
        side: str,
        score: float = 100.0,
    ) -> PositionSize:
        if equity <= 0 or entry_price <= 0 or stop_price <= 0:
            raise ValueError("equity, entry_price, and stop_price must be positive")
        if side == "long" and stop_price >= entry_price:
            raise ValueError("long stop must be below entry")
        if side == "short" and stop_price <= entry_price:
            raise ValueError("short stop must be above entry")

        score_cfg = self.risk
        score_min = float(score_cfg.get("score_min_trade", 60.0))
        score_strong = float(score_cfg.get("score_strong", 80.0))
        strong_factor = float(score_cfg.get("score_strong_size_factor", 1.0))
        good_factor = float(score_cfg.get("score_good_size_factor", 0.75))
        weak_factor = float(score_cfg.get("score_weak_size_factor", 0.5))
        if score >= score_strong:
            factor = strong_factor
        elif score >= score_min:
            factor = good_factor
        else:
            factor = weak_factor

        risk_amount = equity * float(score_cfg.get("risk_percent_per_trade", 2.0)) / 100.0 * factor
        distance = abs(entry_price - stop_price)
        distance_pct = distance / entry_price
        notional = risk_amount / distance_pct
        max_leverage = float(score_cfg.get("max_leverage", 10))
        leverage = max(1.0, min(notional / equity, max_leverage))
        quantity = notional / entry_price
        planned = self.calculate_planned_risk(
            side=side,
            entry_price=entry_price,
            stop_price=stop_price,
            quantity=quantity,
            equity=equity,
        )
        return PositionSize(
            side=side,
            risk_amount=risk_amount,
            stop_distance=distance,
            stop_distance_pct=distance_pct,
            position_notional=notional,
            quantity=quantity,
            leverage=leverage,
            score_factor=factor,
            planned_risk=planned,
        )

    def calculate_planned_risk(self, **kwargs) -> PlannedRisk:
        """Canonical planned-risk facade; the pure model owns the formula."""
        kwargs.setdefault("fee_rate", float(self.execution.get("fee_rate", 0.0)))
        kwargs.setdefault("stop_slippage_rate", float(self.execution.get("slippage_rate", 0.0)))
        return calculate_planned_risk(**kwargs)

    def realized_net_pnl(self, fills: list[FillLeg]) -> float:
        """Canonical realized net PnL facade; costs are included by the model."""
        return calculate_realized_net_pnl(fills)

    def daily_loss(self, start_of_day_equity: float, current_equity: float) -> float:
        return calculate_daily_loss(start_of_day_equity, current_equity)

    def portfolio_risk_at_stop(self, positions, *, candidate=None, equity: float) -> float:
        return calculate_portfolio_risk_at_stop(
            positions,
            candidate=candidate,
            equity=equity,
            fee_rate=float(self.execution.get("fee_rate", 0.0)),
            stop_slippage_rate=float(self.execution.get("slippage_rate", 0.0)),
        )
