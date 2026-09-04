"""The single allowed definitions for Version B measurement metrics.

Two incompatible "Max Drawdown" definitions existed in this repository.  This
module pins one.  Every Version B measurement must use these functions and
these exact metric keys; a bare ``win_rate`` or ``max_drawdown`` is ambiguous
and is rejected by the baseline artifact contract.

These are model metrics.  They describe the frozen replay model, never an
operational or live result.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence


EQUITY_CURVE_DEFINITION = (
    "Account equity from VersionBExecutionService.equity(marks=...) using the "
    "decision-bar close as the mark, seeded with initial_balance. Equity is "
    "wallet value + margin used + unrealized PnL - accrued costs. A curve built "
    "without marks is not an equity curve and must not be used for drawdown. "
    "SAMPLING RESOLUTION: one point per 15m decision bar, while exits resolve on "
    "5m bars, so movement inside a decision interval is not observed."
)

MAX_DRAWDOWN_DEFINITION = (
    "Largest peak-to-trough decline of the marked equity curve, expressed as a "
    "percentage of the running peak: max((peak - equity) / peak * 100). It is "
    "computed on the equity curve against the actual initial_balance. It is "
    "never computed from closed-trade PnL and never against an arbitrary base. "
    "Because the curve is sampled per 15m decision bar, this value is a LOWER "
    "BOUND on the true peak-to-trough drawdown; a deeper intra-interval trough "
    "is not visible to it and must not be claimed as measured."
)

# Exact metric keys allowed in a STRATEGY_MODEL_BASELINE artifact.
MODEL_METRIC_KEYS = (
    "model_trade_count",
    "model_winning_count",
    "model_losing_count",
    "model_win_rate_pct",
    "model_profit_factor",
    "model_net_pnl",
    "model_gross_win",
    "model_gross_loss",
    "model_cost_total",
    "model_forced_exit_count",
    "model_exit_reason_distribution",
    "equity_curve_max_drawdown_pct",
    "equity_curve_peak_index",
    "equity_curve_trough_index",
    "equity_curve_peak_equity",
    "equity_curve_trough_equity",
    "equity_curve_points",
    "equity_curve_start",
    "equity_curve_end",
)

# Exits produced by the end of the data window rather than by the model's own
# stop/target logic.  They are a property of the chosen window and must never
# be silently mixed into model outcomes.
FORCED_EXIT_TYPES = ("END_OF_DATA",)


def compute_equity_curve_max_drawdown(
    equity_curve: Sequence[float],
    *,
    initial_balance: float,
) -> dict[str, Any]:
    """Peak-to-trough drawdown of a marked equity curve."""
    if initial_balance <= 0:
        raise ValueError("initial_balance must be positive")
    curve = [float(value) for value in equity_curve]
    if not curve:
        raise ValueError("equity_curve must not be empty")
    peak = curve[0]
    peak_index = 0
    max_decline_pct = 0.0
    trough_index = 0
    trough_equity = curve[0]
    for index, equity in enumerate(curve):
        if equity > peak:
            peak = equity
            peak_index = index
        if peak <= 0:
            raise ValueError("equity curve reached a non-positive peak; drawdown is undefined")
        decline_pct = (peak - equity) / peak * 100.0
        if decline_pct > max_decline_pct:
            max_decline_pct = decline_pct
            trough_index = index
            trough_equity = equity
    return {
        "equity_curve_max_drawdown_pct": round(max_decline_pct, 6),
        "equity_curve_peak_index": peak_index,
        "equity_curve_trough_index": trough_index,
        "equity_curve_peak_equity": round(peak, 6),
        "equity_curve_trough_equity": round(trough_equity, 6),
        "equity_curve_points": len(curve),
        "equity_curve_start": round(curve[0], 6),
        "equity_curve_end": round(curve[-1], 6),
    }


def compute_model_metrics(
    *,
    trades: Iterable[Mapping[str, Any]],
    equity_curve: Sequence[float],
    initial_balance: float,
) -> dict[str, Any]:
    """Model metrics from closed lifecycles and the marked equity curve.

    One lifecycle is one trade; partial exits are events inside it.  Forced
    end-of-data exits are counted separately so a window artifact is never
    presented as a model outcome.
    """
    rows = list(trades)
    profits = [float(row.get("profit", 0.0)) for row in rows]
    wins = [value for value in profits if value > 0]
    losses = [value for value in profits if value <= 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    total = len(rows)
    exit_reasons: dict[str, int] = {}
    forced = 0
    for row in rows:
        reason = str(row.get("exit_type", "UNKNOWN"))
        exit_reasons[reason] = exit_reasons.get(reason, 0) + 1
        if reason in FORCED_EXIT_TYPES:
            forced += 1
    costs = sum(float(row.get("fees", 0.0) or 0.0) for row in rows)
    metrics: dict[str, Any] = {
        "model_trade_count": total,
        "model_winning_count": len(wins),
        "model_losing_count": len(losses),
        "model_win_rate_pct": round(len(wins) / total * 100.0, 6) if total else 0.0,
        # Undefined when there is no losing lifecycle; never reported as infinity.
        "model_profit_factor": round(gross_win / gross_loss, 6) if gross_loss > 0 else None,
        "model_net_pnl": round(sum(profits), 6),
        "model_gross_win": round(gross_win, 6),
        "model_gross_loss": round(gross_loss, 6),
        "model_cost_total": round(costs, 6),
        "model_forced_exit_count": forced,
        "model_exit_reason_distribution": dict(sorted(exit_reasons.items())),
    }
    metrics.update(
        compute_equity_curve_max_drawdown(equity_curve, initial_balance=initial_balance)
    )
    return metrics
