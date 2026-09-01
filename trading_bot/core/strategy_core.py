"""Shared Version B adapter around the frozen TradingStrategy rules."""

from __future__ import annotations

from typing import Any

from core.decision_taxonomy import DecisionReason, DecisionStage, taxonomy_record
from core.risk_engine import RiskEngine
from core.strategy import TradingStrategy
from data.time_alignment import AlignedFrames, DataStatus, legacy_closed_view


class StrategyCore:
    """Evaluate the existing strategy against an aligned, immutable data view.

    This class intentionally delegates all gate, score, MTF, and Long/Short
    calculations to the existing ``TradingStrategy`` implementation.  The
    only adaptation is converting already-closed Version B frames to the
    legacy ``iloc[-2]`` interface without adding a real market candle.
    """

    def __init__(
        self,
        strategy: TradingStrategy | None = None,
        risk_engine: RiskEngine | None = None,
    ):
        self.strategy = strategy or TradingStrategy()
        self.risk_engine = risk_engine or RiskEngine()

    def evaluate(
        self,
        symbol: str,
        aligned: AlignedFrames,
        *,
        direction: str,
        config_hash: str | None = None,
    ) -> dict[str, Any]:
        """Evaluate one frozen Long or Short decision.

        Data quality is handled before strategy evaluation.  A data failure is
        therefore never represented as ``NO_SIGNAL``.
        """
        if not aligned.valid:
            quality = {
                timeframe: item.status.value
                for timeframe, item in aligned.quality.items()
                if item.status != DataStatus.VALID
            }
            primary = next(iter(quality.values()), DataStatus.INVALID_SCHEMA.value)
            return {
                "symbol": symbol,
                "direction": direction,
                "should_trade": False,
                "data_status": quality,
                "decision": taxonomy_record(
                    stage=DecisionStage.DATA,
                    reason=DecisionReason.INSUFFICIENT_WARMUP
                    if primary == DataStatus.INSUFFICIENT_WARMUP.value
                    else DecisionReason.DATA_FAILURE,
                    detail=str(quality),
                ),
                "decision_time": aligned.decision_time.isoformat(),
            }

        frames = aligned.frames
        views = {
            timeframe: legacy_closed_view(frame, aligned.decision_time)
            for timeframe, frame in frames.items()
        }
        if direction not in {"long", "short"}:
            raise ValueError(f"unsupported strategy direction: {direction}")
        breakdown_method = (
            "get_short_signal_breakdown" if direction == "short"
            else "get_signal_breakdown"
        )
        if hasattr(self.strategy, breakdown_method):
            breakdown = getattr(self.strategy, breakdown_method)(
                views["1h"], views["15m"], views["5m"]
            )
        else:
            # Compatibility boundary for small deterministic strategy doubles.
            # The B pipeline still enters through StrategyCore; production
            # TradingStrategy always takes the canonical breakdown method.
            check_name = "check_short_signal" if direction == "short" else "check_buy_signal"
            check = getattr(self.strategy, check_name, None) or self.strategy.check_buy_signal
            gate_passed, gate_result = check(
                views["1h"], views["15m"], views["5m"]
            )
            score_result = {"total_score": 0.0, "recommendation": "weak"}
            signal_valid = bool(gate_passed)
            validation_info: dict[str, Any] = {}
            if gate_passed and hasattr(self.strategy, "validate_signal"):
                signal_valid, validation_info = self.strategy.validate_signal(
                    views["1h"], views["15m"], views["5m"], gate_result
                )
                score_result = validation_info.get("score_result", score_result)
            elif gate_passed:
                score_result = {"total_score": 100.0, "recommendation": "strong"}
            breakdown = {
                "gate_passed": bool(gate_passed),
                "gate_result": gate_result,
                "signal_valid": bool(signal_valid),
                "validation_info": validation_info,
                "score_result": score_result,
                "should_trade": bool(gate_passed and signal_valid),
                "entry_quality": "fixture_approved" if gate_passed and signal_valid else "rejected_by_gates",
                "direction": direction,
            }

        breakdown["direction"] = direction
        # Preserve the existing bot formula exactly; only the timestamp and
        # immutable context are corrected for Version B reproducibility.
        signal_data = breakdown.get("gate_result", {})
        score = breakdown.get("score_result", {}).get("total_score", 0)
        gate_strength = signal_data.get("gate_strength", 0.0)
        breakdown["effective_score"] = round(score * (0.7 + 0.3 * gate_strength), 2)
        gate = breakdown.get("gate_result", {})
        entry_price = float(gate.get("entry_price", 0.0) or 0.0)
        atr = float(gate.get("atr", 0.0) or 0.0)
        breakdown["stops"] = self.risk_engine.calculate_stops(
            side=direction,
            entry_price=entry_price,
            atr=atr,
        ) if entry_price > 0 and atr > 0 else {"valid": False, "reason": "missing entry/ATR"}
        breakdown["timestamp"] = aligned.decision_time.isoformat()
        breakdown["decision_context"] = {
            "symbol": symbol,
            "decision_time": aligned.decision_time.isoformat(),
            "timeframe_timestamps": {
                timeframe: quality.latest_close.isoformat()
                for timeframe, quality in aligned.quality.items()
                if quality.latest_close is not None
            },
            "config_hash": config_hash,
            "strategy_version": getattr(self.strategy, "version", "compatibility-fixture"),
        }
        return breakdown

    def execution_plan(
        self,
        decision: dict[str, Any],
        *,
        equity: float,
        entry_price: float,
        atr: float,
    ) -> dict[str, Any]:
        """Convert an approved StrategyCore decision into one canonical risk plan."""
        if not decision.get("should_trade", False):
            raise ValueError("cannot create an execution plan for a rejected decision")
        direction = decision.get("direction", "long")
        stops = self.risk_engine.calculate_stops(
            side=direction,
            entry_price=entry_price,
            atr=atr,
        )
        score = float(decision.get("effective_score", 0.0))
        size = self.risk_engine.calculate_position_size(
            equity=equity,
            entry_price=entry_price,
            stop_price=float(stops["stop_loss"]),
            side=direction,
            score=score,
        ) if stops.get("valid", False) else None
        return {"stops": stops, "position_size": size}
