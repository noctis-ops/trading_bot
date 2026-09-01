"""Data package with lazy loading for pure Version B alignment contracts."""

from __future__ import annotations

from typing import Any


__all__ = ["MarketData"]


def __getattr__(name: str) -> Any:
    if name == "MarketData":
        from .market_data import MarketData
        return MarketData
    raise AttributeError(name)
