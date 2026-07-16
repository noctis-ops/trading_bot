"""
═══════════════════════════════════════════════════════════
المنسق الرئيسي للبوت — TradingBot
═══════════════════════════════════════════════════════════
نقطة التقاء جميع مكونات البوت في حلقة تداول واحدة متكاملة.

يُنسّق بين:
    MarketData       ← جلب البيانات وحساب المؤشرات
    TradingStrategy  ← قرار "هل ندخل؟" (check_buy_signal + validate_signal + scoring)
    RiskManager      ← "كم الحجم؟" و"هل التداول مسموح الآن؟" و SL/TP
    OrderManager     ← التنفيذ الفعلي + مراقبة الصفقات المفتوحة وإغلاقها

يعمل مع كلا الوضعين بدون أي تفريق في الكود:
    - PaperTradingExchange (TRADING_MODE=paper) — الوضع الوحيد المعتمد حالياً
    - BinanceExchange      (TRADING_MODE=live)  — للإنتاج فقط بعد اكتمال الاختبار

سلسلة التهيئة (تطابق نمط __main__ في core/order_manager.py):
    create_exchange() → MarketData(exchange) → TradingStrategy()
    → RiskManager(initial_balance) → OrderManager(exchange, risk_manager)

حلقة التداول (كل trading.check_interval_seconds من config.yaml):
    1. مراقبة الصفقات المفتوحة أولاً دائماً (SL/TP/Breakeven عبر OrderManager)
    2. البحث عن دخول جديد فقط إذا لم نصل trading.max_concurrent_positions
       (حد عام على مستوى البوت كله، وليس لكل رمز — لا يوجد تطبيق لهذا
       الحد حالياً في order_manager.py فهو مقصود ليكون هنا)
    3. لكل رمز: تجاهل إذا كانت هناك صفقة مفتوحة عليه، تجاهل إذا لم تُغلق
       شمعة main_timeframe جديدة بعد، تجاهل إذا كان التداول محظوراً حالياً
       (cooldown / حد الخسارة اليومية)
    4. عند إشارة صالحة (TradingStrategy.get_signal_breakdown): حساب SL/TP
       عبر RiskManager.calculate_stops() فقط (مصدر وحيد لتفادي parameter
       drift بين strategy.calculate_exits() المبرمَجة و risk_manager
       المبنية على config.yaml)، تحقق R:R، حساب الحجم، ثم
       OrderManager.open_position()

قرارات تصميم متعمّدة:
    - لا استدعاء لـ strategy.should_exit() يدوياً — OrderManager يغطي
      الخروج بالكامل داخلياً (SL/TP/Breakeven) لكلا وضعي paper/live.
    - الإيقاف الآمن (KeyboardInterrupt) لا يُغلق أي صفقة مفتوحة تلقائياً
      (سلوك موحّد بين paper/live، ولتفادي إغلاق قسري في live لمجرد
      إشارة إيقاف) — فقط يوقف الحلقة بأمان مع تحذير واضح.
    - stop() موجودة كخُطّاف لأمر /stop في Telegram المستقبلي (Phase 5).
    - emergency_stop() تُعرِّض OrderManager.emergency_close_all() على
      مستوى البوت — للاستخدام اليدوي أو من Telegram لاحقاً.
    - عدّاد أخطاء متتالية (MAX_CONSECUTIVE_TICK_ERRORS) يوقف البوت
      احترازياً إذا تكرر الفشل، بدل الدوران في حلقة أخطاء لا نهائية.
    - لا persistence بعد (database في Phase 5) — أي إعادة تشغيل تُصفّر
      حالة PaperTradingExchange بالكامل لأنها في الذاكرة فقط. هذا متوقّع
      ومتوافق مع ترتيب خارطة الطريق في BOT_STATUS_REPORT.md.

ملاحظة مهمة عن كشف الشمعة الجديدة:
    data.market_data.MarketData.is_new_candle() لا يحتفظ بأي حالة بين
    الاستدعاءات — يتحقق فقط من أن آخر شمعتين مجلوبتين متباعدتان بمقدار
    فترة زمنية واحدة بالضبط، وهو شرط شبه صحيح دائماً مع بيانات حقيقية
    (لا فجوات). لذلك فاستخدامه وحده لن يمنع إعادة تحليل نفس الشمعة
    المغلقة على كل tick. الحل هنا: تتبّع timestamp آخر شمعة main_timeframe
    عولجت فعلياً لكل رمز داخلياً في TradingBot (self._last_candle_time)
    دون أي تعديل على market_data.py.
"""

import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
import yaml

from utils.logger import logger
from core import create_exchange
from data.market_data import MarketData
from core.strategy import TradingStrategy
from core.risk_manager import RiskManager
from core.order_manager import OrderManager

# ─────────────────────────────────────────────────────────
# قراءة الإعدادات
# ─────────────────────────────────────────────────────────

CONFIG_PATH = Path(__file__).parent.parent / 'config.yaml'

with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
    _config = yaml.safe_load(f)

_trading_cfg  = _config.get('trading',  {})
_telegram_cfg = _config.get('telegram', {})


# ═══════════════════════════════════════════════════════════
# فئة TradingBot
# ═══════════════════════════════════════════════════════════

class TradingBot:
    """
    المنسق الرئيسي لكل مكونات البوت — نقطة الدخول الوحيدة للحلقة الحية.

    ══════════════════════════════════════════
    الموقع في معمارية البوت:
    ══════════════════════════════════════════

        TradingBot                ← ينسّق كل شيء (هذا الملف)
             │
             ├─→ MarketData        ← "ما هي البيانات الآن؟"
             ├─→ TradingStrategy   ← "هل ندخل؟ (4 مستويات قرار + score)"
             ├─→ RiskManager       ← "كم الحجم؟ وهل مسموح الآن؟"
             └─→ OrderManager      ← "نفّذ، وراقب، وأغلق عند الحاجة"

    ══════════════════════════════════════════
    مسؤوليات TradingBot حصراً (ولا شيء غيرها):
    ══════════════════════════════════════════
        ① جدولة الحلقة كل trading.check_interval_seconds
        ② فرض trading.max_concurrent_positions كحد عام على مستوى البوت
        ③ منع إعادة تحليل نفس الشمعة المغلقة أكثر من مرة لكل رمز
        ④ تمرير المخرجات بين المكونات بالترتيب الصحيح دون تكرار منطق
        ⑤ الصمود أمام الأخطاء العابرة (لا تسقط الحلقة كلها بسبب دورة فاشلة)
        ⑥ الإيقاف الآمن ونقاط التمديد لـ Telegram (Phase 5)

    كل منطق القرار (الإشارة/المخاطر/التنفيذ) موجود بالفعل في strategy.py
    وrisk_manager.py وorder_manager.py — هذا الملف لا يُعيد كتابة أي منه.
    """

    # ── ثوابت من config.yaml (trading:) ──────────────────
    SYMBOLS                 = list(_trading_cfg.get('symbols', []))
    MAIN_TIMEFRAME           = _trading_cfg.get('main_timeframe',           '15m')
    TREND_TIMEFRAME          = _trading_cfg.get('trend_timeframe',          '1h')
    CONFIRMATION_TIMEFRAME   = _trading_cfg.get('confirmation_timeframe',   '5m')
    MAX_CONCURRENT_POSITIONS = int(_trading_cfg.get('max_concurrent_positions', 1))
    CHECK_INTERVAL_SECONDS   = int(_trading_cfg.get('check_interval_seconds',   60))

    # ── ثوابت داخلية (لا مقابل لها في config.yaml بعد) ───
    HEARTBEAT_INTERVAL_SECONDS    = 900    # نبضة حالة (logger.bot_status) كل 15 دقيقة
    RISK_SUMMARY_INTERVAL_SECONDS = 3600   # ملخص مخاطر كامل كل ساعة
    MAX_CONSECUTIVE_TICK_ERRORS   = 10     # توقف احترازي بعد أخطاء متتالية في الحلقة

    def __init__(
        self,
        exchange      = None,
        market_data   = None,
        strategy      = None,
        risk_manager  = None,
        order_manager = None,
    ):
        """
        تهيئة TradingBot.

        Args:
            exchange, market_data, strategy, risk_manager, order_manager:
                معاملات اختيارية لحقن التبعيات (Dependency Injection).
                تُستخدم فقط للاختبار الآلي (test_bot.py — Phase 4.7) لحقن
                mocks أو مكونات مُعدَّة مسبقاً. التشغيل الطبيعي عبر main.py
                يستدعي TradingBot() بدون أي معطيات، فتُبنى كل المكونات
                تلقائياً بنفس سلسلة __main__ الموجودة في order_manager.py:
                    exchange = create_exchange()
                    rm = RiskManager(initial_balance=10000)
                    om = OrderManager(exchange, rm)
        """
        if not self.SYMBOLS:
            raise ValueError(
                "❌ trading.symbols فارغة في config.yaml — لا يوجد ما يُتداول عليه"
            )

        logger.info("🤖 جاري تهيئة TradingBot...")

        # ── سلسلة التهيئة (نفس نمط __main__ في order_manager.py) ──
        self.exchange    = exchange    or create_exchange()
        self.market_data = market_data or MarketData(self.exchange)
        self.strategy    = strategy    or TradingStrategy()

        if risk_manager is not None:
            self.risk_manager = risk_manager
        else:
            initial_balance    = self.exchange.get_available_balance()
            self.risk_manager  = RiskManager(initial_balance=initial_balance)

        self.order_manager = order_manager or OrderManager(self.exchange, self.risk_manager)

        # ── حالة التشغيل ──────────────────────────────────
        self.is_running: bool           = False
        self.start_time: Optional[datetime] = None
        self._stop_requested: bool      = False

        # ── تتبّع الشموع المُعالَجة لكل رمز (state داخلي حقيقي) ──
        # راجع الملاحظة أعلى الملف — MarketData.is_new_candle() لا يحتفظ
        # بحالة بين الاستدعاءات، لذا نتتبّعها هنا بأنفسنا.
        self._last_candle_time: Dict[str, pd.Timestamp] = {}

        # ── توقيت النبضات الدورية ────────────────────────
        self._last_heartbeat_at:    float = 0.0
        self._last_risk_summary_at: float = 0.0

        # ── عداد أخطاء الحلقة المتتالية (حماية من انهيار كامل) ──
        self._consecutive_tick_errors: int = 0

        logger.success(
            f"✅ TradingBot جاهز | "
            f"الرموز: {', '.join(self.SYMBOLS)} | "
            f"الأطر: {self.TREND_TIMEFRAME}/{self.MAIN_TIMEFRAME}/{self.CONFIRMATION_TIMEFRAME} | "
            f"صفقات متزامنة قصوى: {self.MAX_CONCURRENT_POSITIONS} | "
            f"دورة الفحص: {self.CHECK_INTERVAL_SECONDS}ث"
        )

    # ═══════════════════════════════════════════════════
    # حلقة التداول الرئيسية
    # ═══════════════════════════════════════════════════

    def run(self, max_iterations: Optional[int] = None):
        """
        بدء حلقة التداول الرئيسية — تعمل حتى الإيقاف اليدوي (Ctrl+C)
        أو استدعاء stop()/emergency_stop() أو نفاد max_iterations.

        Args:
            max_iterations: عدد دورات الحلقة قبل التوقف تلقائياً.
                             None (الافتراضي) = تشغيل بلا نهاية (24/7).
                             يُستخدم فقط من test_bot.py للاختبار الآلي —
                             لا يظهر أبداً في الاستخدام الحقيقي عبر main.py.
        """
        self.is_running      = True
        self._stop_requested = False
        self.start_time      = datetime.utcnow()

        self._print_startup_banner()
        self._notify_startup_status()

        iteration = 0
        try:
            while self.is_running and not self._stop_requested:
                iteration += 1
                self._run_single_tick()

                if max_iterations is not None and iteration >= max_iterations:
                    logger.info(
                        f"ℹ️ وصل max_iterations={max_iterations} — إيقاف (وضع اختبار)"
                    )
                    break

                time.sleep(self.CHECK_INTERVAL_SECONDS)

        except KeyboardInterrupt:
            print("\n\n👋 تم طلب الإيقاف من المستخدم (Ctrl+C)")

        except Exception as e:
            logger.critical(f"🚨 خطأ فادح غير متوقع في حلقة البوت — إيقاف: {e}")
            import traceback
            traceback.print_exc()

        finally:
            self._shutdown()

    def _run_single_tick(self):
        """
        دورة واحدة من حلقة التداول. مُعزولة في دالة خاصة حتى لا يؤدي
        خطأ في دورة واحدة إلى إيقاف البوت بالكامل (راجع
        MAX_CONSECUTIVE_TICK_ERRORS للحماية من حلقة أخطاء لا نهائية).
        """
        try:
            # 1) مراقبة الصفقات المفتوحة أولاً — دائماً، بغض النظر عن
            #    وجود شمعة جديدة. OrderManager يتكفّل داخلياً بمنطق
            #    SL/TP/Breakeven لكلا وضعي paper وlive، ويسجّل النتيجة
            #    تلقائياً عبر risk_manager.register_trade_result()
            self.order_manager.check_and_update_positions()

            # 2) البحث عن دخول جديد فقط إذا لم نصل الحد الأقصى العام
            if self.order_manager.get_open_positions_count() < self.MAX_CONCURRENT_POSITIONS:
                self._scan_for_entries()

            # 3) النبضات الدورية (حالة + ملخص مخاطر)
            self._heartbeat_if_due()

            self._consecutive_tick_errors = 0   # نجحت الدورة — إعادة تصفير العداد

        except Exception as e:
            self._consecutive_tick_errors += 1
            logger.error(
                f"❌ خطأ في دورة الحلقة "
                f"({self._consecutive_tick_errors}/{self.MAX_CONSECUTIVE_TICK_ERRORS}): {e}"
            )

            if self._consecutive_tick_errors >= self.MAX_CONSECUTIVE_TICK_ERRORS:
                logger.critical(
                    f"🚨 {self.MAX_CONSECUTIVE_TICK_ERRORS} أخطاء متتالية في الحلقة — "
                    f"إيقاف احترازي لتفادي حلقة أخطاء لا نهائية"
                )
                self._stop_requested = True

    # ═══════════════════════════════════════════════════
    # فحص الفرص والدخول في صفقات جديدة
    # ═══════════════════════════════════════════════════

    def _scan_for_entries(self):
        """
        فحص جميع الرموز المُعدَّة بحثاً عن فرصة دخول جديدة.
        يتوقف فور فتح صفقة واحدة (MAX_CONCURRENT_POSITIONS الحالي = 1).
        """
        for symbol in self.SYMBOLS:
            if self.order_manager.has_open_position(symbol):
                continue

            opened = self._scan_symbol_for_entry(symbol)
            if opened:
                break   # صفقة واحدة كحد أقصى لكل دورة — لا نكمل مسح باقي الرموز

    def _scan_symbol_for_entry(self, symbol: str) -> bool:
        """
        فحص رمز واحد بحثاً عن إشارة دخول صالحة وتنفيذها إن وُجدت.

        Args:
            symbol: الزوج (مثال: 'ETH/USDT')

        Returns:
            True إذا فُتحت صفقة فعلاً، False غير ذلك (لأي سبب: لا شمعة
            جديدة، تداول محظور، لا إشارة، R:R غير كافٍ، إلخ)
        """
        try:
            # ── جلب df الإطار الرئيسي أولاً للتحقق من الشمعة الجديدة ──
            # (يُستخدم أيضاً لاحقاً كـ df_15m في get_signal_breakdown —
            #  لا نجلبه مرتين)
            df_main = self.market_data.get_complete_dataframe(symbol, self.MAIN_TIMEFRAME)

            if not self._is_new_candle_for_symbol(symbol, df_main):
                return False   # لا شمعة main_timeframe جديدة مغلقة بعد لهذا الرمز

            # ── هل التداول مسموح الآن؟ (cooldown / حد الخسارة اليومية) ──
            balance = self.exchange.get_available_balance()
            allowed, reason = self.risk_manager.is_trading_allowed(balance)
            if not allowed:
                logger.debug(f"⏸️ {symbol}: {reason}")
                return False

            # ── إكمال باقي الأطر الزمنية فقط إذا استحق الأمر ─────────
            df_1h = self.market_data.get_complete_dataframe(symbol, self.TREND_TIMEFRAME)
            df_5m = self.market_data.get_complete_dataframe(symbol, self.CONFIRMATION_TIMEFRAME)

            # ── التقرير الشامل: gates + validate_signal + score + MTF ──
            breakdown = self.strategy.get_signal_breakdown(df_1h, df_main, df_5m)

            if not breakdown.get('should_trade', False):
                logger.debug(
                    f"➖ {symbol}: {breakdown.get('entry_quality', 'لا إشارة')}"
                )
                return False

            signal_data = breakdown['gate_result']
            score       = breakdown['score_result'].get('total_score', 0)
            signal_data['score'] = score   # لضمان تسجيلها الصحيح داخل OrderManager

            logger.info(
                f"🔎 إشارة محتملة | {symbol} | Score={score:.1f} | "
                f"{breakdown.get('entry_quality', '')}"
            )

            # ── حساب SL/TP — مصدر وحيد هو RiskManager (تفادي parameter drift) ──
            stops = self.risk_manager.calculate_stops(
                signal_data['entry_price'], signal_data['atr']
            )
            if not stops.get('valid', False):
                return False   # RiskManager يسجّل تحذيراً بنفسه بالفعل

            rr_ok, rr = self.risk_manager.validate_risk_reward(
                signal_data['entry_price'],
                stops['stop_loss'],
                stops['take_profit_1'],
                stops['take_profit_2'],
            )
            if not rr_ok:
                return False

            # ── حساب حجم الصفقة (يأخذ signal_score ونظام التقلب) ────
            position_data = self.risk_manager.calculate_position_size(
                balance         = balance,
                entry_price     = signal_data['entry_price'],
                stop_loss_price = stops['stop_loss'],
                signal_score    = score,
                volatility_df   = df_main,
            )
            if not position_data:
                return False

            # ── التنفيذ الفعلي عبر OrderManager ──────────────────────
            result = self.order_manager.open_position(
                symbol, signal_data, position_data, stops
            )

            if not result.get('success', False):
                logger.warning(
                    f"⚠️ فشل فتح الصفقة | {symbol} | {result.get('error', 'سبب غير معروف')}"
                )
                return False

            return True

        except Exception as e:
            logger.error(f"❌ خطأ في فحص {symbol}: {e}")
            return False

    # ═══════════════════════════════════════════════════
    # تتبّع الشموع الجديدة (state داخلي — راجع docstring الملف)
    # ═══════════════════════════════════════════════════

    def _is_new_candle_for_symbol(self, symbol: str, df_main_tf: pd.DataFrame) -> bool:
        """
        التحقق مما إذا كانت آخر شمعة مكتملة (iloc[-2]) على main_timeframe
        لم تُعالَج من قبل لهذا الرمز خلال حياة هذه الجلسة.

        هذا تتبّع حالة حقيقي عبر دورات الحلقة — بخلاف
        MarketData.is_new_candle() الذي لا يحتفظ بأي حالة بين
        الاستدعاءات (راجع docstring أعلى الملف للتفاصيل الكاملة).

        Args:
            symbol:     الزوج
            df_main_tf: DataFrame الإطار الرئيسي (main_timeframe) الذي
                        جُلب بالفعل لهذه الدورة

        Returns:
            True إذا كانت هذه أول مرة نرى فيها هذه الشمعة المغلقة لهذا
            الرمز، False إذا سبقت معالجتها.
        """
        if len(df_main_tf) < 2:
            return False   # بيانات غير كافية بعد

        current_candle_time = df_main_tf.index[-2]   # آخر شمعة مكتملة (نفس منطق iloc[-2] في strategy.py)
        last_seen = self._last_candle_time.get(symbol)

        if last_seen is not None and current_candle_time <= last_seen:
            return False   # نفس الشمعة التي عالجناها سابقاً — لا شيء جديد

        self._last_candle_time[symbol] = current_candle_time
        return True

    # ═══════════════════════════════════════════════════
    # النبضات الدورية (Heartbeat) — حالة + ملخصات
    # ═══════════════════════════════════════════════════

    def _heartbeat_if_due(self):
        """
        تسجيل حالة البوت دورياً عبر logger.bot_status() (كل
        HEARTBEAT_INTERVAL_SECONDS)، وملخص مخاطر كامل أقل تكراراً
        (كل RISK_SUMMARY_INTERVAL_SECONDS). هذا الاستدعاء المتكرر هو
        نقطة التمديد الطبيعية لإشعارات Telegram الدورية في Phase 5.
        """
        now = time.time()

        if now - self._last_heartbeat_at >= self.HEARTBEAT_INTERVAL_SECONDS:
            summary = self.order_manager.get_session_summary()
            logger.bot_status(
                status       = 'running',
                trades_count = summary['total_closed'],
                daily_pnl    = self.risk_manager.daily_pnl,
                uptime       = self._get_uptime_str(),
            )
            self._last_heartbeat_at = now

        if now - self._last_risk_summary_at >= self.RISK_SUMMARY_INTERVAL_SECONDS:
            self.risk_manager.print_risk_summary(
                current_balance=self.exchange.get_available_balance()
            )
            self._last_risk_summary_at = now

    def _get_uptime_str(self) -> str:
        """تنسيق مدة التشغيل كنص (مثال: '3d 4h 32m') — نفس الصيغة المستخدمة في مثال utils/logger.py"""
        if not self.start_time:
            return '0m'

        delta       = datetime.utcnow() - self.start_time
        days, rem   = divmod(int(delta.total_seconds()), 86400)
        hours, rem  = divmod(rem, 3600)
        minutes, _  = divmod(rem, 60)

        parts = []
        if days:  parts.append(f"{days}d")
        if hours: parts.append(f"{hours}h")
        parts.append(f"{minutes}m")
        return ' '.join(parts)

    # ═══════════════════════════════════════════════════
    # رسائل البداية والنهاية
    # ═══════════════════════════════════════════════════

    def _print_startup_banner(self):
        """طباعة لافتة البداية — بنفس أسلوب test_bot.py / test_strategy_comparison.py"""
        print("\n" + "╔" + "═" * 68 + "╗")
        print("║" + "🤖 Trading Bot — بدء حلقة التداول".center(68) + "║")
        print("╚" + "═" * 68 + "╝")
        print(f"\n  الرموز:              {', '.join(self.SYMBOLS)}")
        print(f"  الأطر الزمنية:        {self.TREND_TIMEFRAME} / {self.MAIN_TIMEFRAME} / {self.CONFIRMATION_TIMEFRAME}")
        print(f"  صفقات متزامنة قصوى:  {self.MAX_CONCURRENT_POSITIONS}")
        print(f"  دورة الفحص:          {self.CHECK_INTERVAL_SECONDS} ثانية")
        print(f"  الرصيد المتاح:       ${self.exchange.get_available_balance():,.2f}")
        print(f"  البيئة:              {getattr(self.exchange, 'environment', 'غير معروفة')}")
        print()

    def _notify_startup_status(self):
        """
        إشعار بدء التشغيل. حالياً عبر logger فقط (لا يوجد Telegram بعد —
        Phase 5). هذه هي نقطة الربط الجاهزة لإعداد
        telegram.notifications.send_status_on_startup في config.yaml
        عندما يُبنى notifications/telegram_bot.py.
        """
        if _telegram_cfg.get('notifications', {}).get('send_status_on_startup', False):
            logger.info(
                "📨 send_status_on_startup=true في config.yaml — "
                "بانتظار Phase 5 (Telegram) لتفعيله فعلياً"
            )

    def _shutdown(self):
        """تنظيف وطباعة ملخص الجلسة عند الإيقاف (بأي سبب: طلب المستخدم، خطأ فادح، أو max_iterations)."""
        self.is_running = False

        open_count = self.order_manager.get_open_positions_count()
        if open_count > 0:
            logger.warning(
                f"⚠️ توقف البوت مع {open_count} صفقة مفتوحة — "
                f"لن تُغلق تلقائياً (سلوك متعمَّد، راجع docstring الملف). "
                f"راجعها يدوياً قبل إعادة التشغيل إن لزم."
            )

        print("\n" + "═" * 70)
        print(f"📊 ملخص الجلسة | مدة التشغيل: {self._get_uptime_str()}")
        print("═" * 70)
        self.order_manager.print_session_summary()
        self.risk_manager.print_risk_summary(
            current_balance=self.exchange.get_available_balance()
        )

        logger.info("👋 TradingBot توقف بأمان")

    # ═══════════════════════════════════════════════════
    # التحكم الخارجي (يُستخدم لاحقاً من notifications/telegram_bot.py)
    # ═══════════════════════════════════════════════════

    def stop(self):
        """
        طلب إيقاف آمن للحلقة (لا يُغلق أي صفقة مفتوحة).
        خُطّاف جاهز لأمر /stop في Telegram — Phase 5.
        """
        logger.info("🛑 طلب إيقاف البوت (stop) — سيتوقف بعد نهاية الدورة الحالية")
        self._stop_requested = True

    def emergency_stop(self, reason: str = 'MANUAL_EMERGENCY_STOP'):
        """
        إيقاف طارئ: إغلاق كل الصفقات المفتوحة فوراً عبر
        OrderManager.emergency_close_all() ثم إيقاف الحلقة.
        خُطّاف جاهز لأمر طارئ مستقبلي في Telegram — Phase 5.

        Args:
            reason: سبب الإيقاف الطارئ (يظهر في السجلات وفي extra بيانات
                    كل صفقة أُغلقت عبر register_trade_result)
        """
        logger.warning(f"🚨 EMERGENCY STOP طُلب | السبب: {reason}")
        self.order_manager.emergency_close_all(reason=reason)
        self.stop()

    # ═══════════════════════════════════════════════════
    # الاستعلام عن الحالة (لأوامر /status المستقبلية في Telegram)
    # ═══════════════════════════════════════════════════

    def get_status(self) -> Dict:
        """
        تقرير شامل بحالة البوت الحالية. يجمع بيانات من جميع المكونات —
        نقطة الوصول الوحيدة التي سيحتاجها notifications/telegram_bot.py
        في Phase 5 لأوامر /status و/balance و/stats.

        Returns:
            dict شامل بحالة البوت الحالية
        """
        balance = self.exchange.get_available_balance()
        return {
            'is_running':      self.is_running,
            'uptime':          self._get_uptime_str(),
            'environment':     getattr(self.exchange, 'environment', 'غير معروفة'),
            'symbols':         self.SYMBOLS,
            'balance':         balance,
            'open_positions':  self.order_manager.get_all_open_positions(),
            'open_count':      self.order_manager.get_open_positions_count(),
            'session_summary': self.order_manager.get_session_summary(),
            'risk_summary':    self.risk_manager.get_risk_summary(current_balance=balance),
        }

    def print_status(self):
        """طباعة حالة البوت الحالية بشكل منسق — لأغراض المراقبة اليدوية والتصحيح."""
        s = self.get_status()

        print("\n" + "═" * 65)
        print("🤖 حالة TradingBot الحالية")
        print("═" * 65)

        status_icon = "🟢" if s['is_running'] else "🔴"
        print(f"\n  {status_icon} {'يعمل' if s['is_running'] else 'متوقف'} | مدة التشغيل: {s['uptime']}")
        print(f"  البيئة:        {s['environment']}")
        print(f"  الرصيد:        ${s['balance']:,.2f}")
        print(f"  صفقات مفتوحة:  {s['open_count']} / {self.MAX_CONCURRENT_POSITIONS}")

        for symbol, pos in s['open_positions'].items():
            print(
                f"     • {symbol}: دخول=${pos['entry_price']:,.2f} | "
                f"SL=${pos['stop_loss']:,.2f} | حجم={pos['contract_size']:.6f}"
            )

        summary = s['session_summary']
        print(f"\n  صفقات مغلقة:   {summary['total_closed']}  "
              f"(رابحة: {summary['winning_trades']} | خاسرة: {summary['losing_trades']})")
        print(f"  PnL اليومي:    ${self.risk_manager.daily_pnl:+.2f}")

        print("═" * 65)


# ─────────────────────────────────────────────────────────
# اختبار سريع عند التشغيل المباشر
# ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    bot = TradingBot()
    bot.print_status()
    print("\nلتشغيل البوت فعلياً (بلا نهاية، Ctrl+C للإيقاف):  bot.run()")
    print("لتشغيله لعدد محدود من الدورات (اختبار آلي):        bot.run(max_iterations=5)")
