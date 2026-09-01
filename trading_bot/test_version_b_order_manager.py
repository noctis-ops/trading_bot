"""Safety checks for the OrderManager protection gate."""

import unittest

from core.order_manager import OrderManager


class FakeRiskManager:
    def register_trade_result(self, **kwargs):
        pass


class UnprotectedExchange:
    def __init__(self):
        self.calls = []

    def set_leverage(self, symbol, leverage):
        self.calls.append(("leverage", symbol, leverage))

    def set_margin_type(self, symbol, margin_type):
        self.calls.append(("margin", symbol, margin_type))

    def create_market_order(self, symbol, side, amount, params=None):
        self.calls.append(("market", symbol, side, amount))
        return {"id": "market-1", "price": 100.0, "fee": 0.1}

    def create_stop_loss_order(self, symbol, side, amount, stop_price):
        self.calls.append(("stop", symbol, side, amount, stop_price))
        return {}

    def create_take_profit_order(self, symbol, side, amount, stop_price):
        self.calls.append(("tp", symbol, side, amount, stop_price))
        return {"id": "tp-1"}


class OrderManagerSafetyTests(unittest.TestCase):
    def test_open_is_rejected_and_emergency_close_is_attempted_without_stop(self):
        exchange = UnprotectedExchange()
        manager = OrderManager(exchange, FakeRiskManager())
        manager.MAX_RETRY_ATTEMPTS = 1
        result = manager.open_position(
            "BTC/USDT",
            {"entry_price": 100.0},
            {"contract_size": 1.0, "leverage": 2},
            {
                "valid": True,
                "stop_loss": 98.0,
                "take_profit_1": 102.0,
                "take_profit_2": 104.0,
                "risk_reward_ratio": 2.0,
            },
        )
        self.assertFalse(result["success"])
        self.assertIn("stop-loss", result["error"])
        self.assertNotIn("BTC/USDT", manager.open_positions)
        self.assertEqual([call[0] for call in exchange.calls].count("market"), 2)


if __name__ == "__main__":
    unittest.main()
