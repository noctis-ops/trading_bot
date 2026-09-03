"""Deterministic proof of the External Execution + Restart Recovery contract.

Everything here runs against ``DeterministicExchangeDouble``.  No network, no
ccxt client, no real exchange.  The tests assert **invariants** — after a
failure or a restart the system must not create a duplicate order, fill, or
trade, and it must never report an unknown exchange state as success.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import tempfile
import unittest

from core.execution_service import VersionBExecutionService
from core.external_execution import (
    PURPOSE_ENTRY,
    PURPOSE_STOP_LOSS,
    PURPOSE_TAKE_PROFIT_1,
    PURPOSE_TAKE_PROFIT_2,
    Acknowledgement,
    ExchangeOrderStatus,
    ExternalExecutionService,
    ExternalOrderAdapter,
    IntentStatus,
    LostResponse,
    OrderRejected,
    ProtectionNotConfirmed,
    UnresolvedOrderState,
)
from database.version_b_store import LifecycleEventRecord, VersionBStore


NOW = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
RUN_ID = "run-ext-1"
SYMBOL = "BTC/USDT"


class Clock:
    """Deterministic clock: each call advances exactly one second."""

    def __init__(self, start: datetime = NOW):
        self.current = start

    def __call__(self) -> datetime:
        value = self.current
        self.current = self.current + timedelta(seconds=1)
        return value


class DeterministicExchangeDouble(ExternalOrderAdapter):
    """In-memory exchange model with injectable failure behavior."""

    def __init__(self, fill_price: float = 100.0):
        self.orders: dict[str, dict] = {}
        self.submit_calls: list[str] = []
        self.fetch_calls: list[str] = []
        self.duplicate_submissions = 0
        self.on_submit = None
        # Failure injection, keyed by client_order_id.
        self.lost_after_execution: dict[str, int] = {}
        self.submit_lost: dict[str, int] = {}
        self.reject: set[str] = set()
        self.unknown_reply: set[str] = set()
        self.fetch_lost: set[str] = set()
        self.fetch_lost_counts: dict[str, int] = {}
        self.fetch_not_found: set[str] = set()
        self.partial_quantity: dict[str, float] = {}
        self.fill_price = fill_price

    # ── adapter surface ───────────────────────────────────────────────────
    def _ack(self, order: dict) -> Acknowledgement:
        if order["status"] == "REJECTED":
            return Acknowledgement(status=ExchangeOrderStatus.REJECTED)
        filled = order["filled_quantity"]
        if filled <= 0:
            status = ExchangeOrderStatus.NEW
        elif filled < order["intended_quantity"] - 1e-12:
            status = ExchangeOrderStatus.PARTIALLY_FILLED
        else:
            status = ExchangeOrderStatus.FILLED
        return Acknowledgement(
            status=status,
            exchange_order_id=order["exchange_order_id"],
            filled_quantity=filled,
            average_fill_price=order["average_fill_price"],
        )

    def submit(self, intent):
        self.submit_calls.append(intent.client_order_id)
        if self.on_submit is not None:
            self.on_submit(intent)
        cid = intent.client_order_id
        # A timeout where the request may never have reached the venue at all.
        pending_losses = self.submit_lost.get(cid, 0)
        if pending_losses > 0:
            self.submit_lost[cid] = pending_losses - 1
            raise LostResponse("simulated submit timeout")
        existing = self.orders.get(cid)
        if existing is not None:
            # The venue deduplicates on clientOrderId.  This is the assumption
            # that still needs real exchange evidence.
            self.duplicate_submissions += 1
            return self._ack(existing)
        if cid in self.reject:
            self.orders[cid] = {
                "client_order_id": cid,
                "exchange_order_id": None,
                "intended_quantity": intent.intended_quantity,
                "filled_quantity": 0.0,
                "average_fill_price": None,
                "resting": intent.order_type != "MARKET",
                "status": "REJECTED",
            }
            return Acknowledgement(status=ExchangeOrderStatus.REJECTED)

        order = {
            "client_order_id": cid,
            "exchange_order_id": f"ex-{len(self.orders) + 1}",
            "intended_quantity": float(intent.intended_quantity),
            "filled_quantity": 0.0,
            "average_fill_price": None,
            "resting": intent.order_type != "MARKET",
            "status": "NEW",
        }
        self.orders[cid] = order
        if cid in self.unknown_reply:
            return Acknowledgement(
                status=ExchangeOrderStatus.UNKNOWN, exchange_order_id=order["exchange_order_id"]
            )
        if not order["resting"]:
            order["filled_quantity"] = float(self.partial_quantity.get(cid, intent.intended_quantity))
            order["average_fill_price"] = self.fill_price
        remaining_losses = self.lost_after_execution.get(cid, 0)
        if remaining_losses > 0:
            self.lost_after_execution[cid] = remaining_losses - 1
            raise LostResponse("simulated response loss after execution")
        return self._ack(order)

    def fetch(self, intent):
        cid = intent.client_order_id
        self.fetch_calls.append(cid)
        pending = self.fetch_lost_counts.get(cid, 0)
        if pending > 0:
            self.fetch_lost_counts[cid] = pending - 1
            raise LostResponse("simulated query failure")
        if cid in self.fetch_lost:
            raise LostResponse("simulated query failure")
        order = self.orders.get(cid)
        if order is None or cid in self.fetch_not_found:
            return Acknowledgement(status=ExchangeOrderStatus.NOT_FOUND)
        return self._ack(order)

    # ── test helpers ──────────────────────────────────────────────────────
    def fill_resting(self, cid: str, quantity: float, price: float | None = None) -> None:
        order = self.orders[cid]
        order["filled_quantity"] = float(quantity)
        if price is not None:
            order["average_fill_price"] = float(price)


class ExternalExecutionTestBase(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tempdir.name) / "version_b.sqlite"
        self.clock = Clock()
        self.double = DeterministicExchangeDouble()
        self.store = VersionBStore(self.path)
        self.store.create_run(
            run_id=RUN_ID,
            environment="paper",
            code_version="test-code",
            strategy_version="1.3",
            config_hash="config-hash",
            data_hash=None,
            execution_model_version="vb-1.0-5m-stop-first",
            universe=[SYMBOL],
            effective_config={},
            created_at=NOW,
        )
        self.service = self.make_service(self.store)

    def tearDown(self):
        self.store.close()
        self.tempdir.cleanup()

    def make_service(self, store) -> ExternalExecutionService:
        return ExternalExecutionService(
            adapter=self.double,
            store=store,
            run_id=RUN_ID,
            fee_rate=0.0004,
            max_submit_attempts=3,
            now=self.clock,
        )

    def make_engine(self, store) -> VersionBExecutionService:
        return VersionBExecutionService(
            initial_balance=10_000.0,
            fee_rate=0.0004,
            slippage_rate=0.0,
            move_sl_to_breakeven=True,
            store=store,
            run_id=RUN_ID,
        )

    def restart(self):
        """Simulate a process restart against the same durable store."""
        self.store.close()
        self.store = VersionBStore(self.path)
        self.service = self.make_service(self.store)
        return self.store, self.service

    def open_ok(self, trade_id: str, quantity: float = 4.0, **kwargs):
        return self.service.open_position_with_protection(
            trade_id=trade_id,
            symbol=SYMBOL,
            side="long",
            quantity=quantity,
            stop_loss=98.0,
            take_profit_1=102.0,
            take_profit_2=104.0,
            leverage=10.0,
            event_time=NOW,
            **kwargs,
        )


class WriteAheadAndIdentityTests(ExternalExecutionTestBase):
    def test_intent_row_is_durable_before_the_adapter_is_called(self):
        seen: dict = {}

        def hook(intent):
            row = self.store.get_order_intent(intent.order_intent_id)
            seen["exists"] = row is not None
            seen["status"] = row.status if row else None
            seen["client_order_id"] = row.client_order_id if row else None

        self.double.on_submit = hook
        self.service.open_entry(
            trade_id="T-WA", symbol=SYMBOL, side="long", quantity=4,
            leverage=10, event_time=NOW,
        )
        self.assertTrue(seen["exists"], "intent must be written before any network call")
        self.assertEqual(seen["status"], IntentStatus.SUBMITTED.value)
        self.assertEqual(seen["client_order_id"], self.service.client_order_id("T-WA", PURPOSE_ENTRY))

    def test_retry_reuses_one_identity_and_records_one_fill(self):
        cid = self.service.client_order_id("T-DUP", PURPOSE_ENTRY)
        # The order executes, the response is lost, and the first recovery query
        # also fails — so the service must genuinely resubmit.
        self.double.lost_after_execution[cid] = 1
        self.double.fetch_lost_counts[cid] = 1

        entry = self.service.open_entry(
            trade_id="T-DUP", symbol=SYMBOL, side="long", quantity=4,
            leverage=10, event_time=NOW,
        )

        self.assertEqual(entry.filled_quantity, 4.0)
        self.assertEqual(len(self.double.orders), 1, "one exchange order only")
        self.assertEqual(self.double.duplicate_submissions, 1, "the retry hit the same client id")
        self.assertEqual(len(set(self.double.submit_calls)), 1)

        intent = self.store.get_order_intent(self.service.intent_id("T-DUP", PURPOSE_ENTRY))
        self.assertEqual(intent.status, IntentStatus.FILLED.value)
        self.assertGreaterEqual(intent.attempt_count, 2)
        self.assertEqual(len(self.store.fills_for_intent(intent.order_intent_id)), 1)
        self.assertEqual(len(self.store.fills_for_trade("T-DUP")), 1)
        self.assertEqual(
            [event.event_type for event in self.store.events_for_trade("T-DUP")],
            ["ENTRY_FILLED"],
        )

    def test_unresolvable_response_stays_unknown_and_is_never_resubmitted(self):
        cid = self.service.client_order_id("T-UNK", PURPOSE_ENTRY)
        self.double.submit_lost[cid] = 99
        self.double.fetch_not_found.add(cid)

        with self.assertRaises(UnresolvedOrderState):
            self.service.open_entry(
                trade_id="T-UNK", symbol=SYMBOL, side="long", quantity=4,
                leverage=10, event_time=NOW,
            )

        intent_id = self.service.intent_id("T-UNK", PURPOSE_ENTRY)
        intent = self.store.get_order_intent(intent_id)
        self.assertEqual(intent.status, IntentStatus.UNKNOWN.value)
        self.assertEqual(self.store.fills_for_trade("T-UNK"), [])
        self.assertEqual(self.store.events_for_trade("T-UNK"), [])
        trade = self.store.get_trade("T-UNK")
        self.assertEqual(trade.state, "CREATED")
        self.assertEqual(float(trade.initial_quantity), 0.0)

        calls_before = len(self.double.submit_calls)
        resubmit = self.service.prepare_intent(
            trade_id="T-UNK", symbol=SYMBOL, side="buy", purpose=PURPOSE_ENTRY,
            quantity=4, order_type="MARKET",
        )
        with self.assertRaises(UnresolvedOrderState):
            self.service.submit(resubmit, role="entry", lifecycle_side="long")
        self.assertEqual(
            len(self.double.submit_calls), calls_before,
            "an UNKNOWN intent must be reconciled, never resubmitted",
        )


class FailureAccountingTests(ExternalExecutionTestBase):
    def test_lost_response_resolved_by_query_is_recorded_once(self):
        cid = self.service.client_order_id("T-REC", PURPOSE_ENTRY)
        self.double.lost_after_execution[cid] = 99  # every submit loses its response

        entry = self.service.open_entry(
            trade_id="T-REC", symbol=SYMBOL, side="long", quantity=4,
            leverage=10, event_time=NOW,
        )
        self.assertEqual(entry.filled_quantity, 4.0)
        self.assertEqual(len(self.store.fills_for_trade("T-REC")), 1)
        self.assertEqual(len(self.store.events_for_trade("T-REC")), 1)

    def test_partial_fill_never_invents_quantity(self):
        cid = self.service.client_order_id("T-PART", PURPOSE_ENTRY)
        self.double.partial_quantity[cid] = 3.0

        entry = self.service.open_entry(
            trade_id="T-PART", symbol=SYMBOL, side="long", quantity=10,
            leverage=10, event_time=NOW,
        )
        self.assertEqual(entry.filled_quantity, 3.0)
        intent = self.store.get_order_intent(self.service.intent_id("T-PART", PURPOSE_ENTRY))
        self.assertEqual(intent.status, IntentStatus.PARTIALLY_FILLED.value)
        self.assertEqual(self.store.fills_for_trade("T-PART")[0].quantity, 3.0)
        trade = self.store.get_trade("T-PART")
        self.assertEqual(float(trade.initial_quantity), 3.0)
        self.assertEqual(float(trade.remaining_quantity), 3.0)

    def test_protection_is_sized_from_the_acknowledged_fill(self):
        cid = self.service.client_order_id("T-SIZE", PURPOSE_ENTRY)
        self.double.partial_quantity[cid] = 3.0
        entry = self.service.open_entry(
            trade_id="T-SIZE", symbol=SYMBOL, side="long", quantity=10,
            leverage=10, event_time=NOW,
        )
        protection = self.service.confirm_protection(
            trade_id="T-SIZE", symbol=SYMBOL, side="long",
            filled_quantity=entry.filled_quantity,
            stop_loss=98.0, take_profit_1=102.0, take_profit_2=104.0, event_time=NOW,
        )
        self.assertTrue(protection.confirmed, protection.statuses)
        quantities = {
            record.purpose: float(record.intended_quantity)
            for record in self.store.order_intents_for_trade("T-SIZE")
        }
        self.assertEqual(quantities[PURPOSE_STOP_LOSS], 3.0)
        self.assertEqual(quantities[PURPOSE_TAKE_PROFIT_1], 1.5)
        self.assertEqual(quantities[PURPOSE_TAKE_PROFIT_2], 1.5)

    def test_rejected_entry_records_no_fill_and_no_lifecycle_event(self):
        self.double.reject.add(self.service.client_order_id("T-REJ", PURPOSE_ENTRY))
        with self.assertRaises(OrderRejected):
            self.service.open_entry(
                trade_id="T-REJ", symbol=SYMBOL, side="long", quantity=4,
                leverage=10, event_time=NOW,
            )
        self.assertEqual(self.store.fills_for_trade("T-REJ"), [])
        self.assertEqual(self.store.events_for_trade("T-REJ"), [])
        self.assertEqual(
            self.store.get_order_intent(self.service.intent_id("T-REJ", PURPOSE_ENTRY)).status,
            IntentStatus.REJECTED.value,
        )
        self.assertEqual(self.store.get_trade("T-REJ").state, "CREATED")

    def test_unconfirmed_stop_blocks_acceptance_and_flattens_the_fill(self):
        self.double.reject.add(self.service.client_order_id("T-SL", PURPOSE_STOP_LOSS))
        with self.assertRaises(ProtectionNotConfirmed) as ctx:
            self.open_ok("T-SL")

        self.assertIn(PURPOSE_STOP_LOSS, str(ctx.exception))
        self.assertEqual(
            self.store.get_order_intent(self.service.intent_id("T-SL", PURPOSE_STOP_LOSS)).status,
            IntentStatus.REJECTED.value,
        )
        self.assertIsNone(self.store.find_event("T-SL", "PROTECTION_PLACED"))
        self.assertEqual(
            [event.event_type for event in self.store.events_for_trade("T-SL")],
            ["ENTRY_FILLED", "EMERGENCY_EXIT", "TRADE_CLOSED"],
        )
        self.assertEqual(
            [fill.role for fill in self.store.fills_for_trade("T-SL")], ["entry", "exit"]
        )
        trade = self.store.get_trade("T-SL")
        self.assertEqual(trade.state, "CLOSED")
        self.assertEqual(float(trade.remaining_quantity), 0.0)
        self.assertEqual(trade.final_exit_reason, "EMERGENCY_EXIT")

    def test_confirmation_requires_a_query_not_the_create_response(self):
        # Create says NEW, but the order cannot be seen afterwards.
        self.double.fetch_not_found.add(self.service.client_order_id("T-ACK", PURPOSE_STOP_LOSS))
        with self.assertRaises(ProtectionNotConfirmed):
            self.open_ok("T-ACK")
        stop = self.store.get_order_intent(self.service.intent_id("T-ACK", PURPOSE_STOP_LOSS))
        self.assertEqual(
            stop.status, IntentStatus.UNKNOWN.value,
            "an invisible resting order is unknown, not accepted",
        )

    def test_terminal_intent_state_cannot_be_rewritten(self):
        self.service.open_entry(
            trade_id="T-TERM", symbol=SYMBOL, side="long", quantity=4,
            leverage=10, event_time=NOW,
        )
        intent_id = self.service.intent_id("T-TERM", PURPOSE_ENTRY)
        for illegal in ("REJECTED", "CANCELED", "PARTIALLY_FILLED", "UNKNOWN"):
            with self.assertRaises(ValueError, msg=f"{illegal} should be refused"):
                self.store.update_order_intent(intent_id, status=illegal)
        self.assertEqual(self.store.get_order_intent(intent_id).status, IntentStatus.FILLED.value)


class RestartRecoveryTests(ExternalExecutionTestBase):
    def test_restart_rebuilds_the_position_without_duplicating_anything(self):
        _entry, protection = self.open_ok("T-RS", quantity=4.0)
        self.assertTrue(protection.confirmed)
        tp1_cid = self.service.client_order_id("T-RS", PURPOSE_TAKE_PROFIT_1)
        # TP1 fills on the venue while the process is down.
        self.double.fill_resting(tp1_cid, 2.0)

        store, service = self.restart()
        engine = self.make_engine(store)
        report = service.recover(execution_service=engine)

        self.assertTrue(report.is_clean, report)
        self.assertEqual(report.hydrated_trades, ["T-RS"])
        self.assertEqual(
            report.observed_offline_fills, [service.intent_id("T-RS", PURPOSE_TAKE_PROFIT_1)]
        )
        self.assertEqual(report.pending_management, ["T-RS"])
        self.assertEqual(report.duplicate_fills_prevented, 0)

        self.assertEqual(
            sorted(fill.role for fill in store.fills_for_trade("T-RS")), ["entry", "exit"]
        )
        self.assertEqual(
            [event.event_type for event in store.events_for_trade("T-RS")],
            ["ENTRY_FILLED", "PROTECTION_PLACED", "TAKE_PROFIT_1"],
        )
        trade = store.get_trade("T-RS")
        self.assertEqual(trade.state, "PARTIALLY_CLOSED")
        self.assertEqual(float(trade.remaining_quantity), 2.0)
        position = engine.positions[SYMBOL]
        self.assertEqual(position.initial_quantity, 4.0)
        self.assertEqual(position.remaining_quantity, 2.0)
        self.assertTrue(position.lifecycle.tp1_processed)
        self.assertIsNone(store.find_event("T-RS", "BE_UPDATED"), "recovery must not invent a BE amend")

        # A second recovery must be a no-op on identity.
        before = (
            len(store.order_intents_for_trade("T-RS")),
            len(store.fills_for_trade("T-RS")),
            len(store.events_for_trade("T-RS")),
            engine.balance,
        )
        second = service.recover(execution_service=engine)
        after = (
            len(store.order_intents_for_trade("T-RS")),
            len(store.fills_for_trade("T-RS")),
            len(store.events_for_trade("T-RS")),
            engine.balance,
        )
        self.assertEqual(before, after, "recovery must be idempotent")
        self.assertTrue(second.is_clean, second)
        self.assertEqual(second.hydrated_trades, ["T-RS"])

    def test_hydrated_accounting_matches_the_recorded_fills(self):
        self.open_ok("T-EQ", quantity=4.0)
        store, service = self.restart()
        engine = self.make_engine(store)
        report = service.recover(execution_service=engine)
        self.assertTrue(report.is_clean, report)

        # entry: notional 400, margin 40, fee 0.16
        self.assertAlmostEqual(engine.balance, 10_000.0 - 40.0 - 0.16, places=6)
        self.assertAlmostEqual(engine.positions[SYMBOL].margin_locked, 40.0, places=6)
        self.assertAlmostEqual(engine.equity({SYMBOL: 100.0}), 10_000.0 - 0.16, places=6)

        # Re-persisting the hydrated lifecycle must not write new rows.
        rows_before = (
            len(store.fills_for_trade("T-EQ")),
            len(store.events_for_trade("T-EQ")),
        )
        engine._persist_lifecycle(engine.positions[SYMBOL].lifecycle)
        self.assertEqual(
            rows_before,
            (len(store.fills_for_trade("T-EQ")), len(store.events_for_trade("T-EQ"))),
        )

    def test_recovery_derives_the_event_a_crash_dropped(self):
        self.service.open_entry(
            trade_id="T-CRASH", symbol=SYMBOL, side="long", quantity=4,
            leverage=10, event_time=NOW,
        )
        # Crash between the fill write and the lifecycle event write.
        LifecycleEventRecord.delete().where(LifecycleEventRecord.trade == "T-CRASH").execute()
        self.store.update_trade(
            "T-CRASH", state="CREATED", initial_quantity=0.0, remaining_quantity=0.0
        )

        store, service = self.restart()
        engine = self.make_engine(store)
        report = service.recover(execution_service=engine)

        self.assertEqual(
            [event.event_type for event in store.events_for_trade("T-CRASH")], ["ENTRY_FILLED"]
        )
        trade = store.get_trade("T-CRASH")
        self.assertEqual(trade.state, "OPEN")
        self.assertEqual(float(trade.initial_quantity), 4.0)
        self.assertEqual(float(trade.remaining_quantity), 4.0)
        self.assertEqual(report.protection_unconfirmed, ["T-CRASH"])
        self.assertNotIn(SYMBOL, engine.positions)
        self.assertFalse(report.is_clean)

    def test_recovery_reports_unresolved_instead_of_assuming_success(self):
        cid = self.service.client_order_id("T-LOST", PURPOSE_ENTRY)
        self.double.submit_lost[cid] = 99
        self.double.fetch_lost.add(cid)
        with self.assertRaises(UnresolvedOrderState):
            self.service.open_entry(
                trade_id="T-LOST", symbol=SYMBOL, side="long", quantity=4,
                leverage=10, event_time=NOW,
            )

        store, service = self.restart()
        engine = self.make_engine(store)
        report = service.recover(execution_service=engine)

        self.assertFalse(report.is_clean)
        self.assertEqual(len(report.unresolved), 1)
        self.assertEqual(report.unresolved[0]["status_after"], IntentStatus.UNKNOWN.value)
        self.assertEqual(
            store.get_order_intent(service.intent_id("T-LOST", PURPOSE_ENTRY)).status,
            IntentStatus.UNKNOWN.value,
        )
        self.assertEqual(store.fills_for_trade("T-LOST"), [])
        self.assertNotIn(SYMBOL, engine.positions)
        self.assertIn("T-LOST", report.protection_unconfirmed)

    def test_vanished_resting_stop_is_reported_not_ignored(self):
        self.open_ok("T-GONE", quantity=4.0)
        self.double.fetch_not_found.add(self.service.client_order_id("T-GONE", PURPOSE_STOP_LOSS))

        store, service = self.restart()
        engine = self.make_engine(store)
        report = service.recover(execution_service=engine)

        self.assertFalse(report.is_clean)
        stop = store.get_order_intent(service.intent_id("T-GONE", PURPOSE_STOP_LOSS))
        self.assertEqual(stop.status, IntentStatus.UNKNOWN.value)
        self.assertEqual(report.protection_unconfirmed, ["T-GONE"])
        self.assertNotIn(SYMBOL, engine.positions)


class StoreContractTests(ExternalExecutionTestBase):
    def test_existing_store_is_upgraded_with_the_new_columns(self):
        self.store.close()
        connection = sqlite3.connect(self.path)
        connection.execute("ALTER TABLE vb_order_intents DROP COLUMN purpose")
        connection.execute("ALTER TABLE vb_order_intents DROP COLUMN filled_quantity")
        connection.execute("ALTER TABLE vb_trade_lifecycles DROP COLUMN leverage")
        connection.commit()
        connection.close()

        store = VersionBStore(self.path)
        intent_columns = {
            row[1] for row in sqlite3.connect(self.path).execute("PRAGMA table_info(vb_order_intents)")
        }
        trade_columns = {
            row[1]
            for row in sqlite3.connect(self.path).execute("PRAGMA table_info(vb_trade_lifecycles)")
        }
        self.assertIn("purpose", intent_columns)
        self.assertIn("filled_quantity", intent_columns)
        self.assertIn("leverage", trade_columns)
        store.close()
        self.store = VersionBStore(self.path)

    def test_conflicting_intent_identity_is_refused(self):
        self.service.open_entry(
            trade_id="T-CONF", symbol=SYMBOL, side="long", quantity=4,
            leverage=10, event_time=NOW,
        )
        with self.assertRaises(ValueError):
            self.store.get_or_create_order_intent(
                order_intent_id=self.service.intent_id("T-CONF", PURPOSE_ENTRY),
                trade="T-CONF",
                client_order_id="some-other-client-id",
                symbol=SYMBOL,
                side="buy",
                order_type="MARKET",
                intended_quantity=4,
                status="CREATED",
                purpose=PURPOSE_ENTRY,
            )


if __name__ == "__main__":
    unittest.main()
