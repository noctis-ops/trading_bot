"""Deterministic Version B execution model.

Entry decisions remain on 15m.  This module uses 5m bars only to replay exits.
When a bar touches both a stop and a target and tick order is unavailable,
stop-first is the fixed conservative rule.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


EXECUTION_MODEL_VERSION = "vb-1.0-5m-stop-first"


@dataclass(frozen=True)
class IntrabarDecision:
    event_type: str | None
    trigger_price: float | None
    fill_price: float | None
    deferred_target: str | None = None
    rule: str = "stop-first; one lifecycle event per bar"
    execution_model_version: str = EXECUTION_MODEL_VERSION

    @property
    def triggered(self) -> bool:
        return self.event_type is not None


def _valid_bar(bar: Mapping[str, Any]) -> None:
    for key in ("open", "high", "low", "close"):
        if key not in bar:
            raise ValueError(f"bar missing {key}")
    if float(bar["low"]) > float(bar["high"]):
        raise ValueError("bar low cannot exceed high")


def _fill_price(
    *,
    side: str,
    event_type: str,
    level: float,
    bar_open: float,
    slippage_rate: float,
) -> float:
    """Apply a deterministic gap and adverse-side slippage model."""
    if level <= 0 or bar_open <= 0 or slippage_rate < 0:
        raise ValueError("prices must be positive and slippage cannot be negative")

    is_long = side == "long"
    is_stop = event_type == "STOP_LOSS"
    # A long exits by selling; a short exits by buying.
    exit_is_sell = is_long
    if is_stop:
        crossed_gap = bar_open <= level if is_long else bar_open >= level
    else:
        crossed_gap = bar_open >= level if is_long else bar_open <= level
    base = bar_open if crossed_gap else level
    return base * (1.0 - slippage_rate) if exit_is_sell else base * (1.0 + slippage_rate)


def evaluate_intrabar(
    position: Mapping[str, Any],
    bar: Mapping[str, Any],
    *,
    slippage_rate: float = 0.0,
) -> IntrabarDecision:
    """Evaluate at most one exit event for one smallest-available bar.

    Rules:
    1. Active stop wins over every target touched in the same bar.
    2. Without a stop hit, TP1 is processed before TP2.
    3. If TP1 and TP2 are both touched while TP1 is not processed, only TP1
       is emitted; TP2 is deferred to the next bar.
    4. No event is emitted for an invalid/missing level.
    """
    _valid_bar(bar)
    side = str(position.get("side", "long")).lower()
    if side not in ("long", "short"):
        raise ValueError(f"unsupported position side: {side}")

    high = float(bar["high"])
    low = float(bar["low"])
    bar_open = float(bar["open"])
    stop = position.get("stop_loss")
    tp1 = position.get("take_profit_1")
    tp2 = position.get("take_profit_2")
    tp1_hit = bool(position.get("tp1_hit", False))

    if side == "long":
        stop_hit = stop is not None and low <= float(stop)
        tp1_hit_in_bar = not tp1_hit and tp1 is not None and high >= float(tp1)
        tp2_hit_in_bar = tp2 is not None and high >= float(tp2)
    else:
        stop_hit = stop is not None and high >= float(stop)
        tp1_hit_in_bar = not tp1_hit and tp1 is not None and low <= float(tp1)
        tp2_hit_in_bar = tp2 is not None and low <= float(tp2)

    if stop_hit:
        level = float(stop)
        return IntrabarDecision(
            event_type="STOP_LOSS",
            trigger_price=level,
            fill_price=_fill_price(
                side=side,
                event_type="STOP_LOSS",
                level=level,
                bar_open=bar_open,
                slippage_rate=slippage_rate,
            ),
        )

    if tp1_hit_in_bar:
        return IntrabarDecision(
            event_type="TAKE_PROFIT_1",
            trigger_price=float(tp1),
            fill_price=_fill_price(
                side=side,
                event_type="TAKE_PROFIT_1",
                level=float(tp1),
                bar_open=bar_open,
                slippage_rate=slippage_rate,
            ),
            deferred_target="TAKE_PROFIT_2" if tp2_hit_in_bar else None,
        )

    if tp2_hit_in_bar:
        return IntrabarDecision(
            event_type="TAKE_PROFIT_2",
            trigger_price=float(tp2),
            fill_price=_fill_price(
                side=side,
                event_type="TAKE_PROFIT_2",
                level=float(tp2),
                bar_open=bar_open,
                slippage_rate=slippage_rate,
            ),
        )

    return IntrabarDecision(None, None, None)
