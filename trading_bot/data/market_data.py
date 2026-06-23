"""
═══════════════════════════════════════════════════════════
جلب ومعالجة بيانات السوق - Market Data Module
═══════════════════════════════════════════════════════════
يتعامل مع جلب الشموع وتحويلها إلى DataFrame مع المؤشرات
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import yaml
import time

# استيراد المكتبات الخاصة
from core.exchange import BinanceExchange
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
    
    المسؤولياتها:
    1. جلب الشموع من بينانس
    2. تحويلها إلى DataFrame
    3. إضافة المؤشرات الفنية
    4. تخزين البيانات مؤقتاً (caching)
    5. تحديث البيانات تلقائياً
    """
    
    def __init__(self, exchange: BinanceExchange):
        """
        تهيئة فئة معالجة البيانات
        
        Args:
            exchange: instance من BinanceExchange
        """
        self.exchange = exchange
        self.cache = {}  # cache الشموع
        self.last_fetch = {}  # آخر وقت جلب لكل زوج/timeframe
        self.lookback_period = config['trading']['lookback_period']
        
        logger.success("✅ فئة MarketData جاهزة")
    
    # ═════════════════════════════════════════════════════
    # جلب البيانات الخام (Raw Data Fetching)
    # ═════════════════════════════════════════════════════
    
    def fetch_candles(
        self,
        symbol: str,
        timeframe: str,
        limit: int = None
    ) -> pd.DataFrame:
        """
        جلب الشموع وتحويلها إلى DataFrame
        
        Args:
            symbol: الزوج (مثل 'ETH/USDT')
            timeframe: الإطار الزمني (مثل '15m', '1h')
            limit: عدد الشموع (افتراضي: من config)
        
        Returns:
            DataFrame بالشموع
        """
        if limit is None:
            limit = self.lookback_period
        
        try:
            # جلب البيانات الخام من بينانس
            ohlcv = self.exchange.fetch_ohlcv(symbol, timeframe, limit)
            
            if not ohlcv:
                logger.warning(f"⚠️ لم يتم جلب بيانات لـ {symbol} {timeframe}")
                return pd.DataFrame()
            
            # تحويل إلى DataFrame
            df = pd.DataFrame(
                ohlcv,
                columns=['timestamp', 'open', 'high', 'low', 'close', 'volume']
            )
            
            # تحويل timestamp إلى datetime
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            
            # تحويل الأسعار والحجم إلى float
            df['open'] = df['open'].astype(float)
            df['high'] = df['high'].astype(float)
            df['low'] = df['low'].astype(float)
            df['close'] = df['close'].astype(float)
            df['volume'] = df['volume'].astype(float)
            
            # جعل timestamp هو index
            df.set_index('timestamp', inplace=True)
            
            logger.debug(
                f"✓ جُلبت {len(df)} شمعة لـ {symbol} {timeframe}"
            )
            
            return df
        
        except Exception as e:
            logger.error(f"❌ خطأ في جلب الشموع: {e}")
            return pd.DataFrame()
    
    def get_dataframe(
        self,
        symbol: str,
        timeframe: str,
        limit: int = None,
        use_cache: bool = True
    ) -> pd.DataFrame:
        """
        الحصول على DataFrame مع الخيار لاستخدام cache
        
        Args:
            symbol: الزوج
            timeframe: الإطار الزمني
            limit: عدد الشموع
            use_cache: استخدام البيانات المخزنة مؤقتاً؟
        
        Returns:
            DataFrame
        """
        cache_key = f"{symbol}_{timeframe}"
        
        # إذا كان في cache وليس قديماً جداً
        if use_cache and cache_key in self.cache:
            # تحقق من أن cache آخر من 1 دقيقة
            if time.time() - self.last_fetch.get(cache_key, 0) < 60:
                logger.debug(f"📦 استخدام cache: {cache_key}")
                return self.cache[cache_key]
        
        # جلب بيانات جديدة
        df = self.fetch_candles(symbol, timeframe, limit)
        
        # حفظ في cache
        if not df.empty:
            self.cache[cache_key] = df
            self.last_fetch[cache_key] = time.time()
        
        return df
    
    # ═════════════════════════════════════════════════════
    # حساب المؤشرات الفنية (Technical Indicators)
    # ═════════════════════════════════════════════════════
    
    @staticmethod
    def calculate_ema(data: pd.Series, period: int) -> pd.Series:
        """
        حساب متوسط الحركة الأسي (EMA)
        
        Args:
            data: سلسلة البيانات (عادة closing prices)
            period: الفترة
        
        Returns:
            السلسلة بـ EMA
        """
        return data.ewm(span=period, adjust=False).mean()
    
    @staticmethod
    def calculate_rsi(data: pd.Series, period: int = 14) -> pd.Series:
        """
        حساب مؤشر القوة النسبية (RSI)
        
        Args:
            data: سلسلة البيانات (closing prices)
            period: الفترة (عادة 14)
        
        Returns:
            سلسلة RSI (0-100)
        """
        # حساب التغييرات
        delta = data.diff()
        
        # الأرباح والخسائر
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        
        # تجنب القسمة على صفر
        rs = gain / loss.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs))
        
        return rsi
    
    @staticmethod
    def calculate_macd(
        data: pd.Series,
        fast: int = 12,
        slow: int = 26,
        signal: int = 9
    ) -> Tuple[pd.Series, pd.Series, pd.Series]:
        """
        حساب مؤشر MACD
        
        Args:
            data: سلسلة البيانات
            fast: الفترة السريعة
            slow: الفترة البطيئة
            signal: فترة خط الإشارة
        
        Returns:
            (MACD line, Signal line, Histogram)
        """
        ema_fast = data.ewm(span=fast, adjust=False).mean()
        ema_slow = data.ewm(span=slow, adjust=False).mean()
        
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=signal, adjust=False).mean()
        histogram = macd_line - signal_line
        
        return macd_line, signal_line, histogram
    
    @staticmethod
    def calculate_atr(
        df: pd.DataFrame,
        period: int = 14
    ) -> pd.Series:
        """
        حساب مؤشر المتوسط الحقيقي للنطاق (ATR)
        
        هذا المؤشر يقيس تقلب السوق
        
        Args:
            df: DataFrame بـ high, low, close
            period: الفترة
        
        Returns:
            سلسلة ATR
        """
        high = df['high']
        low = df['low']
        close = df['close']
        
        # حساب TR (True Range)
        tr1 = high - low
        tr2 = abs(high - close.shift())
        tr3 = abs(low - close.shift())
        
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        
        # ATR هو متوسط TR
        atr = tr.rolling(window=period).mean()
        
        return atr
    
    @staticmethod
    def calculate_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
        """
        حساب مؤشر الاتجاه المتوسط (ADX)
        
        يقيس قوة الاتجاه (0-100)
        - أقل من 25: اتجاه ضعيف
        - 25-50: اتجاه قوي
        - أكثر من 50: اتجاه جداً قوي
        
        Args:
            df: DataFrame
            period: الفترة
        
        Returns:
            سلسلة ADX
        """
        high = df['high']
        low = df['low']
        close = df['close']
        
        # حساب DM (Directional Movement)
        plus_dm = high.diff()
        minus_dm = low.diff()
        
        plus_dm[plus_dm < 0] = 0
        minus_dm[minus_dm > 0] = 0
        minus_dm = abs(minus_dm)
        
        # حساب TR
        tr = MarketData.calculate_atr(df, 1)
        
        # حساب DI
        plus_di = 100 * (plus_dm.rolling(period).sum() / tr.rolling(period).sum())
        minus_di = 100 * (minus_dm.rolling(period).sum() / tr.rolling(period).sum())
        
        # حساب ADX
        dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di)
        adx = dx.rolling(period).mean()
        
        return adx
    
    @staticmethod
    def calculate_bollinger_bands(
        data: pd.Series,
        period: int = 20,
        std_dev: float = 2.0
    ) -> Tuple[pd.Series, pd.Series, pd.Series]:
        """
        حساب فرقات بولينجر
        
        Args:
            data: سلسلة البيانات
            period: الفترة
            std_dev: عدد الانحرافات المعيارية
        
        Returns:
            (Upper Band, Middle Band, Lower Band)
        """
        middle = data.rolling(window=period).mean()
        std = data.rolling(window=period).std()
        
        upper = middle + (std * std_dev)
        lower = middle - (std * std_dev)
        
        return upper, middle, lower
    
    @staticmethod
    def calculate_volume_sma(data: pd.Series, period: int = 20) -> pd.Series:
        """
        حساب متوسط الحجم (Volume Simple Moving Average)
        
        Args:
            data: سلسلة الحجم
            period: الفترة
        
        Returns:
            متوسط الحجم
        """
        return data.rolling(window=period).mean()
    
    # ═════════════════════════════════════════════════════
    # إضافة المؤشرات إلى DataFrame
    # ═════════════════════════════════════════════════════
    
    def add_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        إضافة جميع المؤشرات المطلوبة إلى DataFrame
        
        هذه الدالة تأخذ DataFrame بسيط وتضيف جميع المؤشرات
        
        Args:
            df: DataFrame بـ OHLCV
        
        Returns:
            DataFrame مع جميع المؤشرات
        """
        if df.empty:
            return df
        
        # نسخ DataFrame لتجنب التعديل على الأصلي
        df = df.copy()
        
        try:
            # ─────────────────────────────────────────
            # متوسطات الحركة (Moving Averages)
            # ─────────────────────────────────────────
            
            ema_fast = config['strategy']['indicators']['ema_fast']
            ema_slow = config['strategy']['indicators']['ema_slow']
            ema_medium = config['strategy']['indicators']['ema_medium']
            
            df['ema_fast'] = self.calculate_ema(df['close'], ema_fast)
            df['ema_slow'] = self.calculate_ema(df['close'], ema_slow)
            df['ema_medium'] = self.calculate_ema(df['close'], ema_medium)
            
            # ─────────────────────────────────────────
            # مؤشرات الزخم (Momentum)
            # ─────────────────────────────────────────
            
            rsi_period = config['strategy']['momentum']['rsi_period']
            df['rsi'] = self.calculate_rsi(df['close'], rsi_period)
            
            macd_fast = config['strategy']['momentum']['macd_fast']
            macd_slow = config['strategy']['momentum']['macd_slow']
            macd_signal = config['strategy']['momentum']['macd_signal']
            
            df['macd'], df['macd_signal'], df['macd_hist'] = self.calculate_macd(
                df['close'],
                macd_fast,
                macd_slow,
                macd_signal
            )
            
            # ─────────────────────────────────────────
            # مؤشر التقلب (Volatility)
            # ─────────────────────────────────────────
            
            atr_period = config['strategy']['volatility']['atr_period']
            df['atr'] = self.calculate_atr(df, atr_period)
            
            bb_period = config['strategy']['volatility']['bb_period']
            bb_std = config['strategy']['volatility']['bb_std_dev']
            
            df['bb_upper'], df['bb_middle'], df['bb_lower'] = self.calculate_bollinger_bands(
                df['close'],
                bb_period,
                bb_std
            )
            
            # ─────────────────────────────────────────
            # مؤشر الاتجاه (Trend Strength)
            # ─────────────────────────────────────────
            
            adx_period = config['strategy']['trend']['adx_period']
            df['adx'] = self.calculate_adx(df, adx_period)
            
            # ─────────────────────────────────────────
            # الحجم (Volume)
            # ─────────────────────────────────────────
            
            vol_period = config['strategy']['volume']['volume_ma_period']
            df['volume_sma'] = self.calculate_volume_sma(df['volume'], vol_period)
            
            logger.debug(f"✓ تمت إضافة المؤشرات - عدد الأعمدة: {len(df.columns)}")
            
            return df
        
        except Exception as e:
            logger.error(f"❌ خطأ في إضافة المؤشرات: {e}")
            return df
    
    # ═════════════════════════════════════════════════════
    # دوال تحليل البيانات (Data Analysis)
    # ═════════════════════════════════════════════════════
    
    def get_complete_dataframe(
        self,
        symbol: str,
        timeframe: str,
        limit: int = None
    ) -> pd.DataFrame:
        """
        الحصول على DataFrame كامل مع جميع المؤشرات
        
        Args:
            symbol: الزوج
            timeframe: الإطار الزمني
            limit: عدد الشموع
        
        Returns:
            DataFrame كامل جاهز للتحليل
        """
        # جلب البيانات الخام
        df = self.get_dataframe(symbol, timeframe, limit)
        
        if df.empty:
            return df
        
        # إضافة المؤشرات
        df = self.add_indicators(df)
        
        return df
    
    def get_last_closed_candle(
        self,
        symbol: str,
        timeframe: str
    ) -> pd.Series:
        """
        الحصول على آخر شمعة مكتملة (مهم: ليس الشمعة الحالية المفتوحة)
        
        Args:
            symbol: الزوج
            timeframe: الإطار الزمني
        
        Returns:
            آخر صف مكتمل من DataFrame
        """
        df = self.get_complete_dataframe(symbol, timeframe)
        
        if df.empty:
            return pd.Series()
        
        # آخر شمعة مكتملة (iloc[-2])
        # الأخيرة (iloc[-1]) قد تكون مفتوحة ولم تكتمل بعد
        return df.iloc[-2]
    
    def get_last_n_candles(
        self,
        symbol: str,
        timeframe: str,
        n: int = 10
    ) -> pd.DataFrame:
        """
        الحصول على آخر N شمعة مكتملة
        
        Args:
            symbol: الزوج
            timeframe: الإطار الزمني
            n: عدد الشموع
        
        Returns:
            DataFrame بـ N شمعة
        """
        df = self.get_complete_dataframe(symbol, timeframe)
        
        if df.empty:
            return df
        
        # نأخذ آخر N+1 ثم نزيل الأخيرة (الشمعة المفتوحة)
        return df.iloc[-(n+1):-1]
    
    def is_new_candle(self, symbol: str, timeframe: str) -> bool:
        """
        التحقق من أن هناك شمعة جديدة مكتملة
        
        هذا مهم جداً لأننا نريد الإشارات فقط عند إغلاق شمعة كاملة
        
        Args:
            symbol: الزوج
            timeframe: الإطار الزمني
        
        Returns:
            True إذا كانت هناك شمعة جديدة مكتملة
        """
        df = self.get_complete_dataframe(symbol, timeframe, limit=3)
        
        if len(df) < 2:
            return False
        
        # قارن timestamp آخر شمعة مع قبلها
        # إذا اختلفت = هناك شمعة جديدة
        last_time = df.index[-1]
        prev_time = df.index[-2]
        
        # احسب الفرق بناءً على الإطار الزمني
        timeframe_minutes = self._get_timeframe_minutes(timeframe)
        expected_diff = timedelta(minutes=timeframe_minutes)
        
        return (last_time - prev_time) == expected_diff
    
    @staticmethod
    def _get_timeframe_minutes(timeframe: str) -> int:
        """
        تحويل الإطار الزمني إلى دقائق
        
        Args:
            timeframe: الإطار الزمني (مثل '15m', '1h', '4h')
        
        Returns:
            عدد الدقائق
        """
        mapping = {
            '1m': 1,
            '5m': 5,
            '15m': 15,
            '30m': 30,
            '1h': 60,
            '2h': 120,
            '4h': 240,
            '6h': 360,
            '8h': 480,
            '12h': 720,
            '1d': 1440,
            '1w': 10080,
        }
        return mapping.get(timeframe, 15)  # افتراضي: 15m
    
    def clear_cache(self, symbol: str = None, timeframe: str = None):
        """
        مسح cache البيانات
        
        Args:
            symbol: الزوج (إذا None = امسح الكل)
            timeframe: الإطار الزمني (إذا None = امسح الكل)
        """
        if symbol is None or timeframe is None:
            # امسح كل شيء
            self.cache.clear()
            self.last_fetch.clear()
            logger.info("🗑️ تم مسح كل cache البيانات")
        else:
            # امسح محدد
            key = f"{symbol}_{timeframe}"
            if key in self.cache:
                del self.cache[key]
                del self.last_fetch[key]
                logger.debug(f"🗑️ تم مسح cache: {key}")
    
    # ═════════════════════════════════════════════════════
    # دوال معلومات السوق (Market Info)
    # ═════════════════════════════════════════════════════
    
    def get_current_price(self, symbol: str) -> float:
        """
        الحصول على السعر الحالي للزوج
        
        Args:
            symbol: الزوج
        
        Returns:
            السعر الحالي (float)
        """
        try:
            ticker = self.exchange.fetch_ticker(symbol)
            return ticker['close']
        except Exception as e:
            logger.error(f"❌ خطأ في جلب السعر الحالي: {e}")
            return 0.0
    
    def get_24h_change(self, symbol: str) -> float:
        """
        الحصول على التغير في آخر 24 ساعة (نسبة مئوية)
        
        Args:
            symbol: الزوج
        
        Returns:
            النسبة المئوية للتغير
        """
        try:
            ticker = self.exchange.fetch_ticker(symbol)
            return ticker.get('percentage', 0.0)
        except:
            return 0.0
    
    def get_volatility(self, symbol: str, timeframe: str = '15m') -> float:
        """
        حساب التقلب الحالي
        
        Args:
            symbol: الزوج
            timeframe: الإطار الزمني
        
        Returns:
            قيمة ATR (التقلب)
        """
        df = self.get_complete_dataframe(symbol, timeframe)
        
        if df.empty or 'atr' not in df.columns:
            return 0.0
        
        return df['atr'].iloc[-1]
    
    def get_volume_ratio(self, symbol: str, timeframe: str = '15m') -> float:
        """
        حساب نسبة الحجم الحالي إلى متوسطه
        
        Args:
            symbol: الزوج
            timeframe: الإطار الزمني
        
        Returns:
            النسبة (1.0 = متوسط، > 1.0 = أعلى من المتوسط)
        """
        df = self.get_complete_dataframe(symbol, timeframe)
        
        if df.empty or 'volume_sma' not in df.columns:
            return 0.0
        
        current_vol = df['volume'].iloc[-1]
        avg_vol = df['volume_sma'].iloc[-1]
        
        if avg_vol == 0:
            return 0.0
        
        return current_vol / avg_vol
    
    # ═════════════════════════════════════════════════════
    # دوال مساعدة (Helper Functions)
    # ═════════════════════════════════════════════════════
    
    def print_dataframe_info(self, df: pd.DataFrame):
        """
        طباعة معلومات مفيدة عن DataFrame
        
        Args:
            df: DataFrame
        """
        if df.empty:
            logger.warning("⚠️ DataFrame فارغ")
            return
        
        logger.info(f"📊 معلومات DataFrame:")
        logger.info(f"   عدد الصفوف: {len(df)}")
        logger.info(f"   عدد الأعمدة: {len(df.columns)}")
        logger.info(f"   الفترة الزمنية: {df.index[0]} إلى {df.index[-1]}")
        logger.info(f"   السعر الأخير: ${df['close'].iloc[-1]:.2f}")
        
        if 'atr' in df.columns:
            logger.info(f"   ATR: {df['atr'].iloc[-1]:.4f}")
        
        if 'adx' in df.columns:
            logger.info(f"   ADX: {df['adx'].iloc[-1]:.2f}")
        
        if 'rsi' in df.columns:
            logger.info(f"   RSI: {df['rsi'].iloc[-1]:.2f}")
    
    def get_signal_summary(
        self,
        symbol: str,
        timeframe: str
    ) -> Dict:
        """
        الحصول على ملخص إشارات المؤشرات
        
        Args:
            symbol: الزوج
            timeframe: الإطار الزمني
        
        Returns:
            قاموس بملخص الإشارات
        """
        df = self.get_complete_dataframe(symbol, timeframe)
        
        if df.empty:
            return {}
        
        last = df.iloc[-1]
        
        summary = {
            'price': last['close'],
            'rsi': last.get('rsi', None),
            'adx': last.get('adx', None),
            'atr': last.get('atr', None),
            'ema_fast': last.get('ema_fast', None),
            'ema_slow': last.get('ema_slow', None),
            'ema_medium': last.get('ema_medium', None),
            'macd': last.get('macd', None),
            'macd_signal': last.get('macd_signal', None),
            'macd_hist': last.get('macd_hist', None),
            'bb_upper': last.get('bb_upper', None),
            'bb_lower': last.get('bb_lower', None),
            'volume': last['volume'],
            'volume_sma': last.get('volume_sma', None),
        }
        
        return summary

# ─────────────────────────────────────────────────────────
# مثال على الاستخدام
# ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    # إنشاء instances
    exchange = BinanceExchange(use_testnet=True)
    market_data = MarketData(exchange)
    
    # جلب بيانات كاملة
    print("\n🔍 جلب بيانات ETH/USDT على إطار 15m...\n")
    df = market_data.get_complete_dataframe('ETH/USDT', '15m', limit=50)
    
    # طباعة المعلومات
    market_data.print_dataframe_info(df)
    
    # الحصول على آخر شمعة مكتملة
    print("\n📊 آخر شمعة مكتملة:\n")
    last_candle = market_data.get_last_closed_candle('ETH/USDT', '15m')
    print(f"السعر الإغلاق: ${last_candle['close']:.2f}")
    print(f"RSI: {last_candle.get('rsi', 'N/A'):.2f}")
    print(f"ADX: {last_candle.get('adx', 'N/A'):.2f}")
    print(f"ATR: {last_candle.get('atr', 'N/A'):.4f}")
    
    # الحصول على ملخص الإشارات
    print("\n🎯 ملخص الإشارات:\n")
    summary = market_data.get_signal_summary('ETH/USDT', '15m')
    for key, value in summary.items():
        if value is not None:
            print(f"{key}: {value:.2f}" if isinstance(value, float) else f"{key}: {value}")
