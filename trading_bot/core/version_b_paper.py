"""Deterministic Version B Paper — the operational rehearsal of the Live path.

Two things live here:

``VersionBPaperEngine``
    The replay engine with ``environment="paper"``.  It shares every decision,
    sizing, and execution formula with B Backtest; the label is metadata.

``VersionBPaperPipeline``
    The operational wrapper.  It owns a durable :class:`VersionBStore`, so a
    Paper trade is a database row rather than a dictionary, and it can resume
    after a restart using nothing but those rows.  This is the piece that makes
    Paper a rehearsal of the path Live will use instead of a memory-only
    simulation that happens to share a class name.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.risk_engine import RiskEngine
from core.strategy_core import StrategyCore
from core.version_b_recovery import UnifiedRecoveryReport, recover_unified_state
from core.version_b_replay import VersionBReplayEngine


def _json_state(state: Any) -> str:
    return json.dumps(state, sort_keys=True, separators=(",", ":"), default=str)


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


class VersionBPaperPipeline:
    """One durable Paper run over the unified Version B path.

    Data → StrategyCore → RiskEngine → ExecutionService → TradeLifecycle →
    VersionBStore.  No in-memory exchange and no legacy ``OrderManager`` are
    involved; a restart reconstructs state from the store alone.
    """

    def __init__(
        self,
        *,
        db_path: str | Path,
        run_id: str | None = None,
        initial_balance: float = 10_000.0,
        strategy: Any | None = None,
        strategy_core: StrategyCore | None = None,
        fee_rate: float | None = None,
        slippage_rate: float | None = None,
        resume: bool = False,
    ):
        from database.version_b_store import RunRecord, VersionBStore

        self.db_path = Path(db_path)
        self.store = VersionBStore(self.db_path)
        resuming = resume and run_id is not None
        if resuming:
            if RunRecord.get_or_none(RunRecord.run_id == run_id) is None:
                raise ValueError(f"cannot resume unknown run: {run_id}")
            stored_balance = self.store.run_initial_balance(run_id)
            if stored_balance is None:
                # Refuse rather than silently resume on an assumed balance:
                # margin and equity would be rebuilt against the wrong base.
                raise ValueError(f"run {run_id} has no durable initial_balance")
            initial_balance = stored_balance
            stored_state = self.store.operational_state(run_id)
            # The rates a run was executed with are part of that run.  Reading
            # config.yaml here instead would change the cost model of an
            # already-open trade without recording anything.
            for key, current in (("fee_rate", fee_rate), ("slippage_rate", slippage_rate)):
                stored = stored_state.get(key)
                if stored is None:
                    raise ValueError(f"run {run_id} has no durable {key}")
                if current is not None and float(current) != float(stored):
                    raise ValueError(
                        f"run {run_id} executed with {key}={stored}; refusing to resume with {current}"
                    )
                if key == "fee_rate":
                    fee_rate = float(stored)
                else:
                    slippage_rate = float(stored)
        self.run_id = run_id or f"paper-{uuid.uuid4().hex[:12]}"
        self.engine = VersionBPaperEngine(
            initial_balance=initial_balance,
            strategy=strategy,
            strategy_core=strategy_core,
            fee_rate=fee_rate,
            slippage_rate=slippage_rate,
            store=self.store,
            run_id=self.run_id,
        )
        self.service = self.engine.service
        self.resumed = resuming
        self.recovery_report: UnifiedRecoveryReport | None = None
        if not resuming:
            # The engine has already written the immutable run header; these two
            # fields are operational state, so they are added rather than
            # duplicated in a second create_run.
            self.store.update_run(
                self.run_id,
                initial_balance=float(self.engine.initial_balance),
                operational_state_json=_json_state(self.operational_state()),
            )
        else:
            self.recovery_report = self.recover()

    # ── operational state ─────────────────────────────────────────────────

    def operational_state(self) -> dict[str, Any]:
        """The snapshot a restart must be able to reproduce from rows alone."""
        positions = []
        for symbol, position in self.service.positions.items():
            positions.append({
                "symbol": symbol,
                "trade_id": position.lifecycle.trade_id,
                "state": position.lifecycle.state.value,
                "side": position.side,
                "initial_quantity": position.initial_quantity,
                "remaining_quantity": position.remaining_quantity,
                "stop_loss": position.stop_loss,
                "take_profit_1": position.take_profit_1,
                "take_profit_2": position.take_profit_2,
                "tp1_hit": position.tp1_hit,
                "sl_moved_to_be": position.sl_moved_to_be,
                "margin_locked": position.margin_locked,
                "leverage": position.leverage,
            })
        return {
            "run_id": self.run_id,
            "environment": self.engine.environment,
            "initial_balance": self.engine.initial_balance,
            "fee_rate": self.service.fee_rate,
            "slippage_rate": self.service.slippage_rate,
            "balance": self.service.balance,
            "margin_locked": sum(p.margin_locked for p in self.service.positions.values()),
            "open_position_count": len(self.service.positions),
            "closed_trade_count": len(self.service.closed_lifecycles),
            "signal_count": len(self.engine.signals),
            "decision_count": len(self.engine.decisions),
            "positions": sorted(positions, key=lambda item: item["trade_id"]),
            "captured_at": datetime.now(timezone.utc).isoformat(),
        }

    def persist_state(self) -> dict[str, Any]:
        state = self.operational_state()
        self.store.record_operational_state(self.run_id, state)
        return state

    # ── restart ───────────────────────────────────────────────────────────

    def recover(self) -> UnifiedRecoveryReport:
        """Rebuild open positions from the database alone."""
        return recover_unified_state(self.store, self.run_id, self.service)

    def state_matches_durable_snapshot(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        """Compare post-restart state to the snapshot persisted before the crash.

        Equality here is the actual proof that the database was sufficient: the
        rebuilt balance, margin, TP1/breakeven flags, and remaining quantity are
        the pre-crash values without any of them having been carried in memory.
        """
        rebuilt = self.operational_state()
        keys = (
            "initial_balance", "balance", "margin_locked", "open_position_count",
            "closed_trade_count", "positions",
        )
        differences = {
            key: {"expected": snapshot.get(key), "actual": rebuilt.get(key)}
            for key in keys
            if snapshot.get(key) != rebuilt.get(key)
        }
        return {"matches": not differences, "differences": differences}

    # ── execution ─────────────────────────────────────────────────────────

    def run(self, df_1h, df_15m, df_5m, *, symbol: str = "BTC/USDT", direction: str = "long"):
        report = self.engine.run(df_1h, df_15m, df_5m, symbol=symbol, direction=direction)
        self.persist_state()
        return report

    def close(self) -> None:
        self.store.close()

    def __enter__(self) -> "VersionBPaperPipeline":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


__all__ = ["VersionBPaperEngine", "VersionBPaperPipeline"]
