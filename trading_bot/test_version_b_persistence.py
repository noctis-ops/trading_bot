"""Phase 6 tests for Version B durable persistence and recovery."""

from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from database.version_b_store import VersionBStore


NOW = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)


class VersionBPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tempdir.name) / "version_b.sqlite"
        self.store = VersionBStore(self.path)
        self.store.create_run(
            run_id="run-1",
            environment="paper",
            code_version="test-code",
            strategy_version="1.3",
            config_hash="config-hash",
            data_hash="data-hash",
            execution_model_version="vb-1.0-5m-stop-first",
            universe=["BTC/USDT"],
            effective_config={"strategy": {"volume": {"effective_gate": "volume > volume_sma"}}},
            created_at=NOW,
        )

    def tearDown(self):
        self.store.close()
        self.tempdir.cleanup()

    def test_decision_snapshot_and_signal_identity_are_idempotent(self):
        first = self.store.record_decision(
            signal_id="signal-1",
            run_id="run-1",
            symbol="BTC/USDT",
            direction="long",
            decision_time=NOW,
            timeframe_timestamps={"1h": "2026-01-01T10:00:00+00:00", "15m": "2026-01-01T10:00:00+00:00"},
            outcome="BLOCKED",
            reason="NO_SIGNAL",
            snapshot={"gates": {"adx": False}, "score": 42},
            strategy_version="1.3",
            config_hash="config-hash",
        )
        second = self.store.record_decision(
            signal_id="signal-1",
            run_id="run-1",
            symbol="BTC/USDT",
            direction="long",
            decision_time=NOW,
            timeframe_timestamps={"1h": "2026-01-01T10:00:00+00:00", "15m": "2026-01-01T10:00:00+00:00"},
            outcome="BLOCKED",
            reason="NO_SIGNAL",
            snapshot={"different": "ignored because identity is same"},
            strategy_version="1.3",
            config_hash="config-hash",
        )
        self.assertEqual(first.signal_id, second.signal_id)

        with self.assertRaises(ValueError):
            self.store.record_decision(
                signal_id="signal-1",
                run_id="run-1",
                symbol="ETH/USDT",
                direction="long",
                decision_time=NOW,
                timeframe_timestamps={},
                outcome="BLOCKED",
                reason="NO_SIGNAL",
                snapshot={},
                strategy_version="1.3",
                config_hash="config-hash",
            )

    def test_trade_events_and_fills_recover_after_restart(self):
        self.store.create_trade(
            trade_id="trade-1",
            run_id="run-1",
            signal_id="signal-1",
            symbol="BTC/USDT",
            side="long",
            state="OPEN",
            initial_quantity=10,
            remaining_quantity=10,
            opened_at=NOW,
        )
        self.store.create_order_intent(
            order_intent_id="intent-1",
            trade="trade-1",
            client_order_id="client-1",
            exchange_order_id="exchange-1",
            symbol="BTC/USDT",
            side="buy",
            order_type="market",
            intended_quantity=10,
            intended_price=100,
            status="FILLED",
            created_at=NOW,
            confirmed_at=NOW,
        )
        self.store.append_event(
            event_id="trade-1:1:ENTRY_FILLED",
            trade_id="trade-1",
            sequence=1,
            event_type="ENTRY_FILLED",
            event_time=NOW,
            payload={"price": 100, "quantity": 10},
        )
        self.store.append_event(
            event_id="trade-1:2:TAKE_PROFIT_1",
            trade_id="trade-1",
            sequence=2,
            event_type="TAKE_PROFIT_1",
            event_time=NOW,
            payload={"price": 102, "quantity": 5},
        )
        self.store.record_fill(
            fill_id="fill-entry",
            trade="trade-1",
            order_intent="intent-1",
            role="entry",
            side="long",
            fill_time=NOW,
            price=100,
            quantity=10,
            fee=1,
            slippage=0.1,
        )
        self.store.update_trade("trade-1", state="PARTIALLY_CLOSED", remaining_quantity=5)
        self.store.close()

        restarted = VersionBStore(self.path)
        recovered = restarted.reconstruct_trade("trade-1")
        self.assertEqual(recovered["trade_id"], "trade-1")
        self.assertEqual(recovered["state"], "PARTIALLY_CLOSED")
        self.assertEqual(len(recovered["events"]), 2)
        self.assertEqual(recovered["events"][1]["event_type"], "TAKE_PROFIT_1")
        self.assertEqual(recovered["fills"][0]["fill_id"], "fill-entry")
        restarted.close()

    def test_event_identity_and_sequence_prevent_duplicates(self):
        self.store.create_trade(
            trade_id="trade-2", run_id="run-1", signal_id=None,
            symbol="ETH/USDT", side="short", state="OPEN",
            initial_quantity=1, remaining_quantity=1, opened_at=NOW,
        )
        first = self.store.append_event(
            event_id="trade-2:1:ENTRY_FILLED", trade_id="trade-2", sequence=1,
            event_type="ENTRY_FILLED", event_time=NOW,
        )
        second = self.store.append_event(
            event_id="trade-2:1:ENTRY_FILLED", trade_id="trade-2", sequence=1,
            event_type="ENTRY_FILLED", event_time=NOW,
        )
        self.assertEqual(first.event_id, second.event_id)
        with self.assertRaises(ValueError):
            self.store.append_event(
                event_id="trade-2:2:OTHER", trade_id="trade-2", sequence=1,
                event_type="OTHER", event_time=NOW,
            )
        self.store.record_fill(
            fill_id="fill-duplicate", trade="trade-2", order_intent=None,
            role="entry", side="short", fill_time=NOW, price=100, quantity=1,
        )
        with self.assertRaises(Exception):
            self.store.record_fill(
                fill_id="fill-duplicate", trade="trade-2", order_intent=None,
                role="entry", side="short", fill_time=NOW, price=100, quantity=1,
            )

    def test_backup_is_created(self):
        target = Path(self.tempdir.name) / "backups" / "snapshot.sqlite"
        self.store.backup_to(target)
        self.assertTrue(target.exists())
        self.assertGreater(target.stat().st_size, 0)


if __name__ == "__main__":
    unittest.main()
