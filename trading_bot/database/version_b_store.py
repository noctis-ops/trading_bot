"""Durable Version B run, decision, lifecycle, order, and fill store."""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

import peewee as pw

from core.risk_model import FillLeg, calculate_realized_net_pnl


DB = pw.SqliteDatabase(None)


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def _dt(value: datetime | None) -> datetime | None:
    """Store UTC as naive SQLite datetimes; UTC is the DB contract."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


class BaseModel(pw.Model):
    class Meta:
        database = DB


class RunRecord(BaseModel):
    run_id = pw.CharField(primary_key=True)
    environment = pw.CharField()
    code_version = pw.CharField()
    strategy_version = pw.CharField()
    config_hash = pw.CharField()
    data_hash = pw.CharField(null=True)
    execution_model_version = pw.CharField()
    universe_json = pw.TextField()
    effective_config_json = pw.TextField()
    created_at = pw.DateTimeField(default=lambda: datetime.now(timezone.utc))

    class Meta:
        table_name = "vb_runs"


class DecisionRecord(BaseModel):
    signal_id = pw.CharField(primary_key=True)
    run = pw.ForeignKeyField(RunRecord, backref="decisions", column_name="run_id", on_delete="CASCADE")
    symbol = pw.CharField(index=True)
    direction = pw.CharField()
    decision_time = pw.DateTimeField(index=True)
    timeframe_timestamps_json = pw.TextField()
    outcome = pw.CharField()
    reason = pw.CharField()
    snapshot_json = pw.TextField()
    strategy_version = pw.CharField()
    config_hash = pw.CharField()

    class Meta:
        table_name = "vb_decisions"
        indexes = (
            (("run", "decision_time"), False),
        )


class TradeLifecycleRecord(BaseModel):
    trade_id = pw.CharField(primary_key=True)
    run = pw.ForeignKeyField(RunRecord, backref="trades", column_name="run_id", on_delete="CASCADE")
    signal_id = pw.CharField(null=True, index=True)
    symbol = pw.CharField(index=True)
    side = pw.CharField()
    state = pw.CharField()
    initial_quantity = pw.FloatField(default=0.0)
    remaining_quantity = pw.FloatField(default=0.0)
    opened_at = pw.DateTimeField(null=True)
    closed_at = pw.DateTimeField(null=True)
    final_exit_reason = pw.CharField(null=True)
    gross_pnl = pw.FloatField(default=0.0)
    fees = pw.FloatField(default=0.0)
    slippage = pw.FloatField(default=0.0)
    funding = pw.FloatField(default=0.0)
    net_pnl = pw.FloatField(null=True)

    class Meta:
        table_name = "vb_trade_lifecycles"


class OrderIntentRecord(BaseModel):
    order_intent_id = pw.CharField(primary_key=True)
    trade = pw.ForeignKeyField(TradeLifecycleRecord, backref="orders", column_name="trade_id", on_delete="CASCADE")
    client_order_id = pw.CharField(unique=True, null=True)
    exchange_order_id = pw.CharField(unique=True, null=True)
    symbol = pw.CharField()
    side = pw.CharField()
    order_type = pw.CharField()
    intended_quantity = pw.FloatField()
    intended_price = pw.FloatField(null=True)
    status = pw.CharField()
    created_at = pw.DateTimeField(default=lambda: datetime.now(timezone.utc))
    confirmed_at = pw.DateTimeField(null=True)

    class Meta:
        table_name = "vb_order_intents"


class LifecycleEventRecord(BaseModel):
    event_id = pw.CharField(primary_key=True)
    trade = pw.ForeignKeyField(TradeLifecycleRecord, backref="events", column_name="trade_id", on_delete="CASCADE")
    sequence = pw.IntegerField()
    event_type = pw.CharField()
    event_time = pw.DateTimeField(index=True)
    payload_json = pw.TextField()

    class Meta:
        table_name = "vb_lifecycle_events"
        indexes = (
            (("trade", "sequence"), True),
        )


class FillRecord(BaseModel):
    fill_id = pw.CharField(primary_key=True)
    trade = pw.ForeignKeyField(TradeLifecycleRecord, backref="fills", column_name="trade_id", on_delete="CASCADE")
    order_intent = pw.ForeignKeyField(OrderIntentRecord, backref="fills", column_name="order_intent_id", null=True, on_delete="SET NULL")
    role = pw.CharField()
    side = pw.CharField()
    fill_time = pw.DateTimeField(index=True)
    price = pw.FloatField()
    quantity = pw.FloatField()
    fee = pw.FloatField(default=0.0)
    slippage = pw.FloatField(default=0.0)
    funding = pw.FloatField(default=0.0)

    class Meta:
        table_name = "vb_fills"


MODELS = [
    RunRecord,
    DecisionRecord,
    TradeLifecycleRecord,
    OrderIntentRecord,
    LifecycleEventRecord,
    FillRecord,
]


class VersionBStore:
    """SQLite-backed store with idempotent identity and event writes."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not DB.is_closed():
            DB.close()
        DB.init(str(self.path), pragmas={
            "journal_mode": "wal",
            "foreign_keys": 1,
            "synchronous": "normal",
        })
        DB.connect(reuse_if_open=True)
        DB.create_tables(MODELS, safe=True)

    def close(self):
        if not DB.is_closed():
            DB.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    def create_run(
        self,
        *,
        run_id: str,
        environment: str,
        code_version: str,
        strategy_version: str,
        config_hash: str,
        data_hash: str | None,
        execution_model_version: str,
        universe: Iterable[str],
        effective_config: Mapping[str, Any],
        created_at: datetime | None = None,
    ) -> RunRecord:
        return RunRecord.create(
            run_id=run_id,
            environment=environment,
            code_version=code_version,
            strategy_version=strategy_version,
            config_hash=config_hash,
            data_hash=data_hash,
            execution_model_version=execution_model_version,
            universe_json=_json(list(universe)),
            effective_config_json=_json(effective_config),
            created_at=_dt(created_at) or datetime.now(timezone.utc),
        )

    def update_run(self, run_id: str, **values: Any) -> RunRecord:
        """Update only run-header metadata that becomes known at execution time."""
        if not values:
            return RunRecord.get_by_id(run_id)
        RunRecord.update(**values).where(RunRecord.run_id == run_id).execute()
        return RunRecord.get_by_id(run_id)

    def record_decision(
        self,
        *,
        signal_id: str,
        run_id: str,
        symbol: str,
        direction: str,
        decision_time: datetime,
        timeframe_timestamps: Mapping[str, Any],
        outcome: str,
        reason: str,
        snapshot: Mapping[str, Any],
        strategy_version: str,
        config_hash: str,
    ) -> DecisionRecord:
        values = dict(
            signal_id=signal_id,
            run=run_id,
            symbol=symbol,
            direction=direction,
            decision_time=_dt(decision_time),
            timeframe_timestamps_json=_json(timeframe_timestamps),
            outcome=outcome,
            reason=reason,
            snapshot_json=_json(snapshot),
            strategy_version=strategy_version,
            config_hash=config_hash,
        )
        existing = DecisionRecord.get_or_none(DecisionRecord.signal_id == signal_id)
        if existing is not None:
            current = {
                "run_id": existing.run_id,
                "symbol": existing.symbol,
                "direction": existing.direction,
                "outcome": existing.outcome,
                "reason": existing.reason,
                "config_hash": existing.config_hash,
            }
            expected = {
                "run_id": run_id,
                "symbol": symbol,
                "direction": direction,
                "outcome": outcome,
                "reason": reason,
                "config_hash": config_hash,
            }
            if current != expected:
                raise ValueError(f"conflicting decision identity: {signal_id}")
            return existing
        return DecisionRecord.create(**values)

    def create_trade(
        self,
        *,
        trade_id: str,
        run_id: str,
        signal_id: str | None,
        symbol: str,
        side: str,
        state: str = "CREATED",
        initial_quantity: float = 0.0,
        remaining_quantity: float = 0.0,
        opened_at: datetime | None = None,
    ) -> TradeLifecycleRecord:
        return TradeLifecycleRecord.create(
            trade_id=trade_id,
            run=run_id,
            signal_id=signal_id,
            symbol=symbol,
            side=side,
            state=state,
            initial_quantity=initial_quantity,
            remaining_quantity=remaining_quantity,
            opened_at=_dt(opened_at),
        )

    def create_order_intent(self, **values: Any) -> OrderIntentRecord:
        return OrderIntentRecord.create(**values)

    def append_event(
        self,
        *,
        event_id: str,
        trade_id: str,
        sequence: int,
        event_type: str,
        event_time: datetime,
        payload: Mapping[str, Any] | None = None,
    ) -> LifecycleEventRecord:
        existing = LifecycleEventRecord.get_or_none(LifecycleEventRecord.event_id == event_id)
        if existing is not None:
            if existing.trade_id != trade_id or existing.sequence != sequence or existing.event_type != event_type:
                raise ValueError(f"conflicting event identity: {event_id}")
            return existing
        duplicate_sequence = LifecycleEventRecord.get_or_none(
            (LifecycleEventRecord.trade == trade_id) & (LifecycleEventRecord.sequence == sequence)
        )
        if duplicate_sequence is not None:
            raise ValueError(f"duplicate lifecycle sequence: {trade_id}:{sequence}")
        return LifecycleEventRecord.create(
            event_id=event_id,
            trade=trade_id,
            sequence=sequence,
            event_type=event_type,
            event_time=_dt(event_time),
            payload_json=_json(payload or {}),
        )

    def record_fill(self, **values: Any) -> FillRecord:
        return FillRecord.create(
            **{key: (_dt(value) if key == "fill_time" else value) for key, value in values.items()}
        )

    def update_trade(self, trade_id: str, **values: Any) -> TradeLifecycleRecord:
        query = TradeLifecycleRecord.update(**values).where(TradeLifecycleRecord.trade_id == trade_id)
        query.execute()
        return TradeLifecycleRecord.get_by_id(trade_id)

    def reconstruct_trade(self, trade_id: str) -> dict[str, Any]:
        trade = TradeLifecycleRecord.get_by_id(trade_id)
        events = list(
            LifecycleEventRecord.select()
            .where(LifecycleEventRecord.trade == trade_id)
            .order_by(LifecycleEventRecord.sequence)
        )
        fills = list(
            FillRecord.select()
            .where(FillRecord.trade == trade_id)
            .order_by(FillRecord.fill_time, FillRecord.fill_id)
        )
        fill_values = [
            FillLeg(
                role=fill.role,
                side=fill.side,
                price=fill.price,
                quantity=fill.quantity,
                fee=fill.fee,
                slippage=fill.slippage,
            )
            for fill in fills
        ]
        exit_event_types = {
            "TAKE_PROFIT_1", "TAKE_PROFIT_2", "STOP_LOSS", "REVERSAL_EXIT",
            "EMERGENCY_EXIT", "END_OF_DATA", "MANUAL_EXIT",
        }
        recomputed_exit_reason = next(
            (event.event_type for event in reversed(events) if event.event_type in exit_event_types),
            None,
        )
        recomputed_net_pnl = (
            calculate_realized_net_pnl(fill_values)
            if trade.state == "CLOSED" and fill_values else None
        )
        return {
            "trade_id": trade.trade_id,
            "symbol": trade.symbol,
            "side": trade.side,
            "state": trade.state,
            "signal_id": trade.signal_id,
            "initial_quantity": trade.initial_quantity,
            "remaining_quantity": trade.remaining_quantity,
            "stored_net_pnl": trade.net_pnl,
            "recomputed_net_pnl": recomputed_net_pnl,
            "stored_exit_reason": trade.final_exit_reason,
            "recomputed_exit_reason": recomputed_exit_reason,
            "events": [
                {
                    "event_id": event.event_id,
                    "sequence": event.sequence,
                    "event_type": event.event_type,
                    "event_time": event.event_time.isoformat(),
                    "payload": json.loads(event.payload_json),
                }
                for event in events
            ],
            "fills": [
                {
                    "fill_id": fill.fill_id,
                    "role": fill.role,
                    "price": fill.price,
                    "quantity": fill.quantity,
                    "fee": fill.fee,
                    "slippage": fill.slippage,
                    "funding": fill.funding,
                }
                for fill in fills
            ],
        }

    def backup_to(self, destination: str | Path) -> Path:
        """Create a consistent SQLite backup using SQLite's backup API."""
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        if DB.is_closed():
            raise RuntimeError("database is closed")
        DB.execute_sql("PRAGMA wal_checkpoint(PASSIVE)")
        source_connection = DB.connection()
        import sqlite3
        destination_connection = sqlite3.connect(str(target))
        try:
            source_connection.backup(destination_connection)
        finally:
            destination_connection.close()
        return target
