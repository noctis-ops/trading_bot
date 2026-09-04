"""Reproducible measurement lineage for Version B.

A measurement that cannot be tied to a code identity, a data identity, and an
execution-model identity is not reproducible and must not be accepted as a
baseline.  This module resolves those three identities.  It reads nothing about
strategy rules and changes no parameter.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd


UNRESOLVED = "UNRESOLVED"
CODE_IDENTITY_VERSION = "vb-code-identity-1"
DATA_HASH_ALGORITHM = "sha256-frames-v1"
LINEAGE_SCHEMA = "vb-lineage-1"

_REQUIRED_LINEAGE_KEYS = (
    "lineage_schema",
    "code",
    "data",
    "config",
    "execution_model",
    "strategy",
    "run_scope",
)
_REQUIRED_CODE_KEYS = ("commit", "dirty", "branch", "identity_version")


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), text=True, capture_output=True, check=False
    )


def resolve_code_identity(repo_root: str | Path | None = None) -> dict[str, Any]:
    """Resolve the commit the measurement was produced from.

    Returns ``UNRESOLVED`` when git is unavailable.  It never fabricates a
    placeholder such as ``working-tree``, because a placeholder silently makes
    an unreproducible run look reproducible.
    """
    root = Path(repo_root) if repo_root is not None else Path(__file__).resolve().parents[2]
    commit = _git(["rev-parse", "HEAD"], root)
    if commit.returncode != 0:
        return {
            "identity_version": CODE_IDENTITY_VERSION,
            "commit": UNRESOLVED,
            "dirty": None,
            "branch": UNRESOLVED,
            "reason": commit.stderr.strip() or "git unavailable",
        }
    status = _git(["status", "--porcelain"], root)
    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], root)
    return {
        "identity_version": CODE_IDENTITY_VERSION,
        "commit": commit.stdout.strip(),
        # A dirty tree means the measurement is not reproducible from the commit.
        "dirty": bool(status.stdout.strip()) if status.returncode == 0 else None,
        "branch": branch.stdout.strip() or UNRESOLVED,
    }


def _frame_digest(name: str, frame: pd.DataFrame) -> bytes:
    """Canonical bytes for one frame: shape, columns, index, then values."""
    digest = hashlib.sha256()
    digest.update(name.encode("utf-8"))
    digest.update(str(frame.shape[0]).encode("utf-8"))
    digest.update(str(frame.shape[1]).encode("utf-8"))
    digest.update(",".join(str(column) for column in frame.columns).encode("utf-8"))
    index = frame.index
    if isinstance(index, pd.DatetimeIndex):
        digest.update(index.asi8.tobytes())
    else:
        try:
            digest.update(np.ascontiguousarray(index.to_numpy(dtype="int64")).tobytes())
        except (TypeError, ValueError):
            digest.update(str(list(index)).encode("utf-8"))
    for column in frame.columns:
        digest.update(str(column).encode("utf-8"))
        values = frame[column].to_numpy()
        if np.issubdtype(values.dtype, np.number):
            payload = np.ascontiguousarray(values, dtype="float64")
            digest.update(np.nan_to_num(payload, nan=np.nan).tobytes())
        else:
            digest.update(str(list(values)).encode("utf-8"))
    return digest.digest()


def hash_frames(frames: Mapping[str, pd.DataFrame]) -> str:
    """Deterministic identity for the exact frames a measurement consumed."""
    digest = hashlib.sha256()
    digest.update(DATA_HASH_ALGORITHM.encode("utf-8"))
    for name in sorted(frames):
        frame = frames[name]
        if not isinstance(frame, pd.DataFrame):
            raise TypeError(f"frame {name!r} must be a pandas DataFrame")
        digest.update(_frame_digest(name, frame))
    return digest.hexdigest()


def resolve_config_identity() -> dict[str, Any]:
    """Config identity from the runtime snapshot, never a hardcoded literal."""
    try:
        from core.runtime_config import load_runtime_config

        runtime = load_runtime_config()
        return {
            "config_sha256": getattr(runtime, "snapshot_sha256", UNRESOLVED),
            "execution_model_version": runtime.execution_model_version,
            "snapshot": runtime.snapshot(),
        }
    except Exception as exc:  # pragma: no cover - defensive
        return {
            "config_sha256": UNRESOLVED,
            "execution_model_version": UNRESOLVED,
            "snapshot": {},
            "reason": str(exc),
        }


def build_lineage(
    *,
    code: Mapping[str, Any] | None = None,
    data_hash: str,
    frame_row_counts: Mapping[str, int] | None = None,
    config: Mapping[str, Any] | None = None,
    execution_model_version: str,
    strategy_version: str,
    symbol: str,
    direction: str,
    initial_balance: float,
    universe: list[str] | None = None,
    timeframes: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Assemble the lineage block required by the baseline artifact contract."""
    resolved_config = dict(config or resolve_config_identity())
    return {
        "lineage_schema": LINEAGE_SCHEMA,
        "code": dict(code or resolve_code_identity()),
        "data": {
            "data_hash_algorithm": DATA_HASH_ALGORITHM,
            "data_hash": data_hash,
            "frame_row_counts": dict(frame_row_counts or {}),
        },
        "config": {
            "config_sha256": resolved_config.get("config_sha256", UNRESOLVED),
        },
        "execution_model": {"execution_model_version": execution_model_version},
        "strategy": {"strategy_version": strategy_version},
        "run_scope": {
            "symbol": symbol,
            "direction": direction,
            "initial_balance": float(initial_balance),
            "universe": list(universe or [symbol]),
            "timeframes": dict(timeframes or {}),
        },
    }


def validate_lineage(lineage: Mapping[str, Any], *, require_clean_tree: bool = True) -> None:
    """Reject lineage that cannot reproduce the measurement."""
    for key in _REQUIRED_LINEAGE_KEYS:
        if key not in lineage:
            raise ValueError(f"lineage missing required key: {key}")
    code = lineage["code"]
    for key in _REQUIRED_CODE_KEYS:
        if key not in code:
            raise ValueError(f"lineage code identity missing: {key}")
    if code["commit"] == UNRESOLVED:
        raise ValueError("code identity is unresolved; the run is not reproducible")
    if require_clean_tree and code["dirty"] is not False:
        raise ValueError(
            "code tree is dirty or dirtiness is unknown; the measurement is not "
            "reproducible from the recorded commit"
        )
    data_hash = lineage["data"].get("data_hash")
    if not data_hash or data_hash == UNRESOLVED:
        raise ValueError("data_hash is required")
    if len(str(data_hash)) != 64 or any(c not in "0123456789abcdef" for c in str(data_hash)):
        raise ValueError("data_hash must be a 64-character lowercase sha256 hex digest")
    if lineage["config"].get("config_sha256") in (None, "", UNRESOLVED, "UNSPECIFIED"):
        raise ValueError("config identity is required")
    if not lineage["execution_model"].get("execution_model_version"):
        raise ValueError("execution model identity is required")
