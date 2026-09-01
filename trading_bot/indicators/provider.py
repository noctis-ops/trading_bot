"""Canonical indicator provider for Version B.

The formulas intentionally mirror the currently active MarketData formulas.
This module centralizes them; it does not introduce a new indicator model.
"""

from __future__ import annotations

from typing import Any, Mapping

import numpy as np
import pandas as pd


class IndicatorProvider:
    """Compute the project's current indicator columns deterministically."""

    name = "market-data-formulas"
    implementation_version = "1.0.0"

    @staticmethod
    def _get(config: Any, *path: str, default: Any = None) -> Any:
        if hasattr(config, "get") and not isinstance(config, Mapping):
            return config.get(*path, default=default)
        value: Any = config
        for key in path:
            if not isinstance(value, Mapping) or key not in value:
                return default
            value = value[key]
        return value

    @staticmethod
    def calculate_ema(series: pd.Series, period: int) -> pd.Series:
        return series.ewm(span=period, adjust=False).mean()

    @staticmethod
    def calculate_rsi(series: pd.Series, period: int) -> pd.Series:
        delta = series.diff()
        gain = delta.where(delta > 0, 0).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / loss.replace(0, np.nan)
        return 100 - (100 / (1 + rs))

    @staticmethod
    def calculate_macd(
        series: pd.Series,
        fast: int,
        slow: int,
        signal: int,
    ) -> tuple[pd.Series, pd.Series, pd.Series]:
        ema_fast = series.ewm(span=fast, adjust=False).mean()
        ema_slow = series.ewm(span=slow, adjust=False).mean()
        macd = ema_fast - ema_slow
        signal_line = macd.ewm(span=signal, adjust=False).mean()
        return macd, signal_line, macd - signal_line

    @staticmethod
    def calculate_atr(df: pd.DataFrame, period: int) -> pd.Series:
        high = df["high"]
        low = df["low"]
        close = df["close"]
        tr = pd.concat(
            [high - low, (high - close.shift()).abs(), (low - close.shift()).abs()],
            axis=1,
        ).max(axis=1)
        return tr.rolling(window=period).mean()

    @classmethod
    def calculate_adx(cls, df: pd.DataFrame, period: int) -> pd.Series:
        # This intentionally preserves the currently active MarketData
        # formula, including its shared TR denominator semantics.
        high = df["high"]
        low = df["low"]
        plus_dm = high.diff().copy()
        minus_dm = low.diff().copy()
        plus_dm[plus_dm < 0] = 0
        minus_dm[minus_dm > 0] = 0
        minus_dm = minus_dm.abs()

        tr = cls.calculate_atr(df, 1)
        denom_plus = tr.rolling(period).sum().replace(0, np.nan)
        denom_minus = tr.rolling(period).sum().replace(0, np.nan)
        plus_di = 100 * (plus_dm.rolling(period).sum() / denom_plus)
        minus_di = 100 * (minus_dm.rolling(period).sum() / denom_minus)
        di_sum = (plus_di + minus_di).replace(0, np.nan)
        dx = 100 * (plus_di - minus_di).abs() / di_sum
        return dx.rolling(period).mean()

    @staticmethod
    def calculate_bollinger(series: pd.Series, period: int, std_dev: float):
        middle = series.rolling(window=period).mean()
        std = series.rolling(window=period).std()
        return middle + std * std_dev, middle, middle - std * std_dev

    @classmethod
    def add_indicators(cls, df: pd.DataFrame, config: Any) -> pd.DataFrame:
        """Return a copy with the same indicator columns expected by Strategy."""
        if df is None or df.empty:
            return pd.DataFrame() if df is None else df.copy()
        result = df.copy()
        close = result["close"]

        ema_fast_period = int(cls._get(config, "strategy", "indicators", "ema_fast", default=50))
        ema_slow_period = int(cls._get(config, "strategy", "indicators", "ema_slow", default=200))
        ema_medium_period = int(cls._get(config, "strategy", "indicators", "ema_medium", default=21))
        rsi_period = int(cls._get(config, "strategy", "momentum", "rsi_period", default=14))
        macd_fast = int(cls._get(config, "strategy", "momentum", "macd_fast", default=12))
        macd_slow = int(cls._get(config, "strategy", "momentum", "macd_slow", default=26))
        macd_signal = int(cls._get(config, "strategy", "momentum", "macd_signal", default=9))
        atr_period = int(cls._get(config, "strategy", "volatility", "atr_period", default=14))
        bb_period = int(cls._get(config, "strategy", "volatility", "bb_period", default=20))
        bb_std = float(cls._get(config, "strategy", "volatility", "bb_std_dev", default=2.0))
        adx_period = int(cls._get(config, "strategy", "trend", "adx_period", default=14))
        volume_period = int(cls._get(config, "strategy", "volume", "volume_ma_period", default=20))

        result["ema_fast"] = cls.calculate_ema(close, ema_fast_period)
        result["ema_slow"] = cls.calculate_ema(close, ema_slow_period)
        result["ema_medium"] = cls.calculate_ema(close, ema_medium_period)
        result["rsi"] = cls.calculate_rsi(close, rsi_period)
        result["macd"], result["macd_signal"], result["macd_hist"] = cls.calculate_macd(
            close, macd_fast, macd_slow, macd_signal
        )
        result["atr"] = cls.calculate_atr(result, atr_period)
        result["bb_upper"], result["bb_middle"], result["bb_lower"] = cls.calculate_bollinger(
            close, bb_period, bb_std
        )
        result["adx"] = cls.calculate_adx(result, adx_period)
        result["volume_sma"] = result["volume"].rolling(window=volume_period).mean()
        return result
