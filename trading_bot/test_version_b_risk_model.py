"""Phase 4 tests for canonical risk and accounting definitions."""

import unittest

from core.risk_model import (
    FillLeg,
    PositionRiskInput,
    calculate_daily_loss,
    calculate_equity,
    calculate_planned_risk,
    calculate_portfolio_risk_at_stop,
    calculate_realized_net_pnl,
)


class VersionBRiskModelTests(unittest.TestCase):
    def test_equity_is_not_free_balance_margin_or_notional(self):
        self.assertEqual(calculate_equity(1000, 200, -50, 5), 1145)

    def test_planned_risk_uses_actual_entry_and_modeled_costs(self):
        risk = calculate_planned_risk(
            side="long",
            entry_price=100,
            stop_price=98,
            quantity=10,
            equity=1000,
            fee_rate=0.001,
            stop_slippage_rate=0.01,
        )
        self.assertAlmostEqual(risk.gross_loss, 29.8)
        self.assertAlmostEqual(risk.entry_fee, 1.0)
        self.assertAlmostEqual(risk.stop_exit_fee, 0.9702)
        self.assertAlmostEqual(risk.total, 31.7702)
        self.assertAlmostEqual(risk.percent_of_equity, 3.17702)

    def test_short_planned_risk_has_adverse_stop_direction(self):
        risk = calculate_planned_risk(
            side="short",
            entry_price=100,
            stop_price=102,
            quantity=10,
            equity=1000,
            fee_rate=0.001,
            stop_slippage_rate=0.01,
        )
        self.assertAlmostEqual(risk.gross_loss, 30.2)
        self.assertGreater(risk.total, risk.gross_loss)

    def test_realized_net_pnl_aggregates_partial_exits_into_one_lifecycle(self):
        pnl = calculate_realized_net_pnl(
            [
                FillLeg("entry", "long", 100, 10, fee=1.0, slippage=0.1),
                FillLeg("exit", "long", 102, 5, fee=0.51, slippage=0.05),
                FillLeg("exit", "long", 104, 5, fee=0.52, slippage=0.05),
            ]
        )
        # Actual fill prices already contain the slippage impact; slippage is
        # attributed, not deducted a second time.
        self.assertAlmostEqual(pnl, 27.97)

    def test_short_realized_net_pnl(self):
        pnl = calculate_realized_net_pnl(
            [
                FillLeg("entry", "short", 100, 10, fee=1.0),
                FillLeg("exit", "short", 97, 10, fee=0.97),
            ]
        )
        self.assertAlmostEqual(pnl, 28.03)

    def test_daily_loss_is_equity_based_and_never_negative(self):
        self.assertEqual(calculate_daily_loss(1000, 925), 75)
        self.assertEqual(calculate_daily_loss(1000, 1050), 0)

    def test_portfolio_risk_at_stop_includes_candidate(self):
        positions = [
            PositionRiskInput("BTC/USDT", "long", 100, 98, 10),
            PositionRiskInput("ETH/USDT", "short", 100, 102, 5),
        ]
        candidate = PositionRiskInput("SOL/USDT", "long", 50, 49, 20)
        total = calculate_portfolio_risk_at_stop(
            positions,
            candidate=candidate,
            equity=1000,
            fee_rate=0,
            stop_slippage_rate=0,
        )
        self.assertEqual(total, 20 + 10 + 20)

    def test_invalid_lifecycle_is_rejected(self):
        with self.assertRaises(ValueError):
            calculate_realized_net_pnl([FillLeg("entry", "long", 100, 1)])
        with self.assertRaises(ValueError):
            calculate_planned_risk(
                side="long", entry_price=100, stop_price=101,
                quantity=1, equity=1000,
            )


if __name__ == "__main__":
    unittest.main()
