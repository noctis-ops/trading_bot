"""Deterministic Paper adapter checks for the Version B lifecycle contract."""

import unittest

from core.paper_trading import PaperTradingExchange


class VersionBPaperTests(unittest.TestCase):
    def setUp(self):
        self.price = 100.0
        self.exchange = PaperTradingExchange(initial_balance=1000.0)
        self.exchange.fetch_ticker = lambda symbol: {"close": self.price}
        self.exchange.set_leverage("BTC/USDT", 5)

    def test_one_trade_id_spans_tp1_be_and_tp2_and_tp1_is_idempotent(self):
        entry = self.exchange.create_market_order("BTC/USDT", "buy", 1.0)
        self.assertTrue(entry)
        self.exchange.create_stop_loss_order("BTC/USDT", "sell", 1.0, 98.0)
        self.exchange.create_take_profit_order("BTC/USDT", "sell", 0.5, 102.0)
        self.exchange.create_take_profit_order("BTC/USDT", "sell", 0.5, 104.0)
        self.assertTrue(self.exchange.record_protection("BTC/USDT"))

        position = self.exchange.get_position_by_symbol("BTC/USDT")
        trade_id = position["trade_id"]

        self.price = 102.0
        first = self.exchange.check_and_trigger_orders()
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0]["reason"], "TAKE_PROFIT_1")
        self.assertEqual(first[0]["trade_id"], trade_id)
        self.assertEqual(len(self.exchange.get_lifecycle(trade_id).events), 4)  # entry, protection, TP1, BE

        # The market price remains at TP1; a second monitor cycle is a no-op.
        self.assertEqual(self.exchange.check_and_trigger_orders(), [])

        self.price = 104.0
        second = self.exchange.check_and_trigger_orders()
        self.assertEqual(len(second), 1)
        self.assertEqual(second[0]["reason"], "TAKE_PROFIT_2")
        lifecycle = self.exchange.get_lifecycle(trade_id)
        self.assertEqual(lifecycle.state.value, "CLOSED")
        self.assertTrue(all(event.trade_id == trade_id for event in lifecycle.events))
        self.assertEqual(len(self.exchange.trade_history), 2)

        stats = self.exchange.get_paper_stats()
        self.assertEqual(stats["total_trades"], 1)
        self.assertEqual(stats["open_positions"], 0)

    def test_configured_leverage_changes_margin_but_not_notional(self):
        self.exchange.create_market_order("BTC/USDT", "buy", 1.0)
        position = self.exchange.get_position_by_symbol("BTC/USDT")
        self.assertEqual(position["leverage"], 5)
        self.assertAlmostEqual(position["margin_locked"], position["notional"] / 5)


if __name__ == "__main__":
    unittest.main()
