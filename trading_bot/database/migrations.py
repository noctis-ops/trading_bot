"""Version B database bootstrap/migration entry point."""

from __future__ import annotations

from pathlib import Path

from database.version_b_store import MODELS, VersionBStore


SCHEMA_VERSION = "vb-1"


def initialize_version_b_database(path: str | Path) -> VersionBStore:
    """Create the Version B schema idempotently and return an open store."""
    return VersionBStore(path)


__all__ = ["MODELS", "SCHEMA_VERSION", "VersionBStore", "initialize_version_b_database"]
