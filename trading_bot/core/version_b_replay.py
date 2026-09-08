"""One deterministic replay pipeline shared by Version B Backtest and Paper."""

from __future__ import annotations

import json
from typing import Any, Iterable

import pandas as pd

from core.execution_model import EXECUTION_MODEL_VERSION
from core.execution_service import VersionBExecutionService
from core.measurement_lineage import (
    UNRESOLVED,
    build_lineage,
    hash_frames,
    resolve_code_identity,
    resolve_config_identity,
)
from core.measurement_metrics import compute_model_metrics
from core.risk_engine import RiskEngine
from core.strategy_core import StrategyCore
from core.runtime_config import load_runtime_config
from data.time_alignment import align_timeframes, candle_close_time, derive_warmup_requirements


class VersionBReplayEngine:
    """Data → StrategyCore → RiskEngine → Execution → Lifecycle → DB.

    Backtest and deterministic Paper deliberately share this implementation.
    The environment label is metadata only; it does not create a second
    strategy, sizing, or execution formula.
    """

    decision_timeframe = "15m"
    exit_timeframe = "5m"

    def __init__(
        self,
        initial_balance: float = 10_000.0,
        *,
        environment: str = "backtest",
        strategy_core: StrategyCore | None = None,
        risk_engine: RiskEngine | None = None,
        fee_rate: float | None = None,
        slippage_rate: float | None = None,
        store: Any | None = None,
        run_id: str | None = None,
        config_hash: str = "UNSPECIFIED",
    ):
        if (store is None) != (run_id is None):
            raise ValueError("store and run_id must be supplied together")
        self.environment = environment
        self.risk_engine = risk_engine or RiskEngine()
        self.strategy_core = strategy_core or StrategyCore(risk_engine=self.risk_engine)
        if self.strategy_core.risk_engine is not self.risk_engine:
            # StrategyCore and replay must share the same canonical risk object.
            self.strategy_core.risk_engine = self.risk_engine
        configured_execution = getattr(self.risk_engine, "execution", {})
        if fee_rate is None:
            fee_rate = float(configured_execution.get("fee_rate", 0.0004))
        if slippage_rate is None:
            slippage_rate = float(configured_execution.get("slippage_rate", 0.0002))
        move_to_be = bool(
            getattr(self.risk_engine, "risk", {}).get(
                "move_sl_to_breakeven_after_tp1", True
            )
        )
        self.service = VersionBExecutionService(
            initial_balance=initial_balance,
            fee_rate=fee_rate,
            slippage_rate=slippage_rate,
            move_sl_to_breakeven=move_to_be,
            risk_engine=self.risk_engine,
            store=store,
            run_id=run_id,
        )
        self.initial_balance = float(initial_balance)
        self.store = store
        self.run_id = run_id
        # A measurement without code and config identity is not reproducible, so
        # the placeholder default is resolved rather than recorded verbatim.
        self.code_identity = resolve_code_identity()
        if config_hash in (None, "", "UNSPECIFIED"):
            config_hash = resolve_config_identity().get("config_sha256", "UNSPECIFIED")
        self.config_hash = config_hash
        self.data_hash: str | None = None
        self.frame_row_counts: dict[str, int] = {}
        if self.store is not None and self.run_id is not None and hasattr(self.store, "create_run"):
            self._ensure_run_metadata()
        self.signals: list[dict[str, Any]] = []
        self.decisions: list[dict[str, Any]] = []
        self.signal_checks = 0
        self.rejected_signals = 0
        self.data_rejections = 0
        self.equity_curve: list[float] = [self.initial_balance]
        self.timestamps: list[pd.Timestamp] = []

    def _ensure_run_metadata(self) -> None:
        """Create the immutable run header when a caller did not pre-create it."""
        try:
            from database.version_b_store import RunRecord

            if RunRecord.get_or_none(RunRecord.run_id == self.run_id) is not None:
                return
            config = self.risk_engine.config
            snapshot = config.snapshot() if hasattr(config, "snapshot") else {}
            self.store.create_run(
                run_id=self.run_id,
                environment=self.environment,
                code_version=self.code_identity.get("commit", UNRESOLVED),
                strategy_version=getattr(self.strategy_core.strategy, "version", "compatibility-fixture"),
                config_hash=self.config_hash,
                data_hash=None,
                execution_model_version=EXECUTION_MODEL_VERSION,
                universe=[],
                effective_config=snapshot,
            )
        except Exception as exc:
            # A custom test store may deliberately expose only event methods;
            # it should not make the deterministic replay path unusable.
            if self.store.__class__.__module__.startswith("database."):
                raise

    def measurement_lineage(self, *, symbol: str, direction: str) -> dict[str, Any]:
        """Lineage block for the baseline artifact contract.

        Requires a completed ``run`` so the data identity is the identity of the
        frames actually consumed.
        """
        if not self.data_hash:
            raise ValueError("measurement_lineage requires a completed run")
        return build_lineage(
            code=self.code_identity,
            data_hash=self.data_hash,
            frame_row_counts=self.frame_row_counts,
            config={"config_sha256": self.config_hash},
            execution_model_version=EXECUTION_MODEL_VERSION,
            strategy_version=getattr(self.strategy_core.strategy, "version", UNRESOLVED),
            symbol=symbol,
            direction=direction,
            initial_balance=self.initial_balance,
            universe=[symbol],
            timeframes={"decision": self.decision_timeframe, "exit": self.exit_timeframe},
        )

    @staticmethod
    def _normalise(*frames: pd.DataFrame) -> tuple[pd.DataFrame, ...]:
        normalized: list[pd.DataFrame] = []
        for frame in frames:
            if frame is None:
                normalized.append(pd.DataFrame())
                continue
            df = frame.copy()
            if "timestamp" in df.columns:
                df = df.set_index("timestamp")
            df.index = pd.DatetimeIndex(pd.to_datetime(df.index, utc=True))
            normalized.append(df[~df.index.duplicated(keep="last")].sort_index())
        return tuple(normalized)

    @staticmethod
    def _bars_between(
        df: pd.DataFrame,
        start: pd.Timestamp | None,
        end: pd.Timestamp,
    ) -> Iterable[tuple[pd.Timestamp, pd.Series]]:
        if start is None:
            selected = df.loc[df.index < end]
        else:
            # The bar at the entry boundary belongs to the position after the
            # fill and is consumed on the next decision cycle.
            selected = df.loc[(df.index >= start) & (df.index < end)]
        return selected.iterrows()

    @staticmethod
    def _signal_id(run_id: str | None, symbol: str, direction: str, timestamp: pd.Timestamp) -> str:
        prefix = run_id or "replay"
        return f"{prefix}:{symbol}:{direction}:{timestamp.isoformat()}"

    def _persist_decision(
        self,
        *,
        symbol: str,
        direction: str,
        timestamp: pd.Timestamp,
        aligned,
        outcome: str,
        reason: str,
        snapshot: dict[str, Any],
    ) -> None:
        decision = {
            "signal_id": self._signal_id(self.run_id, symbol, direction, timestamp),
            "symbol": symbol,
            "direction": direction,
            "decision_time": timestamp.isoformat(),
            "outcome": outcome,
            "reason": reason,
            "snapshot": snapshot,
            "execution_model_version": EXECUTION_MODEL_VERSION,
        }
        self.decisions.append(decision)
        if self.store is None or self.run_id is None:
            return
        self.store.record_decision(
            signal_id=decision["signal_id"],
            run_id=self.run_id,
            symbol=symbol,
            direction=direction,
            decision_time=timestamp.to_pydatetime(),
            timeframe_timestamps={
                timeframe: quality.latest_close.isoformat() if quality.latest_close is not None else None
                for timeframe, quality in aligned.quality.items()
            },
            outcome=outcome,
            reason=reason,
            snapshot=snapshot,
            strategy_version=getattr(self.strategy_core.strategy, "version", "compatibility-fixture"),
            config_hash=self.config_hash,
        )

    # The operational runtime drives these same two steps one event at a time.
    # They are exposed publicly so event-driven Paper and batch replay share
    # one implementation instead of two that must be kept identical by hand.
    def persist_decision(self, **kwargs: Any) -> None:
        self._persist_decision(**kwargs)

    def open_from_decision(self, **kwargs: Any) -> dict[str, Any] | None:
        return self._open_from_decision(**kwargs)

    def _open_from_decision(
        self,
        *,
        symbol: str,
        direction: str,
        decision: dict[str, Any],
        decision_row: pd.Series,
        timestamp: pd.Timestamp,
    ) -> dict[str, Any] | None:
        if not decision.get("should_trade", False):
            return None
        gate = decision.get("gate_result", {})
        entry_price = float(decision_row["open"])
        atr = float(gate.get("atr", 0.0) or 0.0)
        plan = self.strategy_core.execution_plan(
            decision,
            equity=self.service.equity(),
            entry_price=entry_price,
            atr=atr,
        )
        if not plan["stops"].get("valid", False):
            return None
        size = plan["position_size"]
        signal_id = self._signal_id(self.run_id, symbol, direction, timestamp)
        try:
            position = self.service.open_position(
                symbol=symbol,
                side=direction,
                entry_price=entry_price,
                quantity=size.quantity,
                stop_loss=float(plan["stops"]["stop_loss"]),
                take_profit_1=float(plan["stops"]["take_profit_1"]),
                take_profit_2=float(plan["stops"]["take_profit_2"]),
                leverage=size.leverage,
                signal_id=signal_id if self.run_id else None,
                event_time=timestamp.to_pydatetime(),
            )
        except ValueError:
            return None
        accepted = {
            "signal_id": signal_id,
            "trade_id": position.lifecycle.trade_id,
            "symbol": symbol,
            "side": direction,
            "decision_time": timestamp.isoformat(),
            "signal_entry_price": float(gate.get("entry_price", entry_price)),
            "entry_price": entry_price,
            "atr": atr,
            "score": decision.get("score_result", {}).get("total_score", 0.0),
            "effective_score": decision.get("effective_score", 0.0),
            "stops": plan["stops"],
            "position_size": size.to_dict(),
            "execution_model_version": EXECUTION_MODEL_VERSION,
        }
        self.signals.append(accepted)
        return accepted

    def _trade_rows(self) -> list[dict[str, Any]]:
        rows = []
        exit_types = {
            "TAKE_PROFIT_1", "TAKE_PROFIT_2", "STOP_LOSS", "REVERSAL_EXIT",
            "EMERGENCY_EXIT", "END_OF_DATA", "MANUAL_EXIT",
        }
        for lifecycle in self.service.closed_lifecycles:
            entries = [fill for fill in lifecycle.fills if fill.role == "entry"]
            exits = [fill for fill in lifecycle.fills if fill.role == "exit"]
            if not entries or not exits:
                continue
            entry_quantity = sum(fill.quantity for fill in entries)
            exit_quantity = sum(fill.quantity for fill in exits)
            exit_events = [event for event in lifecycle.events if event.event_type in exit_types]
            rows.append({
                "trade_id": lifecycle.trade_id,
                "symbol": lifecycle.symbol,
                "side": lifecycle.side,
                "entry_price": sum(fill.price * fill.quantity for fill in entries) / entry_quantity,
                "exit_price": sum(fill.price * fill.quantity for fill in exits) / exit_quantity,
                "contract_size": entry_quantity,
                "entry_time": lifecycle.events[0].event_time,
                "exit_time": lifecycle.events[-1].event_time,
                "exit_type": exit_events[-1].event_type if exit_events else "UNKNOWN",
                "profit": lifecycle.realized_net_pnl,
                # Costs are reported separately from net PnL; without these the
                # model_cost_total metric would silently read as zero.
                "fees": sum(fill.fee for fill in lifecycle.fills),
                "slippage": sum(fill.slippage for fill in lifecycle.fills),
                "execution_model_version": EXECUTION_MODEL_VERSION,
                "event_count": len(lifecycle.events),
            })
        return rows

    def run(
        self,
        df_1h: pd.DataFrame,
        df_15m: pd.DataFrame,
        df_5m: pd.DataFrame,
        *,
        symbol: str = "BTC/USDT",
        direction: str = "long",
    ) -> dict[str, Any]:
        if direction not in {"long", "short"}:
            raise ValueError("direction must be long or short")
        if self.store is not None and self.run_id is not None and hasattr(self.store, "update_run"):
            try:
                from database.version_b_store import RunRecord
                record = RunRecord.get_or_none(RunRecord.run_id == self.run_id)
                if record is not None and record.universe_json == "[]":
                    self.store.update_run(self.run_id, universe_json=json.dumps([symbol], separators=(",", ":")))
            except Exception:
                # Custom stores may not expose the relational run model.
                pass
        df_1h, df_15m, df_5m = self._normalise(df_1h, df_15m, df_5m)
        if min(len(df_1h), len(df_15m), len(df_5m)) == 0:
            return {"status": "insufficient_data", "execution_model_version": EXECUTION_MODEL_VERSION}

        # Data identity is part of the measurement: two runs over different
        # frames must not be comparable as the same baseline.
        self.data_hash = hash_frames({"1h": df_1h, "15m": df_15m, "5m": df_5m})
        self.frame_row_counts = {
            "1h": int(len(df_1h)), "15m": int(len(df_15m)), "5m": int(len(df_5m))
        }
        if self.store is not None and self.run_id is not None and hasattr(self.store, "update_run"):
            try:
                self.store.update_run(
                    self.run_id,
                    data_hash=self.data_hash,
                    code_version=self.code_identity.get("commit", UNRESOLVED),
                    config_hash=self.config_hash,
                )
            except Exception:
                # Custom stores may not expose the relational run model.
                pass

        # Small strategy doubles from component tests predate the full
        # TradingStrategy breakdown API.  Keep their adapter contract narrow
        # and explicit; production B remains on the derived active-consumer
        # warm-up (EMA200/indicator consumers).
        legacy_strategy_double = not hasattr(self.strategy_core.strategy, "get_signal_breakdown")
        if legacy_strategy_double:
            compatible_frames = []
            for frame in (df_1h, df_15m, df_5m):
                frame = frame.copy()
                if "volume" not in frame.columns:
                    frame["volume"] = 1.0
                compatible_frames.append(frame)
            df_1h, df_15m, df_5m = compatible_frames
            warmup = {"1h": 21, "15m": 21, "5m": 21}
        else:
            warmup = derive_warmup_requirements(self.risk_engine.config)
        last_exit_boundary: pd.Timestamp | None = None
        five_bar_rows = list(df_5m.iterrows())
        five_cursor = 0
        for index in range(len(df_15m) - 1):
            current_open = df_15m.index[index]
            decision_time = candle_close_time(current_open, self.decision_timeframe)
            decision_row = df_15m.iloc[index + 1]

            # Consume each 5M bar once. This preserves the exact half-open
            # boundary contract without repeatedly slicing the complete frame.
            while five_cursor < len(five_bar_rows) and five_bar_rows[five_cursor][0] < decision_time:
                bar_time, bar = five_bar_rows[five_cursor]
                if last_exit_boundary is None or bar_time >= last_exit_boundary:
                    self.service.process_bar(symbol, bar, event_time=bar_time.to_pydatetime())
                five_cursor += 1
            last_exit_boundary = decision_time

            frames = {"1h": df_1h, "15m": df_15m, "5m": df_5m}
            aligned = align_timeframes(
                frames,
                decision_time=decision_time,
                decision_timeframe=self.decision_timeframe,
                warmup=warmup,
            )
            if not aligned.valid:
                self.data_rejections += 1
                self._persist_decision(
                    symbol=symbol,
                    direction=direction,
                    timestamp=decision_time,
                    aligned=aligned,
                    outcome="DATA_REJECTED",
                    reason=";".join(f"{key}={value.status.value}" for key, value in aligned.quality.items() if value.status.value != "VALID"),
                    snapshot={"quality": {key: value.status.value for key, value in aligned.quality.items()}},
                )
                self.equity_curve.append(self.service.equity({symbol: float(decision_row["close"])}))
                self.timestamps.append(decision_time)
                continue

            self.signal_checks += 1
            decision = self.strategy_core.evaluate(
                symbol,
                aligned,
                direction=direction,
                config_hash=self.config_hash,
            )
            if symbol not in self.service.positions and decision.get("should_trade", False):
                accepted = self._open_from_decision(
                    symbol=symbol,
                    direction=direction,
                    decision=decision,
                    decision_row=decision_row,
                    timestamp=decision_time,
                )
                outcome = "EXECUTED" if accepted else "EXECUTION_REJECTED"
                reason = "SIGNAL_APPROVED" if accepted else "EXECUTION_REJECTED"
                if accepted:
                    snapshot = {"decision": decision, "execution": accepted}
                else:
                    snapshot = {"decision": decision}
            else:
                if decision.get("should_trade", False):
                    outcome = "POSITION_OPEN"
                    reason = "POSITION_ALREADY_OPEN"
                else:
                    outcome = "NO_SIGNAL"
                    reason = decision.get("entry_quality", "NO_SIGNAL")
                snapshot = {"decision": decision}
            if outcome == "NO_SIGNAL":
                self.rejected_signals += 1
            self._persist_decision(
                symbol=symbol,
                direction=direction,
                timestamp=decision_time,
                aligned=aligned,
                outcome=outcome,
                reason=reason,
                snapshot=snapshot,
            )
            self.equity_curve.append(self.service.equity({symbol: float(decision_row["close"])}))
            self.timestamps.append(decision_time)

        if symbol in self.service.positions:
            final_time = df_5m.index[-1]
            self.service.force_close(
                symbol,
                float(df_5m.iloc[-1]["close"]),
                event_time=final_time.to_pydatetime(),
            )
            self.equity_curve.append(self.service.equity())
            self.timestamps.append(final_time)

        trades = self._trade_rows()
        profits = [float(item["profit"]) for item in trades]
        wins = [value for value in profits if value > 0]
        losses = [value for value in profits if value <= 0]
        model_metrics = compute_model_metrics(
            trades=trades,
            equity_curve=self.equity_curve,
            initial_balance=self.initial_balance,
        )
        return {
            "status": "completed",
            "environment": self.environment,
            "symbol": symbol,
            "direction": direction,
            "decision_timeframe": self.decision_timeframe,
            "exit_timeframe": self.exit_timeframe,
            "execution_model_version": EXECUTION_MODEL_VERSION,
            "signal_checks": self.signal_checks,
            "data_rejections": self.data_rejections,
            "accepted_signals": len(self.signals),
            "rejected_signals": self.rejected_signals,
            "total_trades": len(trades),
            "winning_trades": len(wins),
            "losing_trades": len(losses),
            "win_rate": len(wins) / len(trades) * 100 if trades else 0.0,
            "total_profit": sum(profits),
            "final_equity": self.service.equity(),
            "decisions": self.decisions,
            "signals": self.signals,
            "trades": trades,
            "equity_curve": self.equity_curve,
            # Measurement-integrity fields.  A forced end-of-data exit is a
            # property of the chosen window, not a model outcome, so it is
            # reported separately instead of being blended into the totals.
            "model_metrics": model_metrics,
            "model_forced_exit_count": model_metrics["model_forced_exit_count"],
            "equity_curve_max_drawdown_pct": model_metrics["equity_curve_max_drawdown_pct"],
            "initial_balance": self.initial_balance,
            "data_hash": self.data_hash,
            "frame_row_counts": getattr(self, "frame_row_counts", {}),
            "code_identity": self.code_identity,
            "config_hash": self.config_hash,
        }
