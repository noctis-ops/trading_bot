"""Structured decision/rejection taxonomy for Version B."""

from __future__ import annotations

from enum import Enum


class DecisionReason(str, Enum):
    VALID = "VALID"
    NOT_DUE = "NOT_DUE"
    DATA_FAILURE = "DATA_FAILURE"
    STALE_DATA = "STALE_DATA"
    DATA_GAP = "DATA_GAP"
    INVALID_SCHEMA = "INVALID_SCHEMA"
    NO_SIGNAL = "NO_SIGNAL"
    GATE_REJECTED = "GATE_REJECTED"
    SIGNAL_VALIDATION_REJECTED = "SIGNAL_VALIDATION_REJECTED"
    RISK_BLOCK = "RISK_BLOCK"
    CORRELATION_BLOCK = "CORRELATION_BLOCK"
    COOLDOWN = "COOLDOWN"
    DAILY_LOSS_LIMIT = "DAILY_LOSS_LIMIT"
    MAX_POSITIONS = "MAX_POSITIONS"
    INSUFFICIENT_WARMUP = "INSUFFICIENT_WARMUP"
    EXECUTION_FAILURE = "EXECUTION_FAILURE"
    EXECUTION_UNKNOWN = "EXECUTION_UNKNOWN"


class DecisionStage(str, Enum):
    DATA = "DATA"
    STRATEGY = "STRATEGY"
    RISK = "RISK"
    EXECUTION = "EXECUTION"
    PERSISTENCE = "PERSISTENCE"


def taxonomy_record(
    *,
    stage: DecisionStage,
    reason: DecisionReason,
    detail: str = "",
    secondary: tuple[DecisionReason, ...] = (),
) -> dict[str, object]:
    """Return a structured record; callers should not collapse it to SKIP."""
    return {
        "stage": stage.value,
        "reason": reason.value,
        "detail": detail,
        "secondary_reasons": [item.value for item in secondary],
    }
