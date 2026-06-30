"""
═══════════════════════════════════════════════════════════
جلب ومعالجة بيانات السوق — Market Data Module
═══════════════════════════════════════════════════════════
يتعامل مع جلب الشموع وتحويلها إلى DataFrame مع المؤشرات

التحسينات الإضافية على الأصل:
  - استخدام indicators/ للحسابات المشتركة
  - add_indicators يدعم DataFrames من مصادر مختلفة
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import yaml
import time

from utils.logger import logger

# ─────────────────────────────────────────────────────────
# قراءة الإعدادات
# ─────────────────────────────────────────────────────────

CONFIG_PATH = Path(__file__).parent.parent / 'config.yaml'

with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
    config = yaml.safe_load(f)

# ─────────────────────────────────────────────────────────
# فئة MarketData الرئيسية
# ─────────────────────────────────────────────────────────

class MarketData:
    """
    فئة للتعامل مع بيانات السوق

    المسؤوليات:
    1. جلب الشموع من بينانس (أو Paper Trading Engine)
    2. تحويلها إلى DataFrame
    3. إضافة المؤشرات الفنية
    4. تخزين البيانات مؤقتاً (caching)
    5. تحديث البيانات تلقائياً
    """

    def __init__(self, exchange):
        """
        تهيئة فئة معالجة البيانات

        Args:
            exchange: instance من BinanceExchange أو PaperTradingExchange
        """
        self.exchange        = exchange
        self.cache           = {}        # cache الشموع
        self.last_fetch      = {}        # آخر وقت جلب لكل زوج/timeframe
        self.lookback_period = config['trading']['lookback_period']

        logger.success("✅ فئة MarketData جاهزة")

    # ═════════════════════════════════════════════════════
    # جلب البيانات الخام
    # ═════════════════════════════════════════════════════

    def fetch_candles(
        self,
        symbol:    str,
        timeframe: str,
        limit:     int = None
    ) -> pd.DataFrame:
        """
        جلب الشموع وتحويلها إلى DataFrame

        Args:
            symbol:    الزوج (مثل 'ETH/USDT')
            timeframe: الإطار الزمني (مثل '15m', '1h')
            limit:     عدد الشموع (افتراضي: من config)

        Returns:
            DataFrame بالشموع مُفهرَس بالـ timestamp
        """
        if limit is None:
            limit = self.lookback_period

        try:
            ohlcv = self.exchange.fetch_ohlcv(symbol, timeframe, limit)

            if not ohlcv:
                logger.warning(f"⚠️ لم يتم جلب بيانات لـ {symbol} {timeframe}")
                return pd.DataFrame()

            df = pd.DataFrame(
                ohlcv,
                columns=['timestamp', 'open', 'high', 'low', 'close', 'volume']
            )

            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            for col in ['open', 'high', 'low', 'close', 'volume']:
                df[col] = df[col].astype(float)

            df.set_index('timestamp', inplace=True)

            logger.debug(f"✓ جُلبت {len(df)} شمعة لـ {symbol} {timeframe}")
            return df

        except Exception as e:
            logger.error(f"❌ خطأ في جلب الشموع: {e}")
            return pd.DataFrame()

    def get_dataframe(
        self,
        symbol:    str,
        timeframe: str,
        limit:     int  = None,
        use_cache: bool = True
    ) -> pd.DataFrame:
        """
        الحصول على DataFrame مع الخيار لاستخدام cache

        Args:
            symbol:    الزوج
            timeframe: الإطار الزمني
            limit:     عدد الشموع
            use_cache: استخدام البيانات المخزنة مؤقتاً؟

        Returns:
            DataFrame
        """
        cache_key = f"{symbol}_{timeframe}"

        if use_cache and cache_key in self.cache:
            if time.time() - self.last_fetch.get(cache_key, 0) < 60:
                logger.debug(f"📦 استخدام cache: {cache_key}")
                return self.cache[cache_key]

        df = self.fetch_candles(symbol, timeframe, limit)

        if not df.empty:
            self.cache[cache_key]      = df
            self.last_fetch[cache_key] = time.time()

        return df

    # ═════════════════════════════════════════════════════
    # حساب المؤشرات الفنية
    # ═════════════════════════════════════════════════════

    @staticmethod
    def calculate_ema(data: pd.Series, period: int) -> pd.Series:
        """حساب EMA"""
        return data.ewm(span=period, adjust=False).mean()

    @staticmethod
    def calculate_rsi(data: pd.Series, period: int = 14) -> pd.Series:
        """حساب RSI"""
        delta = data.diff()
        gain  = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss  = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs    = gain / loss.replace(0, np.nan)
        rsi   = 100 - (100 / (1 + rs))
        return rsi

    @staticmethod
    def calculate_macd(
        data:   pd.Series,
        fast:   int = 12,
        slow:   int = 26,
        signal: int = 9
    ) -> Tuple[pd.Series, pd.Series, pd.Series]:
        """حساب MACD — يُعيد (MACD, Signal, Histogram)"""
        ema_fast    = data.ewm(span=fast,   adjust=False).mean()
        ema_slow    = data.ewm(span=slow,   adjust=False).mean()
        macd_line   = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=signal, adjust=False).mean()
        histogram   = macd_line - signal_line
        return macd_line, signal_line, histogram

    @staticmethod
    def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
        """حساب ATR (Average True Range)"""
        high  = df['high']
        low   = df['low']
        close = df['close']

        tr1 = high - low
        tr2 = (high - close.shift()).abs()
        tr3 = (low  - close.shift()).abs()
        tr  = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

        return tr.rolling(window=period).mean()

    @staticmethod
    def calculate_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
        """
        حساب ADX (قوة الاتجاه)

        القيم:
            < 20:  سوق راكد
            20-25: حد الاستراتيجية
            25-50: اتجاه قوي ← منطقة الدخول
            > 50:  اتجاه استثنائي
        """
        high  = df['high']
        low   = df['low']
        close = df['close']

        plus_dm  = high.diff()
        minus_dm = low.diff()

        plus_dm[plus_dm  < 0] = 0
        minus_dm[minus_dm > 0] = 0
        minus_dm = minus_dm.abs()

        tr  = MarketData.calculate_atr(df, 1)

        denom_plus  = tr.rolling(period).sum().replace(0, np.nan)
        denom_minus = tr.rolling(period).sum().replace(0, np.nan)

        plus_di  = 100 * (plus_dm.rolling(period).sum()  / denom_plus)
        minus_di = 100 * (minus_dm.rolling(period).sum() / denom_minus)

        di_sum   = (plus_di + minus_di).replace(0, np.nan)
        dx       = 100 * (plus_di - minus_di).abs() / di_sum
        adx      = dx.rolling(period).mean()

        return adx

    @staticmethod
    def calculate_bollinger_bands(
        data:    pd.Series,
        period:  int   = 20,
        std_dev: float = 2.0
    ) -> Tuple[pd.Series, pd.Series, pd.Series]:
        """حساب Bollinger Bands — يُعيد (Upper, Middle, Lower)"""
        middle = data.rolling(window=period).mean()
        std    = data.rolling(window=period).std()
        upper  = middle + (std * std_dev)
        lower  = middle - (std * std_dev)
        return upper, middle, lower

    @staticmethod
    def calculate_volume_sma(data: pd.Series, period: int = 20) -> pd.Series:
        """حساب متوسط الحجم"""
        return data.rolling(window=period).mean()

    # ═════════════════════════════════════════════════════
    # إضافة المؤشرات إلى DataFrame
    # ═════════════════════════════════════════════════════

    def add_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        إضافة جميع المؤشرات المطلوبة إلى DataFrame

        Args:
            df: DataFrame بـ OHLCV

        Returns:
            DataFrame مع جميع المؤشرات مُضافة
        """
        if df.empty:
            return df

        df = df.copy()

        try:
            ema_fast   = config['strategy']['indicators']['ema_fast']
            ema_slow   = config['strategy']['indicators']['ema_slow']
            ema_medium = config['strategy']['indicators']['ema_medium']

            # ── متوسطات الحركة ──────────────────────
            df['ema_fast']   = self.calculate_ema(df['close'], ema_fast)
            df['ema_slow']   = self.calculate_ema(df['close'], ema_slow)
            df['ema_medium'] = self.calculate_ema(df['close'], ema_medium)

            # ── مؤشرات الزخم ────────────────────────
            rsi_period  = config['strategy']['momentum']['rsi_period']
            df['rsi']   = self.calculate_rsi(df['close'], rsi_period)

            macd_fast   = config['strategy']['momentum']['macd_fast']
            macd_slow   = config['strategy']['momentum']['macd_slow']
            macd_sig    = config['strategy']['momentum']['macd_signal']

            df['macd'], df['macd_signal'], df['macd_hist'] = self.calculate_macd(
                df['close'], macd_fast, macd_slow, macd_sig
            )

            # ── مؤشرات التقلب ───────────────────────
            atr_period  = config['strategy']['volatility']['atr_period']
            df['atr']   = self.calculate_atr(df, atr_period)

            bb_period   = config['strategy']['volatility']['bb_period']
            bb_std      = config['strategy']['volatility']['bb_std_dev']
            df['bb_upper'], df['bb_middle'], df['bb_lower'] = \
                self.calculate_bollinger_bands(df['close'], bb_period, bb_std)

            # ── مؤشر قوة الاتجاه ────────────────────
            adx_period  = config['strategy']['trend']['adx_period']
            df['adx']   = self.calculate_adx(df, adx_period)

            # ── الحجم ───────────────────────────────
            vol_period       = config['strategy']['volume']['volume_ma_period']
            df['volume_sma'] = self.calculate_volume_sma(df['volume'], vol_period)

            logger.debug(
                f"✓ تمت إضافة المؤشرات — عدد الأعمدة: {len(df.columns)}"
            )
            return df

        except Exception as e:
            logger.error(f"❌ خطأ في إضافة المؤشرات: {e}")
            return df

    # ═════════════════════════════════════════════════════
    # الدوال الرئيسية للحصول على البيانات الكاملة
    # ═════════════════════════════════════════════════════

    def get_complete_dataframe(
        self,
        symbol:    str,
        timeframe: str,
        limit:     int = None
    ) -> pd.DataFrame:
        """
        الحصول على DataFrame كامل مع جميع المؤشرات

        Args:
            symbol:    الزوج
            timeframe: الإطار الزمني
            limit:     عدد الشموع

        Returns:
            DataFrame كامل جاهز للتحليل والاستراتيجية
        """
        df = self.get_dataframe(symbol, timeframe, limit)
        if df.empty:
            return df
        return self.add_indicators(df)

    def get_last_closed_candle(
        self,
        symbol:    str,
        timeframe: str
    ) -> pd.Series:
        """
        الحصول على آخر شمعة مكتملة (مغلقة)

        ملاحظة: iloc[-1] قد تكون مفتوحة لم تكتمل بعد
                iloc[-2] هي آخر شمعة مكتملة بالتأكيد
        """
        df = self.get_complete_dataframe(symbol, timeframe)
        if df.empty:
            return pd.Series()
        return df.iloc[-2]

    def get_last_n_candles(
        self,
        symbol:    str,
        timeframe: str,
        n:         int = 10
    ) -> pd.DataFrame:
        """الحصول على آخر N شمعة مكتملة"""
        df = self.get_complete_dataframe(symbol, timeframe)
        if df.empty:
            return df
        return df.iloc[-(n + 1):-1]

    def is_new_candle(self, symbol: str, timeframe: str) -> bool:
        """
        التحقق من وجود شمعة جديدة مكتملة

        مهم: نريد الإشارات فقط عند إغلاق شمعة كاملة — ليس في منتصفها
        """
        df = self.get_complete_dataframe(symbol, timeframe, limit=3)
        if len(df) < 2:
            return False

        last_time = df.index[-1]
        prev_time = df.index[-2]
        tf_minutes  = self._get_timeframe_minutes(timeframe)
        expected    = timedelta(minutes=tf_minutes)
        return (last_time - prev_time) == expected

    @staticmethod
    def _get_timeframe_minutes(timeframe: str) -> int:
        """تحويل الإطار الزمني إلى دقائق"""
        mapping = {
            '1m': 1, '5m': 5, '15m': 15, '30m': 30,
            '1h': 60, '2h': 120, '4h': 240, '6h': 360,
            '8h': 480, '12h': 720, '1d': 1440, '1w': 10080,
        }
        return mapping.get(timeframe, 15)

    def clear_cache(self, symbol: str = None, timeframe: str = None):
        """مسح cache البيانات"""
        if symbol is None or timeframe is None:
            self.cache.clear()
            self.last_fetch.clear()
            logger.info("🗑️ تم مسح كل cache البيانات")
        else:
            key = f"{symbol}_{timeframe}"
            if key in self.cache:
                del self.cache[key]
                del self.last_fetch[key]
                logger.debug(f"🗑️ تم مسح cache: {key}")

    # ═════════════════════════════════════════════════════
    # معلومات السوق الحية
    # ═════════════════════════════════════════════════════

    def get_current_price(self, symbol: str) -> float:
        """الحصول على السعر الحالي"""
        try:
            ticker = self.exchange.fetch_ticker(symbol)
            return float(ticker.get('close', 0))
        except Exception as e:
            logger.error(f"❌ خطأ في جلب السعر الحالي: {e}")
            return 0.0

    def get_24h_change(self, symbol: str) -> float:
        """التغير في آخر 24 ساعة (نسبة مئوية)"""
        try:
            ticker = self.exchange.fetch_ticker(symbol)
            return float(ticker.get('percentage', 0.0))
        except Exception:
            return 0.0

    def get_volatility(self, symbol: str, timeframe: str = '15m') -> float:
        """حساب التقلب الحالي (قيمة ATR)"""
        df = self.get_complete_dataframe(symbol, timeframe)
        if df.empty or 'atr' not in df.columns:
            return 0.0
        return float(df['atr'].iloc[-1])

    def get_volume_ratio(self, symbol: str, timeframe: str = '15m') -> float:
        """
        نسبة الحجم الحالي إلى متوسطه

        > 1.0: حجم أعلى من المتوسط (تأكيد إيجابي)
        < 1.0: حجم أقل من المتوسط
        """
        df = self.get_complete_dataframe(symbol, timeframe)
        if df.empty or 'volume_sma' not in df.columns:
            return 0.0

        current_vol = df['volume'].iloc[-1]
        avg_vol     = df['volume_sma'].iloc[-1]

        if avg_vol == 0:
            return 0.0
        return float(current_vol / avg_vol)

    # ═════════════════════════════════════════════════════
    # دوال المساعدة والطباعة
    # ═════════════════════════════════════════════════════

    def print_dataframe_info(self, df: pd.DataFrame):
        """طباعة معلومات مفيدة عن DataFrame"""
        if df.empty:
            logger.warning("⚠️ DataFrame فارغ")
            return

        logger.info(f"📊 معلومات DataFrame:")
        logger.info(f"   عدد الصفوف:    {len(df)}")
        logger.info(f"   عدد الأعمدة:   {len(df.columns)}")
        logger.info(
            f"   الفترة:       {df.index[0]} → {df.index[-1]}"
        )
        logger.info(f"   السعر الأخير:  ${df['close'].iloc[-1]:.2f}")

        for col, label in [('atr', 'ATR'), ('adx', 'ADX'), ('rsi', 'RSI')]:
            if col in df.columns:
                logger.info(f"   {label}:          {df[col].iloc[-1]:.2f}")

    def get_signal_summary(
        self,
        symbol:    str,
        timeframe: str
    ) -> Dict:
        """
        ملخص إشارات المؤشرات للزوج والإطار الزمني المحدد

        Returns:
            dict بجميع قيم المؤشرات الحالية
        """
        df = self.get_complete_dataframe(symbol, timeframe)
        if df.empty:
            return {}

        last = df.iloc[-1]

        return {
            'price':        last['close'],
            'rsi':          last.get('rsi',          None),
            'adx':          last.get('adx',          None),
            'atr':          last.get('atr',          None),
            'ema_fast':     last.get('ema_fast',     None),
            'ema_slow':     last.get('ema_slow',     None),
            'ema_medium':   last.get('ema_medium',   None),
            'macd':         last.get('macd',         None),
            'macd_signal':  last.get('macd_signal',  None),
            'macd_hist':    last.get('macd_hist',    None),
            'bb_upper':     last.get('bb_upper',     None),
            'bb_lower':     last.get('bb_lower',     None),
            'volume':       last['volume'],
            'volume_sma':   last.get('volume_sma',   None),
        }