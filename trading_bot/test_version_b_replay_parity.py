"""Replay integration tests: Backtest and deterministic Paper are one path."""

import unittest

import pandas as pd

from backtesting.version_b_backtest import VersionBBacktestEngine
from core.version_b_paper import VersionBPaperEngine
from test_version_b_backtest import FixtureStrategy


class FixtureMarketData:
    def __init__(self, frames):
        self.frames = frames
        self.calls = []

    def get_complete_dataframe(self, symbol, timeframe):
        self.calls.append((symbol, timeframe))
        return self.frames[timeframe].copy()


def replay_frames():
    end = pd.Timestamp("2026-01-01 00:00:00", tz="UTC")
    one_hour = pd.date_range(end=end, periods=30, freq="1h")
    fifteen = pd.date_range(end=end, periods=120, freq="15min")
    five = pd.date_range(end=end, periods=360, freq="5min")

    def frame(index, highs=None):
        highs = highs or {}
        return pd.DataFrame({
            "open": 100.0,
            "high": [highs.get(i, 100.2) for i in range(len(index))],
            "low": 100.1,
            "close": 100.0,
        }, index=index)

    entry_idx = int((one_hour[24] - five[0]).total_seconds() / 300)
    return {
        "1h": frame(one_hour),
        "15m": frame(fifteen),
        "5m": frame(five, {entry_idx + 1: 102.0, entry_idx + 4: 104.5}),
    }


class VersionBReplayParityTests(unittest.TestCase):
    def test_backtest_and_paper_have_identical_decisions_and_trade_outcomes(self):
        frames = replay_frames()
        backtest = VersionBBacktestEngine(
            initial_balance=1000, fee_rate=0.0, slippage_rate=0.0,
            strategy=FixtureStrategy(),
        )
        paper = VersionBPaperEngine(
            initial_balance=1000, fee_rate=0.0, slippage_rate=0.0,
            strategy=FixtureStrategy(),
        )
        replay_kwargs = {
            "df_1h": frames["1h"],
            "df_15m": frames["15m"],
            "df_5m": frames["5m"],
            "symbol": "BTC/USDT",
        }
        historical = backtest.run(**replay_kwargs)
        deterministic = paper.run(**replay_kwargs)

        self.assertEqual(historical["execution_model_version"], deterministic["execution_model_version"])
        self.assertEqual(historical["decisions"], deterministic["decisions"])
        self.assertEqual(historical["signals"], deterministic["signals"])
        self.assertEqual(historical["total_trades"], deterministic["total_trades"])
        self.assertEqual(historical["total_profit"], deterministic["total_profit"])
        self.assertEqual(historical["trades"][0]["exit_type"], "TAKE_PROFIT_2")

    def test_trading_bot_explicit_b_path_uses_injected_market_data_only(self):
        from core.bot import TradingBot

        frames = replay_frames()
        market_data = FixtureMarketData(frames)
        bot = TradingBot(
            version_b=True,
            market_data=market_data,
            strategy=FixtureStrategy(),
            version_b_initial_balance=1000,
            version_b_fee_rate=0.0,
            version_b_slippage_rate=0.0,
        )
        report = bot.run_version_b_once(symbol="BTC/USDT")
        self.assertEqual(report["total_trades"], 1)
        self.assertEqual(
            market_data.calls,
            [("BTC/USDT", "1h"), ("BTC/USDT", "15m"), ("BTC/USDT", "5m")],
        )
        self.assertIsNone(bot.exchange)
        self.assertEqual(report["trades"][0]["exit_type"], "TAKE_PROFIT_2")


if __name__ == "__main__":
    unittest.main()
