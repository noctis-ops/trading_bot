"""Baseline artifact contract for Version B.

This module makes the Type-1 / Type-2 boundary enforceable instead of
documentary.  A ``STRATEGY_MODEL_BASELINE`` artifact must carry its lineage,
must use the pinned metric keys, and must not carry a profitability verdict.
Anything that looks like an operational result is rejected at build time and
again at validation time.

Nothing here collects data, runs a strategy, or changes a parameter.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from core.measurement_lineage import UNRESOLVED, validate_lineage
from core.measurement_metrics import (
    EQUITY_CURVE_DEFINITION,
    MAX_DRAWDOWN_DEFINITION,
    MODEL_METRIC_KEYS,
)


ARTIFACT_SCHEMA = "vb-baseline-1"

# Type 1 — the frozen decision + execution model replayed over history.
STRATEGY_MODEL_BASELINE = "STRATEGY_MODEL_BASELINE"
TYPE_1_STRATEGY_MODEL = "TYPE_1_STRATEGY_MODEL"

# Type 2 — the operational system against a real venue.  Never produced here.
OPERATIONAL_PERFORMANCE = "OPERATIONAL_PERFORMANCE"
TYPE_2_OPERATIONAL = "TYPE_2_OPERATIONAL"

INTERPRETATION_BANNER = (
    "STRATEGY_MODEL_BASELINE measures the frozen decision and execution model "
    "replayed over historical OHLCV. It is not an operational or live result, "
    "not a forecast, and carries no profitability verdict. Funding is modeled as "
    "0.0, costs are configured model constants, and order rejection, partial "
    "fills, timeouts, unknown order state, disconnects, latency, and "
    "exchange-side rounding do not exist in this measurement."
)

# Keys that belong to an operational verdict.  Their presence in a Type-1
# artifact is the exact confusion this contract exists to prevent.
FORBIDDEN_TYPE_1_KEYS = frozenset({
    "final_verdict",
    "legacy_operational_verdict",
    "verdict",
    "win_rate",
    "win_rate_min",
    "profit_factor",
    "profit_factor_min",
    "max_drawdown_pct",
    "max_drawdown_max",
    "total_pnl",
    "pnl_min",
    "enough_trades",
    "criteria",
    "status_verdict",
})

FORBIDDEN_TYPE_1_TERMS = ("performance", "operational", "live", "expected return")

BASELINE_FILENAME_PATTERN = re.compile(
    r"^baseline_strategymodel_[A-Z0-9]+_(?:long|short)_vb-[0-9.]+-[a-z0-9-]+_[0-9a-f]{12}\.json$"
)

_REQUIRED_TOP_LEVEL_KEYS = (
    "artifact_schema",
    "report_type",
    "measurement_scope",
    "not_an_operational_result",
    "interpretation_banner",
    "lineage",
    "execution_model_version",
    "metric_definitions",
    "metrics",
    "profitability_verdict",
    "non_deterministic_fields",
    "generated_at",
)

# Fields that legitimately differ between two runs of identical inputs.  The
# reproducibility check hashes the artifact with exactly these removed.
ALLOWED_NON_DETERMINISTIC_FIELDS = ("generated_at",)


class BaselineArtifactError(ValueError):
    """Raised when an artifact cannot be accepted as a Version B baseline."""


def _reject_forbidden_keys(mapping: Mapping[str, Any], where: str) -> None:
    found = sorted(FORBIDDEN_TYPE_1_KEYS.intersection(mapping.keys()))
    if found:
        raise BaselineArtifactError(
            f"{where} contains operational-verdict keys {found}; a "
            f"{STRATEGY_MODEL_BASELINE} artifact must not carry them"
        )


def build_baseline_artifact(
    *,
    metrics: Mapping[str, Any],
    lineage: Mapping[str, Any],
    execution_model_version: str,
    counts: Mapping[str, Any] | None = None,
    generated_at: datetime | None = None,
    require_clean_tree: bool = True,
) -> dict[str, Any]:
    """Assemble and validate a Type-1 baseline artifact."""
    unknown = sorted(set(metrics) - set(MODEL_METRIC_KEYS))
    if unknown:
        raise BaselineArtifactError(
            f"metrics contain keys outside the pinned set: {unknown}"
        )
    missing = [key for key in MODEL_METRIC_KEYS if key not in metrics]
    if missing:
        raise BaselineArtifactError(f"metrics missing pinned keys: {missing}")
    validate_lineage(lineage, require_clean_tree=require_clean_tree)
    artifact = {
        "artifact_schema": ARTIFACT_SCHEMA,
        "report_type": STRATEGY_MODEL_BASELINE,
        "measurement_scope": TYPE_1_STRATEGY_MODEL,
        "not_an_operational_result": True,
        "interpretation_banner": INTERPRETATION_BANNER,
        "lineage": json.loads(json.dumps(lineage, default=str, sort_keys=True)),
        "execution_model_version": execution_model_version,
        "metric_definitions": {
            "equity_curve": EQUITY_CURVE_DEFINITION,
            "max_drawdown": MAX_DRAWDOWN_DEFINITION,
            "funding": "modeled as exactly 0.0; not observed funding",
            "costs": "configured model constants; not observed fills",
        },
        "metrics": json.loads(json.dumps(metrics, default=str, sort_keys=True)),
        "counts": json.loads(json.dumps(dict(counts or {}), default=str, sort_keys=True)),
        # Explicitly absent by contract; a Type-1 artifact never scores profit.
        "profitability_verdict": None,
        "non_deterministic_fields": list(ALLOWED_NON_DETERMINISTIC_FIELDS),
        "generated_at": (generated_at or datetime.now(timezone.utc)).isoformat(),
    }
    validate_baseline_artifact(artifact, require_clean_tree=require_clean_tree)
    return artifact


def baseline_artifact_filename(artifact: Mapping[str, Any]) -> str:
    """Self-labelling file name; it cannot be mistaken for an operational report."""
    lineage = artifact["lineage"]
    scope = lineage["run_scope"]
    data_hash = str(lineage["data"]["data_hash"])[:12]
    symbol = str(scope["symbol"]).replace("/", "").upper()
    name = (
        f"baseline_strategymodel_{symbol}_{scope['direction']}_"
        f"{artifact['execution_model_version']}_{data_hash}.json"
    )
    if not BASELINE_FILENAME_PATTERN.match(name):
        raise BaselineArtifactError(f"generated artifact name is invalid: {name}")
    return name


def validate_baseline_artifact(
    artifact: Mapping[str, Any], *, require_clean_tree: bool = True
) -> None:
    """Enforce the full contract.  Raises ``BaselineArtifactError``."""
    for key in _REQUIRED_TOP_LEVEL_KEYS:
        if key not in artifact:
            raise BaselineArtifactError(f"artifact missing required key: {key}")
    if artifact["artifact_schema"] != ARTIFACT_SCHEMA:
        raise BaselineArtifactError(f"unsupported artifact schema: {artifact['artifact_schema']}")
    if artifact["report_type"] != STRATEGY_MODEL_BASELINE:
        raise BaselineArtifactError(
            f"report_type must be {STRATEGY_MODEL_BASELINE}, got {artifact['report_type']!r}"
        )
    if artifact["measurement_scope"] != TYPE_1_STRATEGY_MODEL:
        raise BaselineArtifactError(
            f"measurement_scope must be {TYPE_1_STRATEGY_MODEL}, "
            f"got {artifact['measurement_scope']!r}"
        )
    if artifact["not_an_operational_result"] is not True:
        raise BaselineArtifactError("not_an_operational_result must be true")
    if artifact["profitability_verdict"] is not None:
        raise BaselineArtifactError(
            "a Type-1 baseline carries no profitability verdict; scoring a model "
            "against operational targets creates a tuning incentive"
        )
    _reject_forbidden_keys(artifact, "artifact")
    _reject_forbidden_keys(artifact["metrics"], "metrics")
    _reject_forbidden_keys(artifact["counts"], "counts")
    unknown = sorted(set(artifact["metrics"]) - set(MODEL_METRIC_KEYS))
    if unknown:
        raise BaselineArtifactError(f"metrics contain unpinned keys: {unknown}")
    try:
        validate_lineage(artifact["lineage"], require_clean_tree=require_clean_tree)
    except ValueError as exc:
        raise BaselineArtifactError(str(exc)) from exc
    if artifact["execution_model_version"] in (None, "", UNRESOLVED):
        raise BaselineArtifactError("execution_model_version is required")
    scope = artifact["lineage"]["run_scope"]
    universe = list(scope.get("universe") or [])
    if universe != [scope.get("symbol")]:
        raise BaselineArtifactError(
            f"a per-symbol baseline artifact must declare universe == [symbol]; got "
            f"universe={universe} for symbol={scope.get('symbol')!r}. Aggregated "
            "results are a different measurement and need their own contract."
        )
    if artifact["execution_model_version"] != artifact["lineage"]["execution_model"][
        "execution_model_version"
    ]:
        raise BaselineArtifactError("execution_model_version disagrees with the lineage")
    declared = list(artifact["non_deterministic_fields"])
    unexpected = [field for field in declared if field not in ALLOWED_NON_DETERMINISTIC_FIELDS]
    if unexpected:
        raise BaselineArtifactError(
            f"non_deterministic_fields {unexpected} are not permitted; reproducibility "
            "must not be waived by declaration"
        )
    baseline_artifact_filename(artifact)


def deterministic_payload(
    artifact: Mapping[str, Any], *, require_clean_tree: bool = True
) -> dict[str, Any]:
    """Artifact with the declared non-deterministic fields removed."""
    validate_baseline_artifact(artifact, require_clean_tree=require_clean_tree)
    payload = json.loads(json.dumps(artifact, sort_keys=True, default=str))
    for field in artifact["non_deterministic_fields"]:
        payload.pop(field, None)
    return payload


def artifact_fingerprint(
    artifact: Mapping[str, Any], *, require_clean_tree: bool = True
) -> str:
    """Stable hash of everything that must reproduce between two runs."""
    import hashlib

    payload = json.dumps(
        deterministic_payload(artifact, require_clean_tree=require_clean_tree),
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def write_baseline_artifact(artifact: Mapping[str, Any], directory: str | Path) -> Path:
    validate_baseline_artifact(artifact)
    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    path = target / baseline_artifact_filename(artifact)
    path.write_text(json.dumps(artifact, indent=2, sort_keys=True, default=str), encoding="utf-8")
    return path


def assert_not_type_1(artifact: Mapping[str, Any]) -> None:
    """Guard used by operational reports: refuse Version B baseline shapes."""
    if artifact.get("report_type") == STRATEGY_MODEL_BASELINE:
        raise BaselineArtifactError(
            "this is a STRATEGY_MODEL_BASELINE artifact and must not be processed "
            "by an operational report"
        )
