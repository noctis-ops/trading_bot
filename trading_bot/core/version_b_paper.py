"""Deterministic Version B Paper adapter sharing the Backtest replay path."""

from __future__ import annotations

from typing import Any

from core.risk_engine import RiskEngine
from core.strategy_core import StrategyCore
from core.version_b_replay import VersionBReplayEngine


class VersionBPaperEngine(VersionBReplayEngine):
    """Paper replay with identical decision/risk/execution semantics to B Backtest."""

    def __init__(
        self,
        initial_balance: float = 10_000.0,
        *,
        fee_rate: float | None = None,
        slippage_rate: float | None = None,
        strategy_core: StrategyCore | None = None,
        risk_engine: RiskEngine | None = None,
        strategy: Any | None = None,
        store: Any | None = None,
        run_id: str | None = None,
        config_hash: str = "UNSPECIFIED",
    ):
        if strategy_core is None:
            strategy_core = StrategyCore(strategy=strategy, risk_engine=risk_engine)
        elif risk_engine is None:
            risk_engine = strategy_core.risk_engine
        super().__init__(
            initial_balance=initial_balance,
            environment="paper",
            strategy_core=strategy_core,
            risk_engine=risk_engine or strategy_core.risk_engine,
            fee_rate=fee_rate,
            slippage_rate=slippage_rate,
            store=store,
            run_id=run_id,
            config_hash=config_hash,
        )


__all__ = ["VersionBPaperEngine"]
