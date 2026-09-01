"""Small domain contracts shared by Version B components."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping


@dataclass(frozen=True)
class DecisionContext:
    """The immutable context of one strategy evaluation."""

    symbol: str
    decision_time: datetime
    timeframe_timestamps: Mapping[str, datetime]
    data_status: str = "VALID"
    run_id: str | None = None
    strategy_version: str = "1.3"
    config_hash: str | None = None


@dataclass(frozen=True)
class Fill:
    """A single execution fill; partial fills are separate records."""

    fill_id: str
    order_intent_id: str
    timestamp: datetime
    price: float
    quantity: float
    fee: float = 0.0
    slippage: float = 0.0
    funding: float = 0.0


@dataclass(frozen=True)
class RiskSnapshot:
    """Risk values recorded at one decision or portfolio event."""

    equity: float
    available_balance: float
    margin_used: float
    gross_notional: float
    planned_risk: float
    portfolio_risk_at_stop: float
    daily_loss: float


@dataclass
class EventRecord:
    """Serializable lifecycle event envelope."""

    event_id: str
    trade_id: str
    event_type: str
    event_time: datetime
    sequence: int
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        result = {
            "event_id": self.event_id,
            "trade_id": self.trade_id,
            "event_type": self.event_type,
            "event_time": self.event_time.isoformat(),
            "sequence": self.sequence,
            "payload": self.payload,
        }
        return result
