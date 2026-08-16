"""
═══════════════════════════════════════════════════════════
بوت التليجرام — TelegramNotifier
═══════════════════════════════════════════════════════════
الجسر بين البوت الحي (TradingBot) ومالكه عبر Telegram — تنبيهات فورية
عند فتح/إغلاق صفقة، تقارير يومية/أسبوعية تلقائية، وأوامر تفاعلية
للاستعلام والتحكم (/status، /balance، /stats، /positions، /stop،
/emergency). راجع BOT_STATUS_REPORT.md (Phase 5.3) وconfig.yaml
(القسم telegram:) للمتطلبات الموثَّقة.

══════════════════════════════════════════
معمارية الاتصال (مهم لفهم بقية الملف):
══════════════════════════════════════════
python-telegram-bot==21.0.1 (المُثبَّت في requirements.txt) هو مكتبة
async بالكامل — لا واجهة متزامنة (sync) مثل النسخ الأقدم (v13). بينما
TradingBot.run() حلقة متزامنة بالكامل (time.sleep، لا asyncio). هذا
الملف يجسر الفجوة بطريقتين منفصلتين تماماً:

    ① الإرسال الصادر (send_*/notify_*):
       يُستدعى من TradingBot المتزامن مباشرة. كل استدعاء يفتح حلقة
       asyncio مؤقتة عبر asyncio.run() فقط لإرسال تلك الرسالة، ثم
       يُغلقها. تكلفة بسيطة مقبولة لأن أحداث الصفقات (فتح/إغلاق) غير
       متكررة نسبياً (مقارنةً بـ check_interval_seconds)، وتُبقي
       TradingBot.run() متزامنة بالكامل دون أي إعادة تصميم لها.

    ② الاستماع للأوامر الواردة (/status، /stop، ...):
       يحتاج حلقة أحداث asyncio دائمة (Application.run_polling())، لذا
       تُشغَّل في Thread منفصل (daemon) بحلقة asyncio خاصة بها — لا
       تتقاطع مع الحلقة الرئيسية المتزامنة في TradingBot.run(). يُستدعى
       start_command_listener(trading_bot) صراحةً من TradingBot.run()
       (وليس من TelegramNotifier.__init__) لأن معالجات الأوامر تحتاج
       مرجعاً لكائن TradingBot المكتمل التهيئة بالفعل — وهذا لا يتوفر
       إلا بعد انتهاء TradingBot.__init__().

══════════════════════════════════════════
مبدأ فصل المسؤوليات (Gating):
══════════════════════════════════════════
كل دالة send_*/notify_* عامة تقرأ إعداد telegram.notifications.* الخاص
بها من config.yaml داخلياً (كثابت صنف، بنفس نمط RiskManager بالضبط:
RISK_PCT = float(_risk_cfg.get(...))) وتتجاهل الاستدعاء بصمت إذا كان
معطَّلاً. TradingBot لا يتحقق من أي إعداد telegram قبل الاستدعاء —
فقط يُخطر بحدوث الشيء ("صفقة فُتحت")، والقرار "هل نُرسل فعلاً؟" بالكامل
مسؤولية TelegramNotifier وحده.

══════════════════════════════════════════
الأمان — تقييد الدردشة:
══════════════════════════════════════════
جميع معالجات الأوامر مُقيَّدة بـ filters.Chat(chat_id) على TELEGRAM_CHAT_ID
من .env — أي رسالة من دردشة أخرى تُتجاهَل تماماً على مستوى المكتبة قبل
وصولها لأي معالج. بدون هذا، أي شخص يعرف اسم مستخدم البوت قد يتمكن من
استدعاء /emergency ويُغلق كل الصفقات المفتوحة.

══════════════════════════════════════════
التدهور الآمن (Graceful Degradation):
══════════════════════════════════════════
TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID غير موجودين افتراضياً في .env.example
(قيم نائبة مثل 'your_telegram_bot_token_here'). إذا لم يُضبطا (أو تركا
كالقيم النائبة)، TelegramNotifier يعمل في "وضع معطَّل" بصمت: كل دالة
send_*/notify_* تُصبح no-op فورية دون أي محاولة اتصال أو رسالة تحذير
متكررة. هذا يطابق فلسفة المشروع القائمة: Telegram وقاعدة البيانات
تحسينات اختيارية لا تُعطِّل التشغيل الأساسي — نفس مبدأ عدم تعطيل حلقة
التداول عند فشل trade_logger (راجع database/trade_logger.py).
"""

import asyncio
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import yaml
from dotenv import load_dotenv
from telegram import Bot, Update
from telegram.error import TelegramError
from telegram.ext import Application, CommandHandler, ContextTypes, filters

from utils.logger import logger

# ─────────────────────────────────────────────────────────
# قراءة الإعدادات
# ─────────────────────────────────────────────────────────

CONFIG_PATH = Path(__file__).parent.parent / 'config.yaml'

with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
    _config = yaml.safe_load(f)

_telegram_cfg = _config.get('telegram', {})
_notif_cfg    = _telegram_cfg.get('notifications', {})

load_dotenv()

# ── قيم نائبة معروفة من .env.example — تُعامَل كـ"غير مضبوطة" ──────
_PLACEHOLDER_VALUES = {
    '', 'your_telegram_bot_token_here', 'your_telegram_chat_id_here',
}


# ═══════════════════════════════════════════════════════════
# فئة TelegramNotifier
# ═══════════════════════════════════════════════════════════

class TelegramNotifier:
    """
    الواجهة الوحيدة بين TradingBot وTelegram — إرسال واستقبال.

    التصميم:
        TelegramNotifier لا يعرف شيئاً عن استراتيجية التداول أو منطق
        المخاطر — فقط "كيف أُنسِّق هذا الحدث كرسالة، وهل أُرسلها الآن؟"
        (مطابقةً لمسؤولية RiskManager الوحيدة "ما الحجم؟ وهل مسموح؟"
        وOrderManager "نفّذ" — كل مكوّن بمسؤولية واحدة).
    """

    # ── أعلام الإشعارات (من config.yaml → telegram.notifications) ──
    # بنفس نمط قراءة الثوابت في RiskManager بالضبط (قراءة مرة واحدة
    # عند تحميل الوحدة، لا فحص متكرر لكل استدعاء)
    SEND_ENTRY_SIGNAL      = bool(_notif_cfg.get('send_entry_signal',      True))
    SEND_EXIT_SIGNAL       = bool(_notif_cfg.get('send_exit_signal',       True))
    SEND_TP_SIGNALS        = bool(_notif_cfg.get('send_tp_signals',        True))
    SEND_SL_SIGNALS        = bool(_notif_cfg.get('send_sl_signals',        True))
    SEND_ERROR_ALERTS      = bool(_notif_cfg.get('send_error_alerts',      True))
    SEND_DAILY_REPORT      = bool(_notif_cfg.get('send_daily_report',      True))
    SEND_WEEKLY_REPORT     = bool(_notif_cfg.get('send_weekly_report',     True))
    SEND_STATUS_ON_STARTUP = bool(_notif_cfg.get('send_status_on_startup', True))

    # ── جدولة التقارير الدورية (من config.yaml → telegram) ─────────
    DAILY_REPORT_TIME  = str(_telegram_cfg.get('daily_report_time',  '00:00'))
    WEEKLY_REPORT_DAY  = str(_telegram_cfg.get('weekly_report_day',  'sunday')).lower()
    WEEKLY_REPORT_TIME = str(_telegram_cfg.get('weekly_report_time', '00:00'))

    def __init__(self, bot_token: str = None, chat_id: str = None):
        """
        تهيئة TelegramNotifier.

        Args:
            bot_token: تجاوز اختياري لـ TELEGRAM_BOT_TOKEN من .env (للاختبار)
            chat_id:   تجاوز اختياري لـ TELEGRAM_CHAT_ID من .env (للاختبار)

        لا يفتح أي اتصال شبكي هنا — فقط يقرأ ويتحقق من الإعدادات. أول
        اتصال فعلي يحدث عند أول send_*/notify_* أو start_command_listener().
        """
        raw_token = (bot_token or os.getenv('TELEGRAM_BOT_TOKEN', '')).strip()
        raw_chat  = (chat_id  or os.getenv('TELEGRAM_CHAT_ID',  '')).strip()

        self._bot_token: Optional[str] = None
        self._chat_id:   Optional[int] = None
        self.enabled:    bool          = False

        if raw_token not in _PLACEHOLDER_VALUES and raw_chat not in _PLACEHOLDER_VALUES:
            try:
                self._chat_id = int(raw_chat)
                self._bot_token = raw_token
                self.enabled = True
            except ValueError:
                logger.warning(
                    f"⚠️ TELEGRAM_CHAT_ID='{raw_chat}' ليس رقماً صحيحاً — "
                    f"تعطيل إشعارات Telegram"
                )

        self._bot: Optional[Bot] = Bot(token=self._bot_token) if self.enabled else None
        self._trading_bot = None   # يُضبط لاحقاً عبر start_command_listener()
        self._listener_thread: Optional[threading.Thread] = None
        self._listener_app: Optional[Application] = None

        if self.enabled:
            logger.success(
                f"✅ TelegramNotifier جاهز | Chat ID: {self._chat_id} | "
                f"تقرير يومي: {self.DAILY_REPORT_TIME} | "
                f"تقرير أسبوعي: {self.WEEKLY_REPORT_DAY} {self.WEEKLY_REPORT_TIME}"
            )
        else:
            logger.info(
                "ℹ️ TelegramNotifier معطَّل — TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID "
                "غير مضبوطين في .env (أو ما زالا بالقيم النائبة). "
                "البوت سيعمل بشكل طبيعي بدون إشعارات Telegram."
            )

    # ═══════════════════════════════════════════════════
    # آلية الإرسال الداخلية
    # ═══════════════════════════════════════════════════

    def _send(self, text: str) -> bool:
        """
        إرسال رسالة نصية بشكل متزامن (من منظور المستدعي) — راجع شرح
        معمارية الاتصال ① أعلى الملف. تفشل بصمت (تحذير في السجلات فقط)
        عند أي خطأ شبكي أو من Telegram API، دون رفع استثناء — إشعار
        فاشل لا يجب أن يُسقط حلقة التداول أبداً.

        Returns:
            True إذا أُرسلت الرسالة فعلياً، False في أي حالة أخرى
            (معطَّل، أو فشل الإرسال)
        """
        if not self.enabled:
            return False

        try:
            asyncio.run(self._bot.send_message(chat_id=self._chat_id, text=text))
            return True
        except TelegramError as e:
            logger.warning(f"⚠️ فشل إرسال رسالة Telegram: {e}")
            return False
        except Exception as e:
            logger.warning(f"⚠️ خطأ غير متوقع أثناء إرسال Telegram: {e}")
            return False

    # ═══════════════════════════════════════════════════
    # الإشعارات الفورية — الصفقات
    # ═══════════════════════════════════════════════════

    def notify_trade_opened(self, symbol: str, position: Dict) -> bool:
        """
        إشعار فتح صفقة — يُستدعى من TradingBot فور نجاح
        OrderManager.open_position() (position = position_state المُعاد).
        مُقيَّد بـ SEND_ENTRY_SIGNAL.
        """
        if not self.SEND_ENTRY_SIGNAL:
            return False
        return self._send(self._format_entry_message(symbol, position))

    def notify_trade_closed(self, record: Dict) -> bool:
        """
        إشعار إغلاق صفقة — يُستدعى من TradingBot._sync_trade_log() لكل
        عنصر جديد في order_manager.closed_positions. يُوجِّه تلقائياً
        إلى العلم الصحيح حسب exit_reason:
            STOP_LOSS                → SEND_SL_SIGNALS
            TAKE_PROFIT_1/2          → SEND_TP_SIGNALS
            أي سبب آخر (يدوي/طارئ)  → SEND_EXIT_SIGNAL
        """
        reason = str(record.get('exit_reason', '')).upper()

        if reason == 'STOP_LOSS':
            flag = self.SEND_SL_SIGNALS
        elif reason in ('TAKE_PROFIT_1', 'TAKE_PROFIT_2'):
            flag = self.SEND_TP_SIGNALS
        else:
            flag = self.SEND_EXIT_SIGNAL

        if not flag:
            return False
        return self._send(self._format_exit_message(record))

    # ═══════════════════════════════════════════════════
    # الإشعارات الفورية — الأخطاء
    # ═══════════════════════════════════════════════════

    def send_error_alert(
        self,
        message: str,
        consecutive_errors: int = None,
        is_fatal: bool = False,
    ) -> bool:
        """
        تنبيه خطأ — يُستدعى من TradingBot._run_single_tick() عند استثناء
        في دورة الحلقة، ومن run() عند خطأ فادح غير متوقع. مُقيَّد بـ
        SEND_ERROR_ALERTS.
        """
        if not self.SEND_ERROR_ALERTS:
            return False
        return self._send(
            self._format_error_message(message, consecutive_errors, is_fatal)
        )

    # ═══════════════════════════════════════════════════
    # التقارير الدورية والحالة
    # ═══════════════════════════════════════════════════

    def send_startup_status(self, status: Dict) -> bool:
        """
        إشعار بدء التشغيل — يُستدعى من TradingBot._notify_startup_status()
        (status = bot.get_status()). مُقيَّد بـ SEND_STATUS_ON_STARTUP.
        """
        if not self.SEND_STATUS_ON_STARTUP:
            return False
        return self._send(self._format_startup_message(status))

    def send_daily_report(self, performance) -> bool:
        """
        التقرير اليومي — يُستدعى من TradingBot عند حلول daily_report_time
        (performance = DailyPerformance واحد من trade_logger.get_today_performance()،
        أو None إذا لم تُسجَّل أي بيانات بعد اليوم). مُقيَّد بـ SEND_DAILY_REPORT.
        """
        if not self.SEND_DAILY_REPORT:
            return False
        return self._send(self._format_daily_report(performance))

    def send_weekly_report(self, history: List) -> bool:
        """
        التقرير الأسبوعي — يُستدعى من TradingBot عند حلول weekly_report_day
        + weekly_report_time (history = قائمة DailyPerformance من
        trade_logger.get_performance_history(days=7)). مُقيَّد بـ
        SEND_WEEKLY_REPORT.
        """
        if not self.SEND_WEEKLY_REPORT:
            return False
        return self._send(self._format_weekly_report(history))

    # ═══════════════════════════════════════════════════
    # منسِّقات الرسائل (دوال خالصة — قابلة للاختبار بدون شبكة)
    # ═══════════════════════════════════════════════════

    @staticmethod
    def _format_entry_message(symbol: str, position: Dict) -> str:
        return (
            f"🟢 صفقة جديدة | {symbol}\n"
            f"الدخول: ${position.get('entry_price', 0):,.2f}\n"
            f"الحجم: {position.get('contract_size', 0):.6f} | "
            f"الرافعة: {position.get('leverage', 0)}x\n"
            f"SL: ${position.get('stop_loss', 0):,.2f}\n"
            f"TP1: ${position.get('take_profit_1', 0):,.2f} | "
            f"TP2: ${position.get('take_profit_2', 0):,.2f}\n"
            f"R:R: {position.get('rr_ratio', 0):.2f} | "
            f"الإشارة: {position.get('signal_score', 0):.0f}/100"
        )

    @staticmethod
    def _format_exit_message(record: Dict) -> str:
        pnl     = record.get('pnl', 0)
        pnl_pct = record.get('pnl_pct', 0)
        icon    = '✅' if pnl > 0 else '❌'
        return (
            f"{icon} إغلاق صفقة | {record.get('symbol', '?')}\n"
            f"السبب: {record.get('exit_reason', '?')}\n"
            f"${record.get('entry_price', 0):,.2f} → "
            f"${record.get('exit_price', 0):,.2f}\n"
            f"P&L: ${pnl:+,.2f} ({pnl_pct:+.2f}%)"
        )

    @staticmethod
    def _format_error_message(
        message: str,
        consecutive_errors: Optional[int],
        is_fatal: bool,
    ) -> str:
        header = "🚨 خطأ فادح — توقف البوت" if is_fatal else "⚠️ خطأ في دورة التداول"
        text = f"{header}\n{message}"
        if consecutive_errors is not None:
            text += f"\nأخطاء متتالية: {consecutive_errors}"
        return text

    @staticmethod
    def _format_startup_message(status: Dict) -> str:
        symbols = ', '.join(status.get('symbols', []))
        return (
            f"🤖 البوت يعمل الآن\n"
            f"البيئة: {status.get('environment', '?')}\n"
            f"الرموز: {symbols}\n"
            f"الرصيد: ${status.get('balance', 0):,.2f}\n"
            f"صفقات مفتوحة: {status.get('open_count', 0)}"
        )

    @staticmethod
    def _format_daily_report(performance) -> str:
        if performance is None:
            return "📅 التقرير اليومي\nلم تُسجَّل أي بيانات لليوم الحالي بعد."
        sign = '+' if performance.total_pnl >= 0 else ''
        return (
            f"📅 التقرير اليومي — {performance.date}\n"
            f"الصفقات: {performance.total_trades} "
            f"(رابحة: {performance.winning_trades} | خاسرة: {performance.losing_trades})\n"
            f"Win Rate: {performance.win_rate:.1f}%\n"
            f"PnL: {sign}${performance.total_pnl:,.2f} "
            f"({sign}{performance.total_pnl_pct:.2f}%)\n"
            f"الرصيد: ${performance.starting_balance:,.2f} → "
            f"${performance.ending_balance:,.2f}"
        )

    @staticmethod
    def _format_weekly_report(history: List) -> str:
        if not history:
            return "📊 التقرير الأسبوعي\nلا توجد بيانات لهذا الأسبوع بعد."

        total_trades = sum(d.total_trades for d in history)
        total_pnl    = sum(d.total_pnl    for d in history)
        wins         = sum(d.winning_trades for d in history)
        sign         = '+' if total_pnl >= 0 else ''
        win_rate     = (wins / total_trades * 100) if total_trades > 0 else 0.0

        lines = [
            f"📊 التقرير الأسبوعي ({len(history)} يوم)",
            f"الصفقات: {total_trades} | Win Rate: {win_rate:.1f}%",
            f"PnL الإجمالي: {sign}${total_pnl:,.2f}",
            "",
            "التفصيل اليومي:",
        ]
        for day in sorted(history, key=lambda d: d.date):
            day_sign = '+' if day.total_pnl >= 0 else ''
            lines.append(
                f"  {day.date}: {day.total_trades} صفقة | "
                f"{day_sign}${day.total_pnl:,.2f}"
            )
        return '\n'.join(lines)

    # ═══════════════════════════════════════════════════
    # مستمع الأوامر الواردة (Thread منفصل — راجع معمارية الاتصال ②)
    # ═══════════════════════════════════════════════════

    def start_command_listener(self, trading_bot) -> bool:
        """
        بدء الاستماع لأوامر Telegram الواردة في خيط (thread) منفصل.

        يُستدعى صراحةً من TradingBot.run() (وليس من __init__) لأن
        المعالجات تحتاج مرجعاً لكائن TradingBot المكتمل التهيئة — راجع
        شرح معمارية الاتصال ② أعلى الملف.

        آمنة للاستدعاء حتى إذا كان enabled=False (لا تفعل شيئاً).

        Args:
            trading_bot: كائن TradingBot الحي — تُستدعى دوال منه مباشرة
                         من معالجات الأوامر (get_status، stop، emergency_stop)

        Returns:
            True إذا بدأ خيط الاستماع فعلياً، False إذا كان معطَّلاً أو
            كان الخيط يعمل بالفعل
        """
        if not self.enabled:
            return False

        if self._listener_thread is not None and self._listener_thread.is_alive():
            logger.debug("ℹ️ مستمع أوامر Telegram يعمل بالفعل")
            return False

        self._trading_bot = trading_bot
        self._listener_thread = threading.Thread(
            target=self._run_command_listener,
            name="TelegramCommandListener",
            daemon=True,
        )
        self._listener_thread.start()
        logger.success("✅ مستمع أوامر Telegram بدأ (thread منفصل)")
        return True

    def _run_command_listener(self):
        """
        دالة تشغيل الخيط — تُنشئ حلقة asyncio خاصة بهذا الخيط ثم
        تُشغِّل Application.run_polling() بشكل مستمر. أي استثناء هنا
        يُسجَّل ولا يُسقط الخيط الرئيسي (TradingBot.run()) لأنه بالفعل
        يعمل في thread منفصل تماماً.
        """
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            chat_filter = filters.Chat(chat_id=self._chat_id)

            app = Application.builder().token(self._bot_token).build()
            app.add_handler(CommandHandler('start',     self._cmd_start,     filters=chat_filter))
            app.add_handler(CommandHandler('help',      self._cmd_help,      filters=chat_filter))
            app.add_handler(CommandHandler('status',    self._cmd_status,    filters=chat_filter))
            app.add_handler(CommandHandler('balance',   self._cmd_balance,   filters=chat_filter))
            app.add_handler(CommandHandler('stats',     self._cmd_stats,     filters=chat_filter))
            app.add_handler(CommandHandler('positions', self._cmd_positions, filters=chat_filter))
            app.add_handler(CommandHandler('stop',      self._cmd_stop,      filters=chat_filter))
            app.add_handler(CommandHandler('emergency', self._cmd_emergency, filters=chat_filter))

            self._listener_app = app
            # stop_signals=None: تسجيل معالجات SIGINT/SIGTERM يعمل فقط في
            # الخيط الرئيسي في بايثون — هذا الخيط ليس الرئيسي، وTradingBot
            # نفسه (في الخيط الرئيسي) هو من يتعامل مع Ctrl+C بالفعل.
            # close_loop=False: هذا الخيط "daemon" يُنهيه بايثون تلقائياً
            # عند خروج العملية، لا حاجة لإغلاق يدوي منظّم للحلقة.
            app.run_polling(stop_signals=None, close_loop=False)

        except Exception as e:
            logger.error(f"❌ فشل مستمع أوامر Telegram: {e}")

    # ── معالجات الأوامر (async — تُستدعى من مكتبة python-telegram-bot) ──

    async def _cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "🤖 أهلاً! أنا بوت التداول الآلي.\n"
            "استخدم /help لعرض الأوامر المتاحة."
        )

    async def _cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "الأوامر المتاحة:\n"
            "/status — حالة البوت الحالية\n"
            "/balance — الرصيد المتاح\n"
            "/stats — إحصائيات تراكمية\n"
            "/positions — الصفقات المفتوحة\n"
            "/stop — إيقاف آمن (لا يُغلق صفقات مفتوحة)\n"
            "/emergency — إغلاق طارئ لكل الصفقات المفتوحة فوراً"
        )

    async def _cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        status = self._trading_bot.get_status()
        await update.message.reply_text(self._format_startup_message(status))

    async def _cmd_balance(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        balance = self._trading_bot.exchange.get_available_balance()
        await update.message.reply_text(f"💰 الرصيد المتاح: ${balance:,.2f}")

    async def _cmd_stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        stats = self._trading_bot.trade_logger.get_all_time_stats()
        if not stats or stats.get('total_trades', 0) == 0:
            await update.message.reply_text("📊 لا توجد صفقات مسجَّلة بعد.")
            return
        sign = '+' if stats['total_pnl'] >= 0 else ''
        await update.message.reply_text(
            f"📊 إحصائيات تراكمية\n"
            f"الصفقات: {stats['total_trades']}\n"
            f"Win Rate: {stats['win_rate']:.1f}%\n"
            f"PnL: {sign}${stats['total_pnl']:,.2f}\n"
            f"Profit Factor: {stats['profit_factor']:.2f}"
        )

    async def _cmd_positions(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        positions = self._trading_bot.order_manager.get_all_open_positions()
        if not positions:
            await update.message.reply_text("📂 لا توجد صفقات مفتوحة حالياً.")
            return
        lines = ["📂 الصفقات المفتوحة:"]
        for symbol, pos in positions.items():
            lines.append(
                f"  {symbol}: ${pos['entry_price']:,.2f} | "
                f"SL=${pos['stop_loss']:,.2f} | حجم={pos['contract_size']:.6f}"
            )
        await update.message.reply_text('\n'.join(lines))

    async def _cmd_stop(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        self._trading_bot.stop()
        await update.message.reply_text(
            "🛑 طُلب الإيقاف — سيتوقف البوت بعد نهاية الدورة الحالية "
            "(لن يُغلق أي صفقة مفتوحة)."
        )

    async def _cmd_emergency(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("🚨 جارٍ الإغلاق الطارئ لكل الصفقات المفتوحة...")
        self._trading_bot.emergency_stop(reason='TELEGRAM_EMERGENCY_STOP')
        await update.message.reply_text("✅ تم الإغلاق الطارئ وإيقاف البوت.")


# ─────────────────────────────────────────────────────────
# اختبار سريع عند التشغيل المباشر
# ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    notifier = TelegramNotifier()
    print(f"\nTelegramNotifier.enabled = {notifier.enabled}")
    if not notifier.enabled:
        print("ℹ️ اضبط TELEGRAM_BOT_TOKEN وTELEGRAM_CHAT_ID في .env لتفعيل الإرسال الفعلي.")

    sample_position = {
        'entry_price': 43250.0, 'contract_size': 0.05, 'leverage': 3,
        'stop_loss': 43025.0, 'take_profit_1': 43550.0, 'take_profit_2': 43925.0,
        'rr_ratio': 2.17, 'signal_score': 82,
    }
    print("\n--- عيّنة رسالة فتح صفقة ---")
    print(TelegramNotifier._format_entry_message('BTC/USDT', sample_position))
