"""Phase 5 tests for the Version B execution model and lifecycle."""

from datetime import datetime, timezone
import unittest

from core.execution_model import EXECUTION_MODEL_VERSION, evaluate_intrabar
from core.risk_model import FillLeg
from core.trade_lifecycle import LifecycleState, TradeLifecycle


class VersionBExecutionModelTests(unittest.TestCase):
    def setUp(self):
        self.long_position = {
            "side": "long",
            "stop_loss": 98.0,
            "take_profit_1": 102.0,
            "take_profit_2": 104.0,
            "tp1_hit": False,
        }
        self.short_position = {
            "side": "short",
            "stop_loss": 102.0,
            "take_profit_1": 98.0,
            "take_profit_2": 96.0,
            "tp1_hit": False,
        }

    def test_stop_first_when_bar_touches_stop_and_target(self):
        decision = evaluate_intrabar(
            self.long_position,
            {"open": 100, "high": 105, "low": 97, "close": 103},
        )
        self.assertEqual(decision.event_type, "STOP_LOSS")
        self.assertEqual(decision.trigger_price, 98.0)
        self.assertEqual(decision.execution_model_version, EXECUTION_MODEL_VERSION)

    def test_tp1_precedes_tp2_and_tp2_is_deferred_same_bar(self):
        decision = evaluate_intrabar(
            self.long_position,
            {"open": 100, "high": 105, "low": 99, "close": 104},
        )
        self.assertEqual(decision.event_type, "TAKE_PROFIT_1")
        self.assertEqual(decision.deferred_target, "TAKE_PROFIT_2")

        after_tp1 = dict(self.long_position, tp1_hit=True)
        next_decision = evaluate_intrabar(
            after_tp1,
            {"open": 103, "high": 105, "low": 102, "close": 104},
        )
        self.assertEqual(next_decision.event_type, "TAKE_PROFIT_2")

    def test_short_uses_mirrored_stop_first_and_adverse_slippage(self):
        decision = evaluate_intrabar(
            self.short_position,
            {"open": 100, "high": 103, "low": 95, "close": 97},
            slippage_rate=0.01,
        )
        self.assertEqual(decision.event_type, "STOP_LOSS")
        self.assertAlmostEqual(decision.fill_price, 102.0 * 1.01)

    def test_adverse_stop_gap_uses_bar_open(self):
        decision = evaluate_intrabar(
            self.long_position,
            {"open": 95, "high": 97, "low": 94, "close": 96},
            slippage_rate=0.01,
        )
        self.assertEqual(decision.event_type, "STOP_LOSS")
        self.assertAlmostEqual(decision.fill_price, 95 * 0.99)

    def test_no_trigger_is_explicit(self):
        decision = evaluate_intrabar(
            self.long_position,
            {"open": 100, "high": 101, "low": 99, "close": 100},
        )
        self.assertFalse(decision.triggered)
        self.assertIsNone(decision.fill_price)


class VersionBTradeLifecycleTests(unittest.TestCase):
    def test_tp1_be_tp2_is_one_lifecycle(self):
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        lifecycle = TradeLifecycle("trade-1", "BTC/USDT", "long")
        lifecycle.record_entry_fill(FillLeg("entry", "long", 100, 10, fee=1), event_time=now)
        lifecycle.record_management_event("PROTECTION_PLACED", event_time=now)
        lifecycle.record_exit_fill(
            FillLeg("exit", "long", 102, 5, fee=0.51),
            "TAKE_PROFIT_1",
            event_time=now,
        )
        lifecycle.record_management_event("BE_UPDATED", {"stop": 100}, event_time=now)
        lifecycle.record_management_event("TRAILING_UPDATED", {"stop": 101}, event_time=now)
        lifecycle.record_exit_fill(
            FillLeg("exit", "long", 104, 5, fee=0.52),
            "TAKE_PROFIT_2",
            event_time=now,
        )

        self.assertEqual(lifecycle.state, LifecycleState.CLOSED)
        self.assertEqual(lifecycle.initial_quantity, 10)
        self.assertEqual(lifecycle.remaining_quantity, 0)
        self.assertTrue(lifecycle.tp1_processed)
        self.assertEqual(len({event.trade_id for event in lifecycle.events}), 1)
        self.assertAlmostEqual(lifecycle.realized_net_pnl, 27.97)

    def test_tp1_cannot_repeat(self):
        lifecycle = TradeLifecycle("trade-2", "ETH/USDT", "long")
        lifecycle.record_entry_fill(FillLeg("entry", "long", 100, 10))
        lifecycle.record_exit_fill(FillLeg("exit", "long", 102, 5), "TAKE_PROFIT_1")
        with self.assertRaises(ValueError):
            lifecycle.record_exit_fill(FillLeg("exit", "long", 102, 2), "TAKE_PROFIT_1")

    def test_closed_lifecycle_rejects_more_events(self):
        lifecycle = TradeLifecycle("trade-3", "SOL/USDT", "long")
        lifecycle.record_entry_fill(FillLeg("entry", "long", 100, 1))
        lifecycle.record_exit_fill(FillLeg("exit", "long", 101, 1), "TAKE_PROFIT_2")
        self.assertEqual(lifecycle.state, LifecycleState.CLOSED)
        with self.assertRaises(ValueError):
            lifecycle.record_management_event("BE_UPDATED")


if __name__ == "__main__":
    unittest.main()
