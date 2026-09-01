"""Deterministic tests for the shared Version B execution service."""

from datetime import datetime, timezone
import unittest

from core.execution_service import VersionBExecutionService
from core.risk_engine import RiskEngine


NOW = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)


class VersionBExecutionServiceTests(unittest.TestCase):
    def make_service(self):
        return VersionBExecutionService(
            initial_balance=1000,
            fee_rate=0.001,
            slippage_rate=0.0,
            move_sl_to_breakeven=True,
        )

    def test_entry_then_tp1_then_tp2_reconciles_balance_and_lifecycle(self):
        service = self.make_service()
        position = service.open_position(
            symbol="BTC/USDT", side="long", entry_price=100,
            quantity=10, stop_loss=98, take_profit_1=102, take_profit_2=104,
            leverage=10, event_time=NOW,
        )
        self.assertEqual(position.lifecycle.trade_id, "VB-BTCUSDT-000001")
        self.assertEqual(service.balance, 899.0)  # margin 100 + entry fee 1

        tp1 = service.process_bar(
            "BTC/USDT", {"open": 100, "high": 102, "low": 99, "close": 102}, event_time=NOW
        )
        self.assertEqual(tp1["event_type"], "TAKE_PROFIT_1")
        self.assertEqual(tp1["quantity"], 5)
        self.assertTrue(service.positions["BTC/USDT"].tp1_hit)
        self.assertEqual(service.positions["BTC/USDT"].stop_loss, 100)

        # A second bar at TP1 does not duplicate the partial exit.
        self.assertIsNone(service.process_bar(
            "BTC/USDT", {"open": 102, "high": 103, "low": 101, "close": 102}, event_time=NOW
        ))

        tp2 = service.process_bar(
            "BTC/USDT", {"open": 103, "high": 104, "low": 102, "close": 104}, event_time=NOW
        )
        self.assertEqual(tp2["event_type"], "TAKE_PROFIT_2")
        self.assertNotIn("BTC/USDT", service.positions)
        self.assertEqual(len(service.closed_lifecycles), 1)
        self.assertEqual(service.closed_lifecycles[0].state.value, "CLOSED")
        self.assertAlmostEqual(service.final_net_pnl("VB-BTCUSDT-000001"), 27.97)
        self.assertAlmostEqual(service.balance, 1027.97)

    def test_stop_first_does_not_record_target(self):
        service = self.make_service()
        service.open_position(
            symbol="ETH/USDT", side="long", entry_price=100,
            quantity=1, stop_loss=98, take_profit_1=102, take_profit_2=104,
            leverage=1,
        )
        event = service.process_bar(
            "ETH/USDT", {"open": 100, "high": 105, "low": 97, "close": 103}
        )
        self.assertEqual(event["event_type"], "STOP_LOSS")
        self.assertNotIn("ETH/USDT", service.positions)

    def test_equity_separates_locked_margin_and_unrealized_pnl(self):
        service = self.make_service()
        service.open_position(
            symbol="SOL/USDT", side="long", entry_price=100,
            quantity=10, stop_loss=98, take_profit_1=102, take_profit_2=104,
            leverage=10,
        )
        self.assertAlmostEqual(service.equity({"SOL/USDT": 101}), 1009.0)

    def test_service_receives_the_same_canonical_sizing_contract(self):
        risk = RiskEngine()
        size = risk.calculate_position_size(
            equity=1000, entry_price=100, stop_price=98, side="long", score=100
        )
        service = VersionBExecutionService(
            initial_balance=2000,
            fee_rate=risk.execution["fee_rate"],
            slippage_rate=risk.execution["slippage_rate"],
        )
        position = service.open_position(
            symbol="BTC/USDT", side="long", entry_price=100,
            quantity=size.quantity, stop_loss=98, take_profit_1=102,
            take_profit_2=104, leverage=size.leverage,
        )
        self.assertAlmostEqual(position.initial_quantity, size.quantity)
        self.assertAlmostEqual(size.planned_risk.total, risk.calculate_planned_risk(
            side="long", entry_price=100, stop_price=98,
            quantity=size.quantity, equity=1000,
        ).total)


if __name__ == "__main__":
    unittest.main()
