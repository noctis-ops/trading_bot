"""
═══════════════════════════════════════════════════════════
محرك Backtesting المتقدم — النسخة المُصلَحة v1.1
═══════════════════════════════════════════════════════════
نفس الاستراتيجية بالضبط من core/strategy.py

═══════════════════════════════════════════════════════════
سجل الإصلاحات (v1.1):
═══════════════════════════════════════════════════════════
🐛 Bug 1 — IndexError بالبيانات الحقيقية:
   السبب:  i*4 و i*12 يتجاوزان طول DataFrame (200 صف)
   الإصلاح: التقطيع بالـ timestamp بدلاً من الأرقام الصحيحة

🐛 Bug 2 — مفتاح 'symbol' حرفي:
   السبب:  self.positions['symbol'] تبحث عن المفتاح الحرفي "symbol"
           وليس عن اسم الزوج الفعلي ('BTC/USDT')
   الإصلاح: تمرير symbol كمعامل لكل الدوال

🐛 Bug 3 — timestamp من عمود بدلاً من الـ index:
   السبب:  current_1h['timestamp'] تفشل عندما timestamp هو الـ index
   الإصلاح: استخدام df_1h.index[i] مباشرة

🐛 Bug 4 — generate_sample_data لا تضع timestamp كـ index:
   الإصلاح: إضافة df.set_index('timestamp') لجميع DataFrames
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Tuple, Optional
import json

from utils.logger import logger
from core.strategy import TradingStrategy
from data.market_data import MarketData


class AdvancedBacktestingEngine:
    """
    محرك Backtesting دقيق يستخدم نفس الاستراتيجية بالضبط

    الميزات:
    - يستخدم TradingStrategy.check_buy_signal() بدون تعديل
    - يدعم البيانات الحقيقية (من API) والبيانات التجريبية
    - تقطيع صحيح بالـ timestamp لجميع الأطر الزمنية
    - يتتبع الرصيد والصفقات والأداء
    """

    # ── الحد الأدنى للشموع اللازمة للمؤشرات ──
    MIN_CANDLES = 25

    def __init__(self, initial_balance: float = 10_000.0):
        """
        تهيئة محرك Backtesting

        Args:
            initial_balance: رأس المال الأولي للمحاكاة
        """
        self.initial_balance  = initial_balance
        self.current_balance  = initial_balance
        self.strategy         = TradingStrategy()

        # ── سجلات الصفقات والأداء ──────────────
        self.trades:      List[Dict] = []
        self.signals:     List[Dict] = []
        self.positions:   Dict[str, Dict] = {}   # {symbol → position}
        self.equity_curve: List[float] = [initial_balance]
        self.timestamps:   List = []

        # ── إحصائيات ────────────────────────────
        self.total_trades   = 0
        self.winning_trades = 0
        self.losing_trades  = 0

        logger.success(
            f"✅ محرك Backtesting متقدم جاهز | "
            f"الرصيد: ${initial_balance:,.2f}"
        )

    # ═══════════════════════════════════════════════════
    # تحميل البيانات الحقيقية من API
    # ═══════════════════════════════════════════════════

    def load_and_prepare_data(
        self,
        exchange,
        symbol: str = 'BTC/USDT'
    ) -> Tuple[Optional[pd.DataFrame], Optional[pd.DataFrame], Optional[pd.DataFrame]]:
        """
        تحميل البيانات من API بينانس وتحضيرها

        يستخدم نفس طريقة البوت الأساسي بالضبط.
        يُعيد DataFrames مُفهرَسة بالـ timestamp.
        """
        try:
            logger.info(f"📊 تحميل البيانات لـ {symbol}...")

            market_data = MarketData(exchange)

            # جلب البيانات على الأطر الزمنية الثلاثة
            # limit=500 لضمان وجود بيانات كافية عبر جميع الأطر
            df_1h  = market_data.get_complete_dataframe(symbol, '1h',  limit=300)
            df_15m = market_data.get_complete_dataframe(symbol, '15m', limit=500)
            df_5m  = market_data.get_complete_dataframe(symbol, '5m',  limit=500)

            if df_1h.empty or df_15m.empty or df_5m.empty:
                logger.error("❌ فشل تحميل البيانات — DataFrame فارغ")
                return None, None, None

            # التأكد من أن timestamp هو الـ index
            df_1h, df_15m, df_5m = self._ensure_timestamp_index(
                df_1h, df_15m, df_5m
            )

            logger.success(
                f"✅ تم تحميل البيانات:\n"
                f"   • 1H:  {len(df_1h)} شمعة "
                f"({df_1h.index[0].date()} → {df_1h.index[-1].date()})\n"
                f"   • 15M: {len(df_15m)} شمعة\n"
                f"   • 5M:  {len(df_5m)} شمعة"
            )

            return df_1h, df_15m, df_5m

        except Exception as e:
            logger.error(f"❌ خطأ في تحميل البيانات: {e}")
            return None, None, None

    # ═══════════════════════════════════════════════════
    # توليد بيانات تجريبية
    # ═══════════════════════════════════════════════════

    def generate_sample_data(
        self,
        symbol: str = 'BTC/USDT',
        days:   int = 60
    ) -> Tuple[Optional[pd.DataFrame], Optional[pd.DataFrame], Optional[pd.DataFrame]]:
        """
        توليد بيانات تجريبية واقعية للاختبار دون API

        الإصلاح: تضبط timestamp كـ index في جميع DataFrames
        """
        try:
            logger.warning(f"⚠️ توليد بيانات تجريبية ({days} يوم)...")

            base_price = 43_000.0
            end_time   = datetime.utcnow()

            # ── توليد بيانات 1H ──────────────────────
            n_1h  = days * 24
            dates_1h = pd.date_range(end=end_time, periods=n_1h, freq='1H')

            # Random Walk مع اتجاه صاعد خفيف
            returns_1h = np.random.normal(0.0003, 0.018, n_1h)
            prices_1h  = base_price * np.exp(np.cumsum(returns_1h))
            vol_1h     = np.random.uniform(0.005, 0.015, n_1h)

            df_1h = pd.DataFrame({
                'timestamp': dates_1h,
                'open':   prices_1h * (1 - vol_1h / 2),
                'high':   prices_1h * (1 + vol_1h),
                'low':    prices_1h * (1 - vol_1h),
                'close':  prices_1h,
                'volume': np.random.randint(1_000, 8_000, n_1h).astype(float),
            })

            # ── توليد بيانات 15M ─────────────────────
            n_15m    = days * 24 * 4
            dates_15m = pd.date_range(end=end_time, periods=n_15m, freq='15min')

            returns_15m = np.random.normal(0.00008, 0.009, n_15m)
            prices_15m  = base_price * np.exp(np.cumsum(returns_15m))
            vol_15m     = np.random.uniform(0.003, 0.010, n_15m)

            df_15m = pd.DataFrame({
                'timestamp': dates_15m,
                'open':   prices_15m * (1 - vol_15m / 2),
                'high':   prices_15m * (1 + vol_15m),
                'low':    prices_15m * (1 - vol_15m),
                'close':  prices_15m,
                'volume': np.random.randint(100, 1_000, n_15m).astype(float),
            })

            # ── توليد بيانات 5M ──────────────────────
            n_5m     = days * 24 * 12
            dates_5m = pd.date_range(end=end_time, periods=n_5m, freq='5min')

            returns_5m = np.random.normal(0.00002, 0.005, n_5m)
            prices_5m  = base_price * np.exp(np.cumsum(returns_5m))
            vol_5m     = np.random.uniform(0.001, 0.006, n_5m)

            df_5m = pd.DataFrame({
                'timestamp': dates_5m,
                'open':   prices_5m * (1 - vol_5m / 2),
                'high':   prices_5m * (1 + vol_5m),
                'low':    prices_5m * (1 - vol_5m),
                'close':  prices_5m,
                'volume': np.random.randint(10, 200, n_5m).astype(float),
            })

            # ── إضافة المؤشرات ────────────────────────
            for df in [df_1h, df_15m, df_5m]:
                self._add_indicators(df)

            # ── ✅ الإصلاح: ضبط timestamp كـ index ──────
            df_1h, df_15m, df_5m = self._ensure_timestamp_index(
                df_1h, df_15m, df_5m
            )

            logger.success(
                f"✅ تم توليد البيانات:\n"
                f"   • 1H:  {len(df_1h)} شمعة\n"
                f"   • 15M: {len(df_15m)} شمعة\n"
                f"   • 5M:  {len(df_5m)} شمعة"
            )

            return df_1h, df_15m, df_5m

        except Exception as e:
            logger.error(f"❌ خطأ في توليد البيانات: {e}")
            return None, None, None

    # ═══════════════════════════════════════════════════
    # دوال المؤشرات
    # ═══════════════════════════════════════════════════

    def _add_indicators(self, df: pd.DataFrame):
        """
        إضافة نفس المؤشرات التي يستخدمها البوت الأساسي

        EMA(200, 50, 21) — RSI(14) — MACD(12,26,9)
        ATR(14) — ADX(14) — Volume SMA(20)
        """
        try:
            close = df['close']
            high  = df['high']
            low   = df['low']

            # ── EMA ─────────────────────────────────
            df['ema_slow']   = close.ewm(span=200, adjust=False).mean()  # EMA200
            df['ema_fast']   = close.ewm(span=50,  adjust=False).mean()  # EMA50
            df['ema_medium'] = close.ewm(span=21,  adjust=False).mean()  # EMA21

            # ── RSI(14) ──────────────────────────────
            delta = close.diff()
            gain  = delta.where(delta > 0, 0.0).rolling(14).mean()
            loss  = (-delta.where(delta < 0, 0.0)).rolling(14).mean()
            rs    = gain / loss.replace(0, np.nan)
            df['rsi'] = 100 - (100 / (1 + rs))

            # ── MACD(12,26,9) ────────────────────────
            ema12 = close.ewm(span=12, adjust=False).mean()
            ema26 = close.ewm(span=26, adjust=False).mean()
            df['macd']        = ema12 - ema26
            df['macd_signal'] = df['macd'].ewm(span=9, adjust=False).mean()
            df['macd_hist']   = df['macd'] - df['macd_signal']

            # ── ATR(14) ──────────────────────────────
            hl  = high - low
            hc  = (high - close.shift()).abs()
            lc  = (low  - close.shift()).abs()
            tr  = pd.concat([hl, hc, lc], axis=1).max(axis=1)
            df['atr'] = tr.rolling(14).mean()

            # ── ADX(14) ──────────────────────────────
            df['adx'] = self._calculate_adx(df)

            # ── Volume SMA(20) ───────────────────────
            df['volume_sma'] = df['volume'].rolling(20).mean()

        except Exception as e:
            logger.error(f"❌ خطأ في إضافة المؤشرات: {e}")

    def _calculate_adx(self, df: pd.DataFrame, period: int = 14) -> pd.Series:
        """حساب ADX بدقة"""
        high  = df['high']
        low   = df['low']

        plus_dm  = high.diff().clip(lower=0)
        minus_dm = (-low.diff()).clip(lower=0)

        # الأولوية للحركة الأكبر
        plus_dm  = plus_dm.where(plus_dm  > minus_dm, 0)
        minus_dm = minus_dm.where(minus_dm > plus_dm,  0)

        # ATR
        hl  = high - low
        hc  = (high - df['close'].shift()).abs()
        lc  = (low  - df['close'].shift()).abs()
        tr  = pd.concat([hl, hc, lc], axis=1).max(axis=1)
        atr = tr.rolling(period).mean()

        # DI
        plus_di  = 100 * (plus_dm.rolling(period).mean()  / atr.replace(0, np.nan))
        minus_di = 100 * (minus_dm.rolling(period).mean() / atr.replace(0, np.nan))

        # ADX
        dx  = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
        adx = dx.rolling(period).mean()

        return adx.fillna(0)

    # ═══════════════════════════════════════════════════
    # دوال مساعدة للـ DataFrames
    # ═══════════════════════════════════════════════════

    @staticmethod
    def _ensure_timestamp_index(
        df_1h:  pd.DataFrame,
        df_15m: pd.DataFrame,
        df_5m:  pd.DataFrame
    ) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        """
        التأكد من أن timestamp هو الـ index في جميع DataFrames.
        يُعالج كلاً من:
          - DataFrames مع 'timestamp' كعمود
          - DataFrames مع timestamp كـ index بالفعل
        """
        result = []
        for df in [df_1h, df_15m, df_5m]:
            df = df.copy()
            # إذا كان timestamp عمود → اجعله index
            if 'timestamp' in df.columns:
                df = df.set_index('timestamp')
            # تأكد أن الـ index من نوع DatetimeIndex
            if not isinstance(df.index, pd.DatetimeIndex):
                df.index = pd.to_datetime(df.index)
            df = df.sort_index()
            result.append(df)

        return result[0], result[1], result[2]

    @staticmethod
    def _get_slice_up_to(
        df:        pd.DataFrame,
        timestamp: pd.Timestamp,
        n_candles: int = 50
    ) -> pd.DataFrame:
        """
        ✅ الإصلاح الأساسي لـ Bug 1 (IndexError)

        بدلاً من df.iloc[i*4] التي تتجاوز حدود DataFrame،
        نستخدم التقطيع بالـ timestamp:
            - نأخذ جميع الشموع حتى التوقيت المحدد
            - نأخذ آخر n_candles شمعة منها

        Args:
            df:        DataFrame مُفهرَس بالـ timestamp
            timestamp: الوقت الحالي في حلقة الـ backtesting
            n_candles: عدد الشموع التي نريدها

        Returns:
            DataFrame بآخر n_candles شمعة حتى timestamp
        """
        slice_df = df[df.index <= timestamp]
        if len(slice_df) == 0:
            return pd.DataFrame()
        return slice_df.iloc[-n_candles:]

    # ═══════════════════════════════════════════════════
    # حلقة Backtesting الرئيسية
    # ═══════════════════════════════════════════════════

    def backtest(
        self,
        df_1h:  pd.DataFrame,
        df_15m: pd.DataFrame,
        df_5m:  pd.DataFrame,
        symbol: str = 'BTC/USDT'
    ) -> Dict:
        """
        محاكاة التداول على البيانات التاريخية

        الخوارزمية:
            لكل شمعة 1H في البيانات:
            1. اجمع بيانات 15M و 5M حتى هذا الوقت
            2. تحقق من إشارة الشراء باستخدام نفس TradingStrategy
            3. افتح صفقة إذا وجدت إشارة ولا توجد صفقة مفتوحة
            4. تحقق من SL/TP للصفقة المفتوحة
            5. سجّل منحنى الأسهم

        Args:
            df_1h, df_15m, df_5m: DataFrames مُفهرَسة بالـ timestamp
            symbol: اسم الزوج للتداول

        Returns:
            dict تقرير الأداء الشامل
        """
        try:
            logger.info("🧪 بدء Backtesting...")

            # ── التأكد من الـ index ──────────────────
            df_1h, df_15m, df_5m = self._ensure_timestamp_index(
                df_1h, df_15m, df_5m
            )

            n = len(df_1h)
            if n < self.MIN_CANDLES:
                logger.error(
                    f"❌ بيانات 1H غير كافية: {n} < {self.MIN_CANDLES}"
                )
                return {'status': 'insufficient_data'}

            # ── الحلقة الرئيسية ──────────────────────
            # نبدأ من MIN_CANDLES لضمان وجود بيانات كافية للمؤشرات
            # ونتوقف قبل الأخير لأن iloc[-1] شمعة مفتوحة
            for i in range(self.MIN_CANDLES, n - 1):

                # ── ✅ الإصلاح Bug 3: timestamp من الـ index مباشرة
                ts = df_1h.index[i]

                # ── ✅ الإصلاح Bug 1: تقطيع بالـ timestamp بدلاً من i*4
                df_1h_slice  = self._get_slice_up_to(df_1h,  ts, n_candles=50)
                df_15m_slice = self._get_slice_up_to(df_15m, ts, n_candles=80)
                df_5m_slice  = self._get_slice_up_to(df_5m,  ts, n_candles=80)

                # تجاهل إذا لم تكن البيانات كافية
                if (len(df_15m_slice) < self.MIN_CANDLES or
                        len(df_5m_slice)  < self.MIN_CANDLES):
                    continue

                # ── التحقق من إشارة الشراء ─────────────
                # ✅ الإصلاح Bug 2: استخدام symbol المتغير بدلاً من 'symbol' الحرفي
                if symbol not in self.positions:
                    signal_found, signal_data = self.strategy.check_buy_signal(
                        df_1h_slice,
                        df_15m_slice,
                        df_5m_slice
                    )

                    if signal_found:
                        self._open_position(
                            symbol=symbol,
                            entry_price=signal_data['entry_price'],
                            atr=signal_data.get('atr', 100),
                            timestamp=ts,
                        )
                        self.signals.append(signal_data)

                # ── التحقق من شروط الخروج ──────────────
                if symbol in self.positions:
                    pos           = self.positions[symbol]
                    current_price = df_1h_slice.iloc[-1]['close']

                    exit_triggered, exit_type, exit_price = (
                        self.strategy.should_exit(
                            current_price  = current_price,
                            stop_loss      = pos['stop_loss'],
                            take_profit_1  = pos['take_profit_1'],
                            take_profit_2  = pos['take_profit_2'],
                        )
                    )

                    if exit_triggered:
                        self._close_position(
                            symbol=symbol,
                            exit_price=exit_price,
                            exit_type=exit_type,
                            timestamp=ts,
                        )

                # ── تحديث منحنى الأسهم ─────────────────
                self.equity_curve.append(self.current_balance)
                self.timestamps.append(ts)

            logger.success(
                f"✅ انتهى Backtesting | عدد الصفقات: {len(self.trades)}"
            )

            report = self.get_performance_report()

            if not self.trades:
                logger.warning(
                    "⚠️ لم تُنفَّذ أي صفقات.\n"
                    "   الأسباب المحتملة:\n"
                    "   • شروط الاستراتيجية لم تتحقق في هذه الفترة (طبيعي)\n"
                    "   • فترة البيانات قصيرة جداً (جرب days=90)\n"
                    "   • السوق كان في حالة sideways خلال هذه الفترة"
                )

            return report

        except Exception as e:
            logger.error(f"❌ خطأ في Backtesting: {e}")
            import traceback
            traceback.print_exc()
            return {}

    # ═══════════════════════════════════════════════════
    # دوال فتح وإغلاق الصفقات
    # ═══════════════════════════════════════════════════

    def _open_position(
        self,
        symbol:      str,
        entry_price: float,
        atr:         float,
        timestamp,
    ):
        """
        ✅ الإصلاح Bug 2: يأخذ symbol كمعامل صريح

        فتح صفقة جديدة في الـ Backtesting
        """
        try:
            # حساب SL و TP
            exits = self.strategy.calculate_exits(entry_price, atr)

            if not exits.get('valid', False):
                logger.warning(
                    f"⚠️ R:R غير كافٍ | "
                    f"R:R={exits.get('risk_reward_ratio', 0):.2f} "
                    f"< {self.strategy.MIN_RISK_REWARD}"
                )
                return

            # حساب حجم الصفقة
            pos_size = self.strategy.calculate_position_size(
                balance         = self.current_balance,
                entry_price     = entry_price,
                stop_loss_price = exits['stop_loss'],
            )

            if not pos_size:
                return

            trade_data = {
                'symbol':        symbol,
                'entry_price':   entry_price,
                'entry_time':    timestamp,
                'stop_loss':     exits['stop_loss'],
                'take_profit_1': exits['take_profit_1'],
                'take_profit_2': exits['take_profit_2'],
                'contract_size': pos_size.get('contract_size', 0),
                'leverage':      pos_size.get('leverage', 1),
                'risk_reward':   exits.get('risk_reward_ratio', 0),
            }

            # التحقق من صحة الصفقة
            if not self.strategy.validate_trade(trade_data):
                return

            # خصم التكلفة من الرصيد
            cost = entry_price * trade_data['contract_size']
            self.current_balance -= cost

            # ✅ الإصلاح Bug 2: المفتاح هو symbol الفعلي
            self.positions[symbol] = trade_data
            self.total_trades += 1

            logger.info(
                f"📈 [BT] فتح LONG | {symbol} @ ${entry_price:,.2f} | "
                f"الحجم: {trade_data['contract_size']:.4f} | "
                f"R:R: {exits['risk_reward_ratio']:.2f} | "
                f"{timestamp}"
            )

        except Exception as e:
            logger.error(f"❌ خطأ في _open_position: {e}")

    def _close_position(
        self,
        symbol:     str,
        exit_price: float,
        exit_type:  str,
        timestamp,
    ):
        """
        ✅ الإصلاح Bug 2: يأخذ symbol كمعامل ويستخدمه كمفتاح

        إغلاق صفقة وتسجيل النتيجة
        """
        try:
            # ✅ الإصلاح: self.positions[symbol] بدلاً من self.positions['symbol']
            if symbol not in self.positions:
                return

            position = self.positions.pop(symbol)

            contract_size = position['contract_size']
            profit = (exit_price - position['entry_price']) * contract_size
            profit_pct = (
                profit / (position['entry_price'] * contract_size) * 100
                if contract_size > 0 else 0
            )

            # استرداد الرصيد
            self.current_balance += exit_price * contract_size

            # تسجيل النتيجة
            trade = {
                'symbol':        symbol,
                'entry_price':   position['entry_price'],
                'exit_price':    exit_price,
                'entry_time':    position['entry_time'],
                'exit_time':     timestamp,
                'contract_size': contract_size,
                'leverage':      position['leverage'],
                'profit':        profit,
                'profit_pct':    profit_pct,
                'exit_type':     exit_type,
                'risk_reward':   position['risk_reward'],
            }
            self.trades.append(trade)

            if profit > 0:
                self.winning_trades += 1
                icon = "✅"
            else:
                self.losing_trades += 1
                icon = "❌"

            logger.info(
                f"{icon} [BT] إغلاق ({exit_type}) | {symbol} | "
                f"IN: ${position['entry_price']:,.2f} → OUT: ${exit_price:,.2f} | "
                f"P&L: ${profit:+,.2f} ({profit_pct:+.2f}%)"
            )

        except Exception as e:
            logger.error(f"❌ خطأ في _close_position: {e}")

    # ═══════════════════════════════════════════════════
    # تقرير الأداء
    # ═══════════════════════════════════════════════════

    def get_performance_report(self) -> Dict:
        """
        تقرير الأداء الشامل بجميع المؤشرات
        """
        if not self.trades:
            return {
                'status':          'no_trades',
                'total_trades':    0,
                'win_rate':        0,
                'total_profit':    0,
                'total_profit_pct': 0,
                'roi':             0,
                'final_balance':   self.current_balance,
                'max_loss':        0,
            }

        df_trades = pd.DataFrame(self.trades)
        profits   = df_trades['profit']

        total_profit = profits.sum()
        wins         = profits[profits > 0]
        losses       = profits[profits <= 0]

        profit_factor = (
            wins.sum() / abs(losses.sum())
            if len(losses) > 0 and losses.sum() != 0
            else float('inf')
        )

        # أقصى تراجع (Max Drawdown)
        equity = pd.Series(self.equity_curve)
        peak   = equity.expanding().max()
        dd     = (equity - peak) / peak * 100
        max_dd = dd.min()

        return {
            'status':           'completed',
            'total_trades':     len(df_trades),
            'winning_trades':   len(wins),
            'losing_trades':    len(losses),
            'win_rate':         len(wins) / len(df_trades) * 100,
            'total_profit':     total_profit,
            'total_profit_pct': total_profit / self.initial_balance * 100,
            'avg_profit':       profits.mean(),
            'avg_win':          wins.mean()          if len(wins)   > 0 else 0,
            'avg_loss':         losses.mean()         if len(losses) > 0 else 0,
            'max_profit':       profits.max(),
            'max_loss':         profits.min(),
            'profit_factor':    profit_factor,
            'max_drawdown_pct': max_dd,
            'final_balance':    self.current_balance,
            'roi':              (self.current_balance - self.initial_balance)
                                / self.initial_balance * 100,
            'total_signals':    len(self.signals),
        }

    def print_report(self):
        """طباعة تقرير منسق وشامل"""
        report = self.get_performance_report()

        print("\n" + "═" * 70)
        print("📊 تقرير Backtesting الشامل (نفس الاستراتيجية الأساسية v1.1)")
        print("═" * 70)

        if report.get('status') == 'no_trades':
            print("\n⏳ لم تُنفَّذ أي صفقات في هذه الفترة.")
            print(f"   الرصيد الحالي: ${report['final_balance']:,.2f}")
            print("═" * 70)
            return

        print(f"\n💰 الأرصدة:")
        print(f"   الأولي:       ${self.initial_balance:>12,.2f}")
        print(f"   النهائي:      ${report['final_balance']:>12,.2f}")
        pnl_sign = '+' if report['total_profit'] >= 0 else ''
        print(
            f"   إجمالي P&L:   "
            f"{pnl_sign}${report['total_profit']:>11,.2f}  "
            f"({pnl_sign}{report['roi']:.2f}%)"
        )

        print(f"\n📈 الصفقات:")
        print(f"   الإجمالي:     {report['total_trades']:>6}")
        print(f"   رابحة:        {report['winning_trades']:>6}  "
              f"({report['win_rate']:.1f}%)")
        print(f"   خاسرة:        {report['losing_trades']:>6}")

        print(f"\n🎯 جودة الصفقات:")
        print(f"   متوسط الربح:   ${report['avg_win']:>10,.2f}")
        print(f"   متوسط الخسارة: ${report['avg_loss']:>10,.2f}")
        print(f"   Profit Factor:  {report['profit_factor']:>9.2f}")
        print(f"   Max Drawdown:  {report['max_drawdown_pct']:>9.2f}%")

        print("\n" + "═" * 70)


# ─────────────────────────────────────────────────────────
# اختبار سريع عند التشغيل المباشر
# ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    engine = AdvancedBacktestingEngine(initial_balance=10_000.0)
    logger.success("✅ محرك Backtesting جاهز للاختبار")