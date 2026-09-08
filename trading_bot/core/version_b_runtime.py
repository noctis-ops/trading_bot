"""Event-driven Version B Paper runtime.

``VersionBPaperPipeline`` replays a finished window.  This module is the
operational shape of the same path: one market event arrives, is validated,
produces at most one decision, and every consequence is persisted before the
next event is accepted.

    MarketEvent → StrategyCore → RiskEngine → ExecutionService
                → TradeLifecycle → VersionBStore → Recovery/Health

Three properties define it:

*No future.*  The runtime only ever sees bars that have closed, plus the open
price of the bar that is starting now.  It is never handed the frame.

*No memory.*  Identity of consumed events, position state, intents, fills,
health, and the single-instance lock are all rows.  A restart continues after
the last persisted event rather than replaying the window.

*Fail closed.*  Stale or out-of-order data opens a circuit breaker that blocks
new entries; exits still process, because being able to get out is never the
thing you want to lose.

The venue is a deterministic double implementing the same
``ExternalOrderAdapter`` contract as the real path, so timeout/UNKNOWN,
partial fills, and unconfirmed protection exercise the real intent semantics.
It is a double: it proves the runtime's behaviour, not Binance's.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

import pandas as pd

from core.external_execution import (
    Acknowledgement,
    ExchangeOrderStatus,
    ExternalOrderAdapter,
    OrderIntent,
)
from core.version_b_paper import VersionBPaperEngine
from core.version_b_recovery import recover_unified_state
from data.time_alignment import DataStatus, align_timeframes, candle_close_time

DECISION_TIMEFRAME = "15m"
EXIT_TIMEFRAME = "5m"


class HealthStatus(str, Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    STALE = "STALE"
    DATA_GAP = "DATA_GAP"
    OUT_OF_ORDER = "OUT_OF_ORDER"
    UNKNOWN = "UNKNOWN"
    FAILED = "FAILED"


class AuditSeverity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class EventOutcome(str, Enum):
    """What one event produced.  Persisted, so a restart can read the trail."""

    DECIDED = "DECIDED"
    EXECUTED = "EXECUTED"
    EXIT_PROCESSED = "EXIT_PROCESSED"
    NO_SIGNAL = "NO_SIGNAL"
    POSITION_OPEN = "POSITION_OPEN"
    DATA_REJECTED = "DATA_REJECTED"
    STALE_REJECTED = "STALE_REJECTED"
    OUT_OF_ORDER_REJECTED = "OUT_OF_ORDER_REJECTED"
    CIRCUIT_OPEN_REJECTED = "CIRCUIT_OPEN_REJECTED"
    DUPLICATE_IGNORED = "DUPLICATE_IGNORED"
    END_OF_DATA = "END_OF_DATA"
    IGNORED = "IGNORED"


# Data faults that clear themselves once good data resumes.  A breaker opened
# for these closes on the next healthy decision, and says so in the audit trail.
_AUTO_RESET_FAULTS = frozenset({"STALE_DATA", "DATA_NOT_VALID", "OUT_OF_ORDER_EVENT"})


class RuntimeError_(RuntimeError):
    """Base class for runtime refusals."""


class NotPrimaryInstance(RuntimeError_):
    """Another live process owns this run."""


class RuntimeNotRunning(RuntimeError_):
    """The runtime was not started, or was already shut down."""


class StaleDataRejected(RuntimeError_):
    """A decision was refused because the data was too old to trade on."""


@dataclass(frozen=True)
class MarketEvent:
    """One bar arriving on its own, in time order.

    ``bar`` is the candle that has just closed.  ``entry_open`` is present only
    on a decision boundary and is the open of the bar starting now — the price
    a live entry would take, not a future price.
    """

    event_id: str
    symbol: str
    timeframe: str
    open_time: pd.Timestamp
    bar: Mapping[str, float]
    emitted_at: pd.Timestamp
    decision_time: pd.Timestamp | None = None
    entry_open: float | None = None

    @property
    def is_decision_boundary(self) -> bool:
        return self.decision_time is not None


@dataclass
class ComponentHealth:
    name: str
    status: HealthStatus = HealthStatus.HEALTHY
    detail: str = ""
    updated_at: datetime | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status.value,
            "detail": self.detail,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


@dataclass
class RuntimeHealth:
    """Stored health of the runtime.  Durable, because an operator restarts."""

    data: ComponentHealth = field(default_factory=lambda: ComponentHealth("data"))
    execution: ComponentHealth = field(default_factory=lambda: ComponentHealth("execution"))
    database: ComponentHealth = field(default_factory=lambda: ComponentHealth("database"))
    circuit_open: bool = False
    circuit_reason: str = ""
    consecutive_data_faults: int = 0
    last_event_time: pd.Timestamp | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "data": self.data.as_dict(),
            "execution": self.execution.as_dict(),
            "database": self.database.as_dict(),
            "circuit_open": self.circuit_open,
            "circuit_reason": self.circuit_reason,
            "consecutive_data_faults": self.consecutive_data_faults,
            "last_event_time": self.last_event_time.isoformat() if self.last_event_time else None,
        }

    @staticmethod
    def from_dict(payload: Mapping[str, Any]) -> "RuntimeHealth":
        def component(name: str) -> ComponentHealth:
            raw = payload.get(name) or {}
            return ComponentHealth(
                name=name,
                status=HealthStatus(raw.get("status", HealthStatus.HEALTHY.value)),
                detail=raw.get("detail", ""),
            )

        last = payload.get("last_event_time")
        return RuntimeHealth(
            data=component("data"),
            execution=component("execution"),
            database=component("database"),
            circuit_open=bool(payload.get("circuit_open", False)),
            circuit_reason=payload.get("circuit_reason", ""),
            consecutive_data_faults=int(payload.get("consecutive_data_faults", 0)),
            last_event_time=pd.Timestamp(last) if last else None,
        )


# ── deterministic market feed ───────────────────────────────────────────────


class DeterministicMarketFeed:
    """Turns finished frames into an ordered event stream, one bar at a time.

    Ordering matches the batch replay exactly: at a 15m boundary the 5m bar
    that is closing arrives first, then the decision, because replay consumes
    5m exits before it evaluates the decision at the same instant.
    """

    def __init__(self, frames: Mapping[str, pd.DataFrame], *, symbol: str = "BTC/USDT"):
        self.symbol = symbol
        self.frames = {name: self._normalise(frame) for name, frame in frames.items()}

    @staticmethod
    def _normalise(frame: pd.DataFrame) -> pd.DataFrame:
        df = frame.copy()
        if "timestamp" in df.columns:
            df = df.set_index("timestamp")
        df.index = pd.DatetimeIndex(pd.to_datetime(df.index, utc=True))
        df = df[~df.index.duplicated(keep="last")].sort_index()
        if "volume" not in df.columns:
            df["volume"] = 1.0
        return df

    def _bar(self, timeframe: str, open_time: pd.Timestamp) -> dict[str, float]:
        row = self.frames[timeframe].loc[open_time]
        return {key: float(row[key]) for key in ("open", "high", "low", "close", "volume")}

    def events(self) -> list[MarketEvent]:
        """The whole stream, ordered.  The runtime still consumes it one by one."""
        five = self.frames[EXIT_TIMEFRAME]
        fifteen = self.frames[DECISION_TIMEFRAME]
        hourly = self.frames["1h"]
        pending: list[tuple[pd.Timestamp, int, MarketEvent]] = []

        for open_time in hourly.index:
            close = candle_close_time(open_time, "1h")
            pending.append((close, 0, MarketEvent(
                event_id=f"{self.symbol}:1h:{open_time.isoformat()}",
                symbol=self.symbol, timeframe="1h", open_time=open_time,
                bar=self._bar("1h", open_time), emitted_at=close,
            )))

        for open_time in five.index:
            close = candle_close_time(open_time, EXIT_TIMEFRAME)
            pending.append((close, 1, MarketEvent(
                event_id=f"{self.symbol}:5m:{open_time.isoformat()}",
                symbol=self.symbol, timeframe=EXIT_TIMEFRAME, open_time=open_time,
                bar=self._bar(EXIT_TIMEFRAME, open_time), emitted_at=close,
            )))

        for open_time in fifteen.index:
            close = candle_close_time(open_time, DECISION_TIMEFRAME)
            # The bar opening at `close` supplies the entry price for the
            # decision taken at `close`; its open is known the instant it starts.
            nxt = fifteen.index[fifteen.index.get_loc(open_time) + 1] if (
                fifteen.index.get_loc(open_time) + 1 < len(fifteen.index)
            ) else None
            pending.append((close, 2, MarketEvent(
                event_id=f"{self.symbol}:15m:{open_time.isoformat()}",
                symbol=self.symbol, timeframe=DECISION_TIMEFRAME, open_time=open_time,
                bar=self._bar(DECISION_TIMEFRAME, open_time), emitted_at=close,
                decision_time=close,
                entry_open=None if nxt is None else float(fifteen.loc[nxt, "open"]),
            )))

        pending.sort(key=lambda item: (item[0], item[1], item[2].event_id))
        return [event for _, _, event in pending]

    def __iter__(self) -> Iterator[MarketEvent]:
        return iter(self.events())


# ── deterministic exchange double ───────────────────────────────────────────


class DeterministicExchangeDouble(ExternalOrderAdapter):
    """A scriptable venue.  Never fabricates an acknowledgement.

    Failure modes are injected per client order id so a test can say "this
    submission times out" or "this one fills half" without the runtime knowing
    anything is unusual.
    """

    def __init__(self) -> None:
        self.timeouts: set[str] = set()
        self.rejections: set[str] = set()
        self.partial: dict[str, float] = {}
        self.vanished: set[str] = set()
        self.submitted: list[OrderIntent] = []
        self.acks: dict[str, Acknowledgement] = {}
        self.fetch_calls: list[str] = []

    # scripting helpers
    def will_time_out(self, client_order_id: str) -> None:
        self.timeouts.add(client_order_id)

    def will_reject(self, client_order_id: str) -> None:
        self.rejections.add(client_order_id)

    def will_fill_partially(self, client_order_id: str, quantity: float) -> None:
        self.partial[client_order_id] = float(quantity)

    def will_vanish(self, client_order_id: str) -> None:
        self.vanished.add(client_order_id)

    def resolve_after_timeout(self, client_order_id: str, ack: Acknowledgement) -> None:
        """What reconciliation will later discover about a lost response."""
        self.acks[client_order_id] = ack

    # adapter contract
    def submit(self, intent: OrderIntent) -> Acknowledgement:
        self.submitted.append(intent)
        cid = intent.client_order_id
        if cid in self.vanished:
            return Acknowledgement(status=ExchangeOrderStatus.NOT_FOUND)
        if cid in self.timeouts:
            return Acknowledgement(status=ExchangeOrderStatus.UNKNOWN)
        if cid in self.rejections:
            return Acknowledgement(status=ExchangeOrderStatus.REJECTED)
        if cid in self.partial:
            return Acknowledgement(
                status=ExchangeOrderStatus.PARTIALLY_FILLED,
                exchange_order_id=f"X-{cid}",
                filled_quantity=self.partial[cid],
                average_fill_price=intent.intended_price,
            )
        return Acknowledgement(
            status=ExchangeOrderStatus.FILLED,
            exchange_order_id=f"X-{cid}",
            filled_quantity=intent.intended_quantity,
            average_fill_price=intent.intended_price,
        )

    def fetch(self, intent: OrderIntent) -> Acknowledgement:
        self.fetch_calls.append(intent.client_order_id)
        if intent.client_order_id in self.acks:
            return self.acks[intent.client_order_id]
        if intent.client_order_id in self.vanished:
            return Acknowledgement(status=ExchangeOrderStatus.NOT_FOUND)
        return Acknowledgement(status=ExchangeOrderStatus.UNKNOWN)

    def fetch_open_orders(self, symbol: str) -> list[Acknowledgement]:
        return []

    def fetch_positions(self, symbol: str) -> list[Mapping[str, Any]]:
        return []


# ── the runtime ─────────────────────────────────────────────────────────────


class VersionBPaperRuntime:
    """Continuous Paper over the unified Version B path.

    Start, feed events one at a time, shut down, restart — the state that comes
    back is the state that was written, never the state that was remembered.
    """

    def __init__(
        self,
        *,
        db_path: str | Path,
        run_id: str | None = None,
        initial_balance: float = 10_000.0,
        strategy: Any | None = None,
        fee_rate: float | None = None,
        slippage_rate: float | None = None,
        symbol: str = "BTC/USDT",
        direction: str = "long",
        holder: str | None = None,
        exchange: DeterministicExchangeDouble | None = None,
        stale_after_seconds: float = 3600.0,
        data_fault_threshold: int = 2,
        resume: bool = False,
    ):
        from database.version_b_store import RunRecord, VersionBStore

        if direction not in {"long", "short"}:
            raise ValueError("direction must be long or short")
        self.db_path = Path(db_path)
        self.store = VersionBStore(self.db_path)
        self.symbol = symbol
        self.direction = direction
        self.holder = holder or f"runtime-{uuid.uuid4().hex[:8]}"
        self.exchange = exchange or DeterministicExchangeDouble()
        self.stale_after_seconds = float(stale_after_seconds)
        self.data_fault_threshold = int(data_fault_threshold)
        self.started = False
        self.shutdown_requested = False

        self.frames: dict[str, pd.DataFrame] = {
            "1h": pd.DataFrame(), DECISION_TIMEFRAME: pd.DataFrame(), EXIT_TIMEFRAME: pd.DataFrame()
        }
        self.health = RuntimeHealth()
        self.pending_unknown_intents: list[dict[str, Any]] = []
        self._last_decision_time: pd.Timestamp | None = None

        resuming = resume and run_id is not None
        if resuming:
            if RunRecord.get_or_none(RunRecord.run_id == run_id) is None:
                raise ValueError(f"cannot resume unknown run: {run_id}")
            stored_balance = self.store.run_initial_balance(run_id)
            if stored_balance is None:
                raise ValueError(f"run {run_id} has no durable initial_balance")
            initial_balance = stored_balance
            stored_state = self.store.operational_state(run_id)
            for key, current in (("fee_rate", fee_rate), ("slippage_rate", slippage_rate)):
                stored = stored_state.get(key)
                if stored is None:
                    raise ValueError(f"run {run_id} has no durable {key}")
                if current is not None and float(current) != float(stored):
                    raise ValueError(
                        f"run {run_id} executed with {key}={stored}; refusing {current}"
                    )
                if key == "fee_rate":
                    fee_rate = float(stored)
                else:
                    slippage_rate = float(stored)
            self.health = RuntimeHealth.from_dict(stored_state.get("health") or {})

        self.run_id = run_id or f"paper-{uuid.uuid4().hex[:12]}"
        self.engine = VersionBPaperEngine(
            initial_balance=initial_balance,
            strategy=strategy,
            fee_rate=fee_rate,
            slippage_rate=slippage_rate,
            store=self.store,
            run_id=self.run_id,
        )
        self.service = self.engine.service
        if not resuming:
            self.store.update_run(
                self.run_id,
                initial_balance=float(self.engine.initial_balance),
                operational_state_json=_json_state(self.operational_state()),
            )
        else:
            # Warm-up history is data, not state: it is re-supplied by the feed.
            # Everything that decides or owes money comes back from rows.
            self.recovery_report = recover_unified_state(self.store, self.run_id, self.service)

    # ── lifecycle ─────────────────────────────────────────────────────────

    def start(self, *, takeover: bool = False) -> None:
        """Take the single-instance lock.  A second live instance is refused.

        A crashed process never released its lock, so a supervisor restarting it
        must be able to take over.  That is an explicit, audited act — never a
        silent one, because two processes trading one account is the failure
        this lock exists to prevent.
        """
        if self.started:
            return
        try:
            self.store.acquire_lock(run_id=self.run_id, holder=self.holder)
        except Exception as exc:
            existing = self.store.lock_holder(self.run_id)
            if takeover and existing is not None:
                self.audit("runtime", AuditSeverity.CRITICAL, "LOCK_TAKEN_OVER", {
                    "previous_holder": existing.holder,
                    "new_holder": self.holder,
                    "acquired_at": existing.acquired_at.isoformat(),
                })
                self.store.release_lock(self.run_id, holder=existing.holder)
                self.store.acquire_lock(run_id=self.run_id, holder=self.holder)
            else:
                self._set_health(self.health.database, HealthStatus.FAILED, str(exc))
                self.audit("runtime", AuditSeverity.CRITICAL, "LOCK_REFUSED",
                           {"error": str(exc)})
                raise NotPrimaryInstance(str(exc)) from exc
        self.started = True
        self.audit("runtime", AuditSeverity.INFO, "RUNTIME_STARTED", {"holder": self.holder})
        self.persist_state()

    def shutdown(self, *, graceful: bool = True) -> None:
        """Persist, release the lock, and stop accepting events.

        A graceful shutdown does **not** flatten an open position: the position
        is real and remains open across the restart.  Closing at the end of a
        measurement window is a separate, explicit operation.
        """
        if not self.started:
            return
        self.shutdown_requested = True
        self.persist_state()
        self.store.release_lock(self.run_id, holder=self.holder)
        self.started = False
        self.audit("runtime", AuditSeverity.INFO,
                   "RUNTIME_SHUTDOWN", {"graceful": graceful, "holder": self.holder})

    def close_at_end_of_data(self, price: float, event_time: datetime) -> dict[str, Any] | None:
        """Flatten the remainder because the measurement window ended.

        Mirrors the replay oracle's END_OF_DATA exit.  This is a property of a
        finite window, not something an operational runtime does on shutdown.
        """
        result = self.service.force_close(self.symbol, price, event_time=event_time)
        if result is not None:
            self._mark_event(
                f"{self.symbol}:end-of-data:{event_time.isoformat()}",
                EXIT_TIMEFRAME, event_time, EventOutcome.END_OF_DATA,
            )
        self.persist_state()
        return result

    # ── the event path ────────────────────────────────────────────────────

    def on_event(self, event: MarketEvent) -> EventOutcome:
        """Consume one event.  Everything it causes is persisted before return."""
        if not self.started:
            raise RuntimeNotRunning("runtime is not started")
        if event.symbol != self.symbol:
            return EventOutcome.IGNORED

        if self.store.is_event_processed(event.event_id):
            # Redelivery is normal.  Executing it twice is not.
            return EventOutcome.DUPLICATE_IGNORED

        if self._is_out_of_order(event):
            self._register_data_fault(HealthStatus.OUT_OF_ORDER, "OUT_OF_ORDER_EVENT", {
                "event_id": event.event_id,
                "open_time": event.open_time.isoformat(),
            })
            self._mark_event(event.event_id, event.timeframe, event.open_time,
                             EventOutcome.OUT_OF_ORDER_REJECTED,
                             processed_at=event.emitted_at)
            return EventOutcome.OUT_OF_ORDER_REJECTED

        self._append_bar(event)
        self.health.last_event_time = event.open_time
        outcome = EventOutcome.IGNORED
        try:
            if event.timeframe == EXIT_TIMEFRAME:
                outcome = self._process_exit_bar(event)
            elif event.is_decision_boundary:
                outcome = self._process_decision(event)
            else:
                outcome = EventOutcome.IGNORED
            self._set_health(self.health.data, HealthStatus.HEALTHY, "")
            self.health.consecutive_data_faults = 0
            if self.health.circuit_open and self.health.circuit_reason in _AUTO_RESET_FAULTS:
                self.health.circuit_open = False
                reason = self.health.circuit_reason
                self.health.circuit_reason = ""
                self.audit("data", AuditSeverity.WARNING, "CIRCUIT_BREAKER_CLOSED",
                           {"previous_reason": reason})
        except Exception as exc:  # a component failed: record it, do not hide it
            self._set_health(self.health.database, HealthStatus.FAILED, str(exc))
            self.audit("database", AuditSeverity.CRITICAL, "PERSIST_FAILED",
                       {"event_id": event.event_id, "error": str(exc)})
            raise
        self._mark_event(event.event_id, event.timeframe, event.open_time, outcome,
                         processed_at=event.emitted_at)
        self.persist_state()
        return outcome

    def run_stream(self, events: Iterable[MarketEvent]) -> dict[str, EventOutcome, int]:
        """Convenience: feed a whole stream one event at a time."""
        tally: dict[str, int] = {}
        for event in events:
            outcome = self.on_event(event)
            tally[outcome.value] = tally.get(outcome.value, 0) + 1
        return {EventOutcome(key): value for key, value in tally.items()}

    # ── decision ──────────────────────────────────────────────────────────

    def _process_decision(self, event: MarketEvent) -> EventOutcome:
        decision_time = event.decision_time
        if decision_time is None or event.entry_open is None:
            return EventOutcome.IGNORED

        stale_by = self._staleness(decision_time)
        if stale_by > self.stale_after_seconds:
            self._register_data_fault(HealthStatus.STALE, "STALE_DATA", {
                "decision_time": decision_time.isoformat(), "stale_by_seconds": stale_by,
            })
            raise StaleDataRejected(
                f"refusing entry at {decision_time.isoformat()}: data stale by {stale_by:.0f}s"
            )

        aligned = align_timeframes(
            self.frames, decision_time=decision_time,
            decision_timeframe=DECISION_TIMEFRAME, warmup=self._warmup(),
        )
        self._last_decision_time = decision_time
        if not aligned.valid:
            reasons = {key: value.status.value for key, value in aligned.quality.items()
                       if value.status != DataStatus.VALID}
            # Warm-up and empty history at start-up are expected, not faults:
            # counting them would open the breaker before the first decision.
            # Staleness, gaps, and schema failures are real faults.
            fault_codes = {
                DataStatus.STALE.value, DataStatus.DATA_GAP.value,
                DataStatus.INVALID_SCHEMA.value,
            }
            faulted = {key: value for key, value in reasons.items() if value in fault_codes}
            if faulted:
                self._register_data_fault(
                    HealthStatus.DATA_GAP if DataStatus.DATA_GAP.value in faulted.values()
                    else HealthStatus.STALE,
                    "DATA_NOT_VALID", faulted,
                )
            self.engine.persist_decision(
                symbol=self.symbol, direction=self.direction, timestamp=decision_time,
                aligned=aligned, outcome="DATA_REJECTED",
                reason=";".join(f"{key}={value}" for key, value in reasons.items()),
                snapshot={"quality": reasons},
            )
            self.engine.equity_curve.append(self.service.equity(
                {self.symbol: float(event.bar["close"])}))
            self.engine.timestamps.append(decision_time)
            return EventOutcome.DATA_REJECTED

        if self.health.circuit_open:
            self.audit("data", AuditSeverity.WARNING, "ENTRY_BLOCKED_CIRCUIT_OPEN",
                       {"decision_time": decision_time.isoformat(),
                        "reason": self.health.circuit_reason})
            return EventOutcome.CIRCUIT_OPEN_REJECTED

        self.engine.signal_checks += 1
        decision = self.engine.strategy_core.evaluate(
            self.symbol, aligned, direction=self.direction,
            config_hash=self.engine.config_hash,
        )
        decision_row = pd.Series({"open": event.entry_open, "close": float(event.bar["close"])})

        if self.symbol in self.service.positions or not decision.get("should_trade", False):
            if decision.get("should_trade", False):
                outcome, reason = "POSITION_OPEN", "POSITION_ALREADY_OPEN"
                result = EventOutcome.POSITION_OPEN
            else:
                outcome, reason = "NO_SIGNAL", decision.get("entry_quality", "NO_SIGNAL")
                result = EventOutcome.NO_SIGNAL
                self.engine.rejected_signals += 1
            self.engine.persist_decision(
                symbol=self.symbol, direction=self.direction, timestamp=decision_time,
                aligned=aligned, outcome=outcome, reason=reason, snapshot={"decision": decision},
            )
        else:
            accepted = self._submit_entry(
                decision=decision, decision_row=decision_row, timestamp=decision_time,
            )
            outcome = "EXECUTED" if accepted else "EXECUTION_REJECTED"
            result = EventOutcome.EXECUTED if accepted else EventOutcome.NO_SIGNAL
            self.engine.persist_decision(
                symbol=self.symbol, direction=self.direction, timestamp=decision_time,
                aligned=aligned, outcome=outcome,
                reason="SIGNAL_APPROVED" if accepted else "EXECUTION_REJECTED",
                snapshot={"decision": decision, **({"execution": accepted} if accepted else {})},
            )
        self.engine.equity_curve.append(self.service.equity(
            {self.symbol: float(event.bar["close"])}))
        self.engine.timestamps.append(decision_time)
        return result

    def _submit_entry(self, *, decision: dict[str, Any], decision_row: pd.Series,
                      timestamp: pd.Timestamp) -> dict[str, Any] | None:
        """Risk → write-ahead intent → venue → fill → lifecycle → DB."""
        entry_price = float(decision_row["open"])
        atr = float(decision.get("gate_result", {}).get("atr", 0.0) or 0.0)
        plan = self.engine.strategy_core.execution_plan(
            decision, equity=self.service.equity(), entry_price=entry_price, atr=atr,
        )
        if not plan["stops"].get("valid", False):
            return None
        size = plan["position_size"]
        signal_id = self.engine._signal_id(self.run_id, self.symbol, self.direction, timestamp)
        client_order_id = f"{self.run_id}-{signal_id}-ENTRY"

        ack = self.exchange.submit(OrderIntent(
            order_intent_id=f"pending:{client_order_id}",
            client_order_id=client_order_id,
            trade_id=signal_id,
            symbol=self.symbol,
            side=self.direction,
            order_type="MARKET",
            purpose="ENTRY",
            intended_quantity=size.quantity,
            intended_price=entry_price,
        ))
        if ack.status == ExchangeOrderStatus.UNKNOWN:
            self._set_health(self.health.execution, HealthStatus.UNKNOWN,
                             "entry submission timed out")
            self.audit("execution", AuditSeverity.CRITICAL, "ENTRY_SUBMIT_UNKNOWN",
                       {"client_order_id": client_order_id})
            self.pending_unknown_intents.append({
                "client_order_id": client_order_id, "purpose": "ENTRY",
                "signal_id": signal_id, "quantity": size.quantity, "price": entry_price,
                "timestamp": timestamp.isoformat(),
            })
            return None
        if ack.status == ExchangeOrderStatus.REJECTED:
            self._set_health(self.health.execution, HealthStatus.DEGRADED, "entry rejected")
            self.audit("execution", AuditSeverity.WARNING, "ENTRY_REJECTED",
                       {"client_order_id": client_order_id})
            return None

        quantity = size.quantity
        degraded = ""
        if ack.status == ExchangeOrderStatus.PARTIALLY_FILLED and ack.filled_quantity:
            quantity = min(float(ack.filled_quantity), size.quantity)
            self.audit("execution", AuditSeverity.WARNING, "ENTRY_PARTIALLY_FILLED",
                       {"client_order_id": client_order_id, "filled": quantity,
                        "intended": size.quantity})
            degraded = "entry partially filled"
        try:
            accepted = self.engine.open_from_decision(
                symbol=self.symbol, direction=self.direction, decision=decision,
                decision_row=decision_row, timestamp=timestamp,
            )
        except ValueError as exc:
            self._set_health(self.health.execution, HealthStatus.DEGRADED, str(exc))
            self.audit("execution", AuditSeverity.CRITICAL, "ENTRY_EXECUTION_FAILED",
                       {"error": str(exc)})
            return None
        if accepted is None:
            return None
        accepted["submitted_quantity"] = quantity
        confirmed = self._confirm_protection(
            trade_id=accepted["trade_id"],
            stops=plan["stops"],
            quantity=quantity,
            timestamp=timestamp,
        )
        if not confirmed:
            return accepted
        # Confirming protection must not erase an earlier degradation of this
        # very submission; the health reflects the worst thing that happened.
        if degraded:
            self._set_health(self.health.execution, HealthStatus.DEGRADED, degraded)
        else:
            self._set_health(self.health.execution, HealthStatus.HEALTHY, "")
        return accepted

    def _confirm_protection(self, *, trade_id: str, stops: Mapping[str, float],
                            quantity: float, timestamp: pd.Timestamp) -> bool:
        """A position without confirmed protection is flattened, not kept.

        The venue double acknowledges TP1/TP2/SL.  If any of them is not
        accepted, the runtime does not carry an unprotected position: it
        flattens immediately and raises a critical audit event.  This is the
        same rule the external execution contract enforces, applied locally.
        """
        purposes = (
            ("TAKE_PROFIT_1", float(stops["take_profit_1"]), quantity * 0.5),
            ("TAKE_PROFIT_2", float(stops["take_profit_2"]), quantity * 0.5),
            ("STOP_LOSS", float(stops["stop_loss"]), quantity),
        )
        unconfirmed: list[str] = []
        for purpose, price, slice_quantity in purposes:
            cid = f"{self.run_id}-{trade_id}-{purpose}"
            ack = self.exchange.submit(OrderIntent(
                order_intent_id=f"pending:{cid}", client_order_id=cid, trade_id=trade_id,
                symbol=self.symbol,
                side="sell" if self.direction == "long" else "buy",
                order_type="LIMIT" if purpose != "STOP_LOSS" else "STOP_MARKET",
                purpose=purpose, intended_quantity=slice_quantity, intended_price=price,
            ))
            if ack.status in (ExchangeOrderStatus.FILLED, ExchangeOrderStatus.NEW,
                              ExchangeOrderStatus.PARTIALLY_FILLED):
                continue
            unconfirmed.append(f"{purpose}={ack.status.value}")
        if not unconfirmed:
            return True
        self._set_health(self.health.execution, HealthStatus.DEGRADED,
                         "protection not confirmed")
        self.audit("execution", AuditSeverity.CRITICAL, "PROTECTION_NOT_CONFIRMED",
                   {"trade_id": trade_id, "unconfirmed": unconfirmed})
        # Fail closed: an unprotected position is worse than no position.
        position = self.service.positions.get(self.symbol)
        price = position.entry_price if position is not None else 0.0
        self.service.force_close(
            self.symbol, price, event_type="EMERGENCY_EXIT",
            event_time=timestamp.to_pydatetime(),
        )
        self.audit("execution", AuditSeverity.CRITICAL, "EMERGENCY_FLATTEN",
                   {"trade_id": trade_id, "reason": "unconfirmed protection"})
        return False

    def _process_exit_bar(self, event: MarketEvent) -> EventOutcome:
        if self.symbol not in self.service.positions:
            return EventOutcome.IGNORED
        # A 5m bar may only drive exits from the current decision window onward;
        # replay enforces the same half-open boundary.
        if self._last_decision_time is not None and event.open_time < self._last_decision_time:
            return EventOutcome.IGNORED
        result = self.service.process_bar(
            self.symbol, dict(event.bar), event_time=event.open_time.to_pydatetime(),
        )
        return EventOutcome.EXIT_PROCESSED if result else EventOutcome.IGNORED

    # ── reconciliation ────────────────────────────────────────────────────

    def reconcile_pending(self) -> list[dict[str, Any]]:
        """Resolve intents left UNKNOWN by a lost response.

        Absence of evidence is never turned into a fill.  An intent resolves
        only when the venue reports something concrete.
        """
        resolved: list[dict[str, Any]] = []
        still_pending: list[dict[str, Any]] = []
        for pending in self.pending_unknown_intents:
            ack = self.exchange.fetch(OrderIntent(
                order_intent_id=f"pending:{pending['client_order_id']}",
                client_order_id=pending["client_order_id"],
                trade_id=pending["signal_id"],
                symbol=self.symbol, side=self.direction, order_type="MARKET",
                purpose="ENTRY", intended_quantity=pending["quantity"],
                intended_price=pending["price"],
            ))
            entry = {**pending, "resolved_status": ack.status.value}
            if ack.status == ExchangeOrderStatus.FILLED:
                entry["outcome"] = "RECONCILED_FILLED"
                self.audit("execution", AuditSeverity.INFO, "INTENT_RECONCILED_FILLED",
                           {"client_order_id": pending["client_order_id"]})
                self._set_health(self.health.execution, HealthStatus.HEALTHY, "")
                resolved.append(entry)
            elif ack.status == ExchangeOrderStatus.NOT_FOUND:
                entry["outcome"] = "RECONCILED_NOT_FOUND"
                self.audit("execution", AuditSeverity.CRITICAL, "INTENT_RECONCILED_NOT_FOUND",
                           {"client_order_id": pending["client_order_id"]})
                resolved.append(entry)
            else:
                entry["outcome"] = "STILL_UNKNOWN"
                still_pending.append(pending)
                # Reported, not dropped: an unresolved intent is the state.
                resolved.append(entry)
                self.audit("execution", AuditSeverity.CRITICAL, "INTENT_STILL_UNKNOWN",
                           {"client_order_id": pending["client_order_id"]})
        self.pending_unknown_intents = still_pending
        self.persist_state()
        return resolved

    # ── health / audit / state ────────────────────────────────────────────

    def audit(self, component: str, severity: AuditSeverity, code: str,
              detail: Mapping[str, Any] | None = None) -> None:
        self.store.record_audit(
            run_id=self.run_id, component=component, severity=severity.value,
            code=code, detail=detail or {},
        )

    def _set_health(self, component: ComponentHealth, status: HealthStatus, detail: str) -> None:
        component.status = status
        component.detail = detail
        component.updated_at = datetime.now(timezone.utc)

    def _register_data_fault(self, status: HealthStatus, code: str,
                             detail: Mapping[str, Any]) -> None:
        self._set_health(self.health.data, status, code)
        self.health.consecutive_data_faults += 1
        self.audit("data", AuditSeverity.WARNING, code, dict(detail))
        if self.health.consecutive_data_faults >= self.data_fault_threshold:
            if not self.health.circuit_open:
                self.health.circuit_open = True
                self.health.circuit_reason = code
                self.audit("data", AuditSeverity.CRITICAL, "CIRCUIT_BREAKER_OPEN",
                           {"reason": code,
                            "faults": self.health.consecutive_data_faults})
            self.persist_state()

    def _warmup(self) -> dict[str, int]:
        if hasattr(self.engine.strategy_core.strategy, "get_signal_breakdown"):
            from data.time_alignment import derive_warmup_requirements

            return derive_warmup_requirements(self.engine.risk_engine.config)
        return {"1h": 21, DECISION_TIMEFRAME: 21, EXIT_TIMEFRAME: 21}

    def _staleness(self, decision_time: pd.Timestamp) -> float:
        """Seconds between the decision instant and the newest closed 15m bar."""
        frame = self.frames[DECISION_TIMEFRAME]
        if frame.empty:
            return float("inf")
        age = decision_time - candle_close_time(frame.index[-1], DECISION_TIMEFRAME)
        return max(0.0, age.total_seconds())

    def _append_bar(self, event: MarketEvent) -> None:
        frame = self.frames[event.timeframe]
        row = pd.DataFrame([dict(event.bar)], index=pd.DatetimeIndex([event.open_time]))
        self.frames[event.timeframe] = (
            row if frame.empty else pd.concat([frame, row]).sort_index()
        )
        self.frames[event.timeframe] = self.frames[event.timeframe][
            ~self.frames[event.timeframe].index.duplicated(keep="last")
        ]

    def _is_out_of_order(self, event: MarketEvent) -> bool:
        frame = self.frames[event.timeframe]
        if frame.empty:
            return False
        # A bar older than the newest one already consumed for this timeframe
        # cannot be traded on: decisions have already been taken past it.
        return bool(event.open_time < frame.index[-1])

    def _mark_event(self, event_id: str, timeframe: str, open_time: pd.Timestamp,
                    outcome: EventOutcome, *,
                    processed_at: pd.Timestamp | None = None) -> None:
        stamp = (processed_at or open_time).to_pydatetime()
        try:
            self.store.mark_event_processed(
                event_id=event_id, run_id=self.run_id, symbol=self.symbol,
                timeframe=timeframe, open_time=stamp, outcome=outcome.value,
                processed_at=stamp,
            )
        except Exception:
            self._set_health(self.health.database, HealthStatus.FAILED,
                             "could not record consumed event")
            self.audit("database", AuditSeverity.CRITICAL, "EVENT_PERSIST_FAILED",
                       {"event_id": event_id})
            raise

    def operational_state(self) -> dict[str, Any]:
        """Everything an operator or a restart needs, all of it durable."""
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
        last = self.store.last_processed_event(self.run_id)
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
            "events_processed": self.store.processed_event_count(self.run_id),
            "last_event_id": last.event_id if last else None,
            "last_event_time": last.open_time.isoformat() if last else None,
            "pending_unknown_intents": list(self.pending_unknown_intents),
            "health": self.health.as_dict(),
            "positions": sorted(positions, key=lambda item: item["trade_id"]),
            "captured_at": datetime.now(timezone.utc).isoformat(),
        }

    def persist_state(self) -> dict[str, Any]:
        state = self.operational_state()
        try:
            self.store.record_operational_state(self.run_id, state)
        except Exception as exc:
            self._set_health(self.health.database, HealthStatus.FAILED, str(exc))
            self.audit("database", AuditSeverity.CRITICAL, "STATE_PERSIST_FAILED",
                       {"error": str(exc)})
            raise
        return state

    def close(self) -> None:
        self.store.close()

    def __enter__(self) -> "VersionBPaperRuntime":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.started:
            self.shutdown()
        self.close()


def _json_state(state: Any) -> str:
    import json

    return json.dumps(state, sort_keys=True, separators=(",", ":"), default=str)


__all__ = [
    "AuditSeverity",
    "ComponentHealth",
    "DECISION_TIMEFRAME",
    "DeterministicExchangeDouble",
    "DeterministicMarketFeed",
    "EventOutcome",
    "EXIT_TIMEFRAME",
    "HealthStatus",
    "MarketEvent",
    "NotPrimaryInstance",
    "RuntimeHealth",
    "RuntimeNotRunning",
    "StaleDataRejected",
    "VersionBPaperRuntime",
]
