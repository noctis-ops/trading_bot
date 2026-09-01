"""Version B runtime configuration and reproducibility contracts.

This module is deliberately independent from the trading decision code.  It
provides one validated, immutable view of configuration without activating any
currently dormant parameter (for example ``volume_multiplier``).
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml


DEFAULT_CONFIG_PATH = Path(__file__).parent.parent / "config.yaml"


class ConfigError(ValueError):
    """Raised when configuration cannot be validated safely."""


@dataclass(frozen=True)
class RuntimeMetadata:
    """Deployment values that are not business/strategy parameters."""

    trading_mode: str
    paper_initial_balance: float


@dataclass(frozen=True)
class RuntimeConfig:
    """Immutable effective configuration for one bot run.

    ``declared`` is the exact YAML mapping.  ``effective`` is the resolved
    runtime view used for reproducibility.  Both are retained because a
    declared-but-dormant parameter must remain visible in the audit trail
    without silently becoming active.
    """

    declared: Mapping[str, Any]
    effective: Mapping[str, Any]
    metadata: RuntimeMetadata
    source_path: str
    source_sha256: str
    snapshot_sha256: str

    def snapshot(self) -> dict[str, Any]:
        """Return a JSON-serialisable immutable-run snapshot."""
        return {
            "source_path": self.source_path,
            "source_sha256": self.source_sha256,
            "snapshot_sha256": self.snapshot_sha256,
            "execution_model_version": self.execution_model_version,
            "metadata": {
                "trading_mode": self.metadata.trading_mode,
                "paper_initial_balance": self.metadata.paper_initial_balance,
            },
            "declared": copy.deepcopy(dict(self.declared)),
            "effective": copy.deepcopy(dict(self.effective)),
        }

    @property
    def execution_model_version(self) -> str:
        """The one execution model version used by this runtime snapshot."""
        return str(self.effective["execution"]["model_version"])

    def get(self, *path: str, default: Any = None) -> Any:
        """Read only from the effective configuration using a path."""
        value: Any = self.effective
        for key in path:
            if not isinstance(value, Mapping) or key not in value:
                return default
            value = value[key]
        return value


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_json(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return _sha256_bytes(encoded)


def _required(mapping: Mapping[str, Any], path: tuple[str, ...]) -> Any:
    value: Any = mapping
    for key in path:
        if not isinstance(value, Mapping) or key not in value:
            dotted = ".".join(path)
            raise ConfigError(f"missing required configuration key: {dotted}")
        value = value[key]
    return value


def _validate_declared(config: Mapping[str, Any]) -> None:
    symbols = _required(config, ("trading", "symbols"))
    if not isinstance(symbols, list) or not symbols or not all(isinstance(s, str) and s for s in symbols):
        raise ConfigError("trading.symbols must be a non-empty list of symbols")

    decision_tf = _required(config, ("trading", "main_timeframe"))
    trend_tf = _required(config, ("trading", "trend_timeframe"))
    confirmation_tf = _required(config, ("trading", "confirmation_timeframe"))
    for name, value in (("main_timeframe", decision_tf), ("trend_timeframe", trend_tf), ("confirmation_timeframe", confirmation_tf)):
        if not isinstance(value, str) or not value:
            raise ConfigError(f"trading.{name} must be a non-empty string")

    execution = _required(config, ("execution",))
    if execution.get("model_version") != "vb-1.0-5m-stop-first":
        raise ConfigError("execution.model_version must be vb-1.0-5m-stop-first")
    for key in ("fee_rate", "slippage_rate"):
        value = _required(config, ("execution", key))
        if not isinstance(value, (int, float)) or value < 0:
            raise ConfigError(f"execution.{key} must be non-negative")
    if execution.get("decision_timeframe") != "15m" or execution.get("exit_timeframe") != "5m":
        raise ConfigError("execution clock roles must be decision=15m and exit=5m")

    lookback = _required(config, ("trading", "lookback_period"))
    if not isinstance(lookback, int) or lookback <= 0:
        raise ConfigError("trading.lookback_period must be a positive integer")

    risk = _required(config, ("risk_management",))
    for key in ("risk_percent_per_trade", "max_daily_loss_percent", "max_leverage", "min_risk_reward_ratio"):
        value = _required(config, ("risk_management", key))
        if not isinstance(value, (int, float)) or value <= 0:
            raise ConfigError(f"risk_management.{key} must be positive")
    for key in ("max_consecutive_losses", "cooldown_minutes"):
        value = _required(config, ("risk_management", key))
        if not isinstance(value, int) or value < 0:
            raise ConfigError(f"risk_management.{key} must be a non-negative integer")

    if not isinstance(risk.get("move_sl_to_breakeven_after_tp1"), bool):
        raise ConfigError("risk_management.move_sl_to_breakeven_after_tp1 must be boolean")

    for path in (
        ("strategy", "indicators", "ema_fast"),
        ("strategy", "indicators", "ema_slow"),
        ("strategy", "indicators", "ema_medium"),
        ("strategy", "momentum", "rsi_period"),
        ("strategy", "trend", "adx_period"),
        ("strategy", "volatility", "atr_period"),
        ("strategy", "volume", "volume_ma_period"),
    ):
        value = _required(config, path)
        if not isinstance(value, int) or value <= 0:
            raise ConfigError(f"{'.'.join(path)} must be a positive integer")


def _build_effective(config: Mapping[str, Any], metadata: RuntimeMetadata) -> dict[str, Any]:
    """Build the Version A-compatible effective view.

    The important rule is that dormant YAML values are not activated here.
    The current volume gate remains ``volume > volume_sma`` even though YAML
    declares ``volume_multiplier: 1.5``.
    """
    effective = copy.deepcopy(dict(config))
    effective["runtime"] = {
        "trading_mode": metadata.trading_mode,
        "paper_initial_balance": metadata.paper_initial_balance,
    }
    effective.setdefault("strategy", {}).setdefault("volume", {})[
        "effective_gate"
    ] = "volume > volume_sma"
    effective["strategy"]["volume"]["declared_multiplier_status"] = "dormant; not used by Version A gate"
    # Do not overwrite the declared B execution model.  The Version A
    # historical model is recorded in VERSION_A_MANIFEST.json; it is not a
    # fallback value for the effective Version B runtime snapshot.
    execution = effective.setdefault("execution", {})
    # Validation above guarantees this key exists. Assign the declared value
    # explicitly so a future refactor cannot reintroduce a legacy fallback.
    execution["model_version"] = config["execution"]["model_version"]
    return effective


def load_runtime_config(
    path: str | Path = DEFAULT_CONFIG_PATH,
    *,
    environ: Mapping[str, str] | None = None,
) -> RuntimeConfig:
    """Load and validate configuration once for a run.

    ``environ`` is injectable for tests.  Secrets are never copied into the
    snapshot; only the mode and Paper initial balance are recorded.
    """
    source = Path(path)
    raw_bytes = source.read_bytes()
    declared = yaml.safe_load(raw_bytes.decode("utf-8")) or {}
    if not isinstance(declared, Mapping):
        raise ConfigError("configuration root must be a mapping")
    _validate_declared(declared)

    env = environ if environ is not None else os.environ
    mode = str(env.get("TRADING_MODE", "paper")).strip().lower()
    if mode not in {"paper", "live"}:
        raise ConfigError(f"unsupported TRADING_MODE: {mode!r}")

    try:
        initial_balance = float(env.get("PAPER_INITIAL_BALANCE", "10000"))
    except (TypeError, ValueError) as exc:
        raise ConfigError("PAPER_INITIAL_BALANCE must be numeric") from exc
    if initial_balance <= 0:
        raise ConfigError("PAPER_INITIAL_BALANCE must be positive")

    metadata = RuntimeMetadata(mode, initial_balance)
    effective = _build_effective(declared, metadata)
    snapshot_payload = {
        "declared": declared,
        "effective": effective,
        "metadata": {
            "trading_mode": mode,
            "paper_initial_balance": initial_balance,
        },
        "source_sha256": _sha256_bytes(raw_bytes),
    }
    return RuntimeConfig(
        declared=declared,
        effective=effective,
        metadata=metadata,
        source_path=str(source),
        source_sha256=_sha256_bytes(raw_bytes),
        snapshot_sha256=_sha256_json(snapshot_payload),
    )
