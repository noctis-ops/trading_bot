"""
═══════════════════════════════════════════════════════════
تنبيهات Telegram — TelegramNotifier (Phase 5.3)
═══════════════════════════════════════════════════════════
نقطة الوصول الوحيدة لإشعارات Telegram في البوت.

المسؤولية الواحدة لهذا الملف:
    "إعلام المستخدم فورياً بكل ما يهمه أثناء تشغيل البوت"
        • تنبيه فوري عند فتح صفقة      (send_trade_entry)
        • تنبيه فوري عند إغلاق صفقة    (send_trade_exit)
        • أخطاء حرجة                  (send_error)
        • تقرير يومي/أسبوعي تلقائي     (جدولة عبر APScheduler)
        • أوامر عن بُعد               (/start /status /balance /stats
                                       /stop /emergency)

═══════════════════════════════════════════════════════════
المكتبة المستخدمة: python-telegram-bot (المقترحة في
requirements.txt) — وليس requests. PTB v21 مبنية على asyncio،
لذا نُشغّل Application في خيط خلفي يملك حلقة أحداث خاصة به،
ونرسل رسائل الخروج من حلقة البوت المتزامنة عبر
asyncio.run_coroutine_threadsafe() إلى تلك الحلقة.

═══════════════════════════════════════════════════════════
قرار تصميم — أمان أوامر تغيير الحالة:
    أوامر /stop و/emergency لا تُنفَّذ مباشرة من خيط Telegram
    (حماية من السباق على الحالة) بل تُقف في صفّ انتظار آمن
    (command queue) تستهلكها حلقة البوت الرئيسية في نهاية
    الدورة — بنفس الآلية المستخدمة في core/bot.py.

═══════════════════════════════════════════════════════════
الإعدادات:
    .env  → TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID
    config.yaml → telegram: (أي تنبيه يرسل/لا يرسل + وقت التقرير)

    إذا بقي token هو القيمة الافتراضية 'your_telegram_bot_token_here'
    فإن is_enabled() يعيد False وتتوقف كل العمليات بأمان.
"""

import asyncio
import os
import threading
import time
import yaml
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

from utils.logger import logger

# ─────────────────────────────────────────────────────────
# قراءة الإعدادات
# ─────────────────────────────────────────────────────────

CONFIG_PATH = Path(__file__).parent.parent / 'config.yaml'

with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
    _config = yaml.safe_load(f)

_tg_cfg       = _config.get('telegram', {})
_notif_cfg    = _tg_cfg.get('notifications', {})
DAILY_TIME    = _tg_cfg.get('daily_report_time',  '00:00')
WEEKLY_DAY    = _tg_cfg.get('weekly_report_day',  'sunday').lower()
WEEKLY_TIME   = _tg_cfg.get('weekly_report_time', '00:00')

_TOKEN_PLACEHOLDER = 'your_telegram_bot_token_here'


# ═══════════════════════════════════════════════════════════
# فئة TelegramNotifier
# ═══════════════════════════════════════════════════════════

class TelegramNotifier:
    """
    موصّل تنبيهات Telegram — لا يتخذ قرارات، يُعلِم فقط.

    طريقتان للعمل:
    ─────────────────
    • استباقي (Proactive): من حلقة البوت عبر send_trade_entry() /
      send_trade_exit() / send_error() / send_daily_report().
    • تفاعلي (Reactive): أوامر /status /balance /stats /stop
      /emergency تُستقبل عبر Application (python-telegram-bot)
      وتُعالج في خيط خلفي يملك حلقة أحداث خاصة به.

    نقاط الربط مع TradingBot (core/bot.py):
        _notify_startup_status() → send_startup_status()
        _notify_trade_entry()    → send_trade_entry()
        _log_newly_closed_trades() → send_trade_exit()
        _process_telegram_commands() → consume_command()
    """

    POLL_TIMEOUT = 10   # ثوانٍ لـ getUpdates عبر python-telegram-bot

    def __init__(self, get_bot=None):
        """
        Args:
            get_bot: callable اختياري يُعيد TradingBot الحالي.
                     يُستخدم للأوامر التفاعلية وللتقارير المجدولة.
                     (يُمرِّره core/bot.py كـ lambda: self)
        """
        self.get_bot      = get_bot or (lambda: None)
        self._token       = os.getenv('TELEGRAM_BOT_TOKEN', '').strip()
        self._chat_id     = os.getenv('TELEGRAM_CHAT_ID', '').strip()

        # ── مُفعّل فقط إذا كان token صحيحاً وليس placeholder ──
        self._enabled = bool(
            self._token
            and self._token != _TOKEN_PLACEHOLDER
            and self._chat_id
        )

        # ── أمان تغيير الحالة: صفّ أوامر يستهلكه البوت ──────
        self._cmd_lock  = threading.Lock()
        self._cmd_queue: List[Tuple[str, int]] = []   # (command, chat_id)

        # ── Application (python-telegram-bot) وحلقة الأحداث ──
        self._app       = None
        self._loop      = None
        self._thread    = None
        self._started   = False

        # ── الجدولة ────────────────────────────────────────
        self._scheduler = None

        if self._enabled:
            # بناء Application (المكتبة المقترحة في requirements)
            self._app = Application.builder().token(self._token).build()
            self._register_handlers()
            logger.success("✅ TelegramNotifier جاهز (مُفعّل)")
        else:
            logger.info(
                "ℹ️ TelegramNotifier غير مفعّل — ضع TELEGRAM_BOT_TOKEN "
                "و TELEGRAM_CHAT_ID في .env (راجع .env.example)"
            )

    # ═══════════════════════════════════════════════════
    # الحالة العامة
    # ═══════════════════════════════════════════════════

    def is_enabled(self) -> bool:
        """هل التنبيهات مفعّلة؟ (يتطلب token + chat_id صحيحين)"""
        return self._enabled

    def _is_ready(self) -> bool:
        """هل Application مُهيَّأ وحلقة أحداثه تعمل؟"""
        return self._enabled and self._app is not None and self._loop is not None

    # ═══════════════════════════════════════════════════
    # الإرسال الأساسي
    # ═══════════════════════════════════════════════════

    def send_message(
        self,
        text: str,
        chat_id: Optional[str] = None,
        parse_mode: str = 'HTML',
    ) -> bool:
        """
        إرسال رسالة نصية إلى Telegram.

        يرسل عبر python-telegram-bot (application.bot.send_message)
        على حلقة أحداث خيط الـ Application عبر run_coroutine_threadsafe.

        Args:
            text:      نص الرسالة
            chat_id:   معرّف المحادثة (افتراضي: من .env)
            parse_mode: 'HTML' | 'MarkdownV2' | '' (None)

        Returns:
            True عند النجاح، False عند التعطّل أو الفشل (لا يرفع
            استثناءً ليستمر البوت دون انقطاع بسبب مشكلة شبكة عابرة)
        """
        if not self._is_ready():
            return False

        target = chat_id or self._chat_id
        try:
            future = asyncio.run_coroutine_threadsafe(
                self._app.bot.send_message(
                    chat_id                 = target,
                    text                    = text,
                    parse_mode              = parse_mode or None,
                    disable_web_page_preview = True,
                ),
                self._loop,
            )
            future.result(timeout=10)
            return True
        except Exception as e:
            logger.error(f"❌ خطأ في إرسال Telegram: {e}")
            return False

    # ═══════════════════════════════════════════════════
    # تنبيهات فورية (يستدعيها core/bot.py)
    # ═══════════════════════════════════════════════════

    def send_startup_status(self) -> bool:
        """رسالة بدء التشغيل — مفعّلة عبر send_status_on_startup في config."""
        if not _notif_cfg.get('send_status_on_startup', False):
            return False
        bot = self.get_bot()
        if bot is None:
            return False
        return self.send_message(self._format_status(bot))

    def send_trade_entry(
        self,
        symbol:        str,
        side:          str,
        entry:         float,
        contract_size: float,
        leverage:      float,
        sl:            float,
        tp1:           float,
        tp2:           float,
        score:         float = 0.0,
    ) -> bool:
        """تنبيه فوري عند فتح صفقة — send_entry_signal في config."""
        if not _notif_cfg.get('send_entry_signal', False):
            return False
        side_emoji = '🟢 LONG' if side == 'long' else '🔴 SHORT'
        text = (
            f"<b>🚀 صفقة جديدة</b>\n"
            f"{'─' * 24}\n"
            f"<b>الزوج:</b> <code>{symbol}</code>  <b>{side_emoji}</b>\n"
            f"<b>الدخول:</b> ${entry:,.2f} × {contract_size:.6f}\n"
            f"<b>الرافعة:</b> {leverage}x | <b>Score:</b> {score:.1f}\n"
            f"<b>SL:</b> ${sl:,.2f}\n"
            f"<b>TP1:</b> ${tp1:,.2f} | <b>TP2:</b> ${tp2:,.2f}"
        )
        return self.send_message(text)

    def send_trade_exit(
        self,
        symbol:   str,
        side:     str,
        entry:    float,
        exit_price: float,
        pnl:      float,
        pnl_pct:  float,
        reason:   str,
    ) -> bool:
        """
        تنبيه فوري عند إغلاق صفقة.

        يحترم فلاتر config:
            send_exit_signal (عام) + send_sl_signals / send_tp_signals
        """
        if not _notif_cfg.get('send_exit_signal', False):
            return False
        if reason == 'STOP_LOSS' and not _notif_cfg.get('send_sl_signals', False):
            return False
        if 'TAKE_PROFIT' in reason and not _notif_cfg.get('send_tp_signals', False):
            return False

        emoji  = '✅' if pnl > 0 else '❌'
        reason_label = {
            'STOP_LOSS':     'Stop Loss',
            'TAKE_PROFIT_1': 'هدف 1',
            'TAKE_PROFIT_2': 'هدف 2',
            'EMERGENCY':     'إغلاق طارئ',
        }.get(reason, reason)
        sign = '+' if pnl >= 0 else ''
        text = (
            f"<b>{emoji} إغلاق صفقة</b>\n"
            f"{'─' * 24}\n"
            f"<b>الزوج:</b> <code>{symbol}</code> "
            f"{'🟢 LONG' if side == 'long' else '🔴 SHORT'}\n"
            f"<b>السبب:</b> {reason_label}\n"
            f"<b>الدخول:</b> ${entry:,.2f} → <b>${exit_price:,.2f}</b>\n"
            f"<b>P&L:</b> {sign}${pnl:,.2f} ({sign}{pnl_pct:.2f}%)"
        )
        return self.send_message(text)

    def send_error(self, message: str) -> bool:
        """تنبيه خطأ حرج — send_error_alerts في config."""
        if not _notif_cfg.get('send_error_alerts', False):
            return False
        return self.send_message(
            f"<b>🚨 خطأ</b>\n{'─' * 24}\n<code>{message}</code>"
        )

    # ═══════════════════════════════════════════════════
    # التقارير المجدولة
    # ═══════════════════════════════════════════════════

    def send_daily_report(self) -> bool:
        """تقرير يومي تلقائي — send_daily_report في config."""
        if not _notif_cfg.get('send_daily_report', False):
            return False
        bot = self.get_bot()
        if bot is None:
            return False
        return self.send_message(self._format_daily_report(bot))

    def send_weekly_report(self) -> bool:
        """تقرير أسبوعي — send_weekly_report في config."""
        if not _notif_cfg.get('send_weekly_report', False):
            return False
        bot = self.get_bot()
        if bot is None:
            return False
        status  = bot.get_status()
        risk    = status.get('risk_summary', {})
        total_pnl = status.get('session_summary', {}).get('total_pnl', 0)
        pnl_sign  = '+' if total_pnl >= 0 else ''
        return self.send_message(
            f"<b>📅 التقرير الأسبوعي</b>\n{'─' * 24}\n"
            f"إجمالي PnL (جلسة): {pnl_sign}${total_pnl:,.2f}\n"
            f"إجمالي الصفقات: {risk.get('total_trades', 0)}\n"
            f"Win Rate: {risk.get('win_rate', 0):.1f}%"
        )

    # ═══════════════════════════════════════════════════
    # صفّ أوامر تغيير الحالة (يستهلكه bot.py)
    # ═══════════════════════════════════════════════════

    def queue_command(self, command: str, chat_id: int):
        """إضافة أمر /stop أو /emergency إلى صفّ آمن للاستهلاك لاحقاً."""
        with self._cmd_lock:
            self._cmd_queue.append((command.upper(), chat_id))

    def consume_command(self) -> Optional[Tuple[str, int]]:
        """يستدعيها core/bot.py في كل دورة — يُعيد أقدم أمر معلَّق أو None."""
        with self._cmd_lock:
            if not self._cmd_queue:
                return None
            return self._cmd_queue.pop(0)

    # ═══════════════════════════════════════════════════
    # بدء/إيقاف الخدمات الخلفية
    # ═══════════════════════════════════════════════════

    def start(self):
        """
        تشغيل خلفية Telegram: جدولة التقارير + خيط Application.

        آمنة للاستدعاء المتكرر (idempotent). لا تفعل شيئاً إذا كان
        notifier معطّلاً.
        """
        if not self._enabled or self._started:
            return
        self._started = True

        self._start_scheduler()
        self._start_background()

    def stop(self):
        """إيقاف خلفية Telegram بأمان (يستدعيها bot.py عند _shutdown)."""
        self._started = False

        if self._scheduler is not None:
            try:
                self._scheduler.shutdown(wait=False)
            except Exception:
                pass
            self._scheduler = None

        if self._app is not None and self._loop is not None:
            try:
                asyncio.run_coroutine_threadsafe(
                    self._app.updater.stop(), self._loop
                ).result(timeout=10)
                asyncio.run_coroutine_threadsafe(
                    self._app.stop(), self._loop
                ).result(timeout=10)
                asyncio.run_coroutine_threadsafe(
                    self._app.shutdown(), self._loop
                ).result(timeout=10)
                self._loop.call_soon_threadsafe(self._loop.stop)
            except Exception as e:
                logger.warning(f"⚠️ خطأ في إيقاف Telegram: {e}")

        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=5)
        self._thread = None

        logger.info("🔌 TelegramNotifier أوقف خلفيته بأمان")

    def _start_background(self):
        """بدء خيط Application الخلفي (يملك حلقة أحداث خاصة به)."""
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run_background,
            name='telegram-app',
            daemon=True,
        )
        self._thread.start()

        # انتظار جاهزية حلقة الأحداث قبل أي إرسال
        for _ in range(200):   # حتى ~10 ثوانٍ
            if self._loop is not None:
                break
            time.sleep(0.05)

    def _run_background(self):
        """
        حلقة الأحداث الخاصة بـ python-telegram-bot.

        نهيِّئ Application ثم نبدأ updater.start_polling() ثم
        loop.run_forever() ليبقى الخيط مستمعاً للأوامر.
        """
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            loop.run_until_complete(self._app.initialize())
            loop.run_until_complete(self._app.start())
            loop.run_until_complete(
                self._app.updater.start_polling(
                    allowed_updates=['message'],
                )
            )
            loop.run_forever()
        except Exception as e:
            logger.error(f"❌ خطأ في حلقة Telegram: {e}")
        finally:
            self._loop = None

    def _start_scheduler(self):
        """جدولة التقرير اليومي والأسبوعي عبر APScheduler (Background)."""
        try:
            from apscheduler.schedulers.background import BackgroundScheduler

            self._scheduler = BackgroundScheduler(timezone='UTC')

            dh, dm = self._parse_time(DAILY_TIME)
            self._scheduler.add_job(
                self.send_daily_report, 'cron',
                hour=dh, minute=dm,
                id='daily_report',
                replace_existing=True,
            )

            wh, wm = self._parse_time(WEEKLY_TIME)
            self._scheduler.add_job(
                self.send_weekly_report, 'cron',
                day_of_week=self._map_weekday(WEEKLY_DAY),
                hour=wh, minute=wm,
                id='weekly_report',
                replace_existing=True,
            )

            self._scheduler.start()
            logger.success(
                f"✅ جدولة Telegram مفعّلة | يومي {DAILY_TIME} | "
                f"أسبوعي {WEEKLY_DAY} {WEEKLY_TIME} (UTC)"
            )
        except Exception as e:
            logger.error(f"❌ خطأ في تشغيل جدولة Telegram: {e}")

    # ═══════════════════════════════════════════════════
    # معالجات الأوامر (python-telegram-bot)
    # ═══════════════════════════════════════════════════

    def _register_handlers(self):
        """تسجيل أوامر البوت لدى Application."""
        self._app.add_handler(CommandHandler('start',     self._cmd_start))
        self._app.add_handler(CommandHandler('status',    self._cmd_status))
        self._app.add_handler(CommandHandler('balance',   self._cmd_balance))
        self._app.add_handler(CommandHandler('stats',     self._cmd_stats))
        self._app.add_handler(CommandHandler('stop',      self._cmd_stop))
        self._app.add_handler(CommandHandler('emergency', self._cmd_emergency))
        logger.debug("🧩 سُجِّلت أوامر Telegram: /start /status /balance /stats /stop /emergency")

    async def _cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        bot = self.get_bot()
        if bot is None:
            await update.effective_message.reply_text("🤖 Trading Bot")
            return
        status = bot.get_status()
        env  = status.get('environment', 'غير معروفة')
        syms = ', '.join(status.get('symbols', []))
        await update.effective_message.reply_text(
            f"<b>🤖 Trading Bot</b>\n"
            f"مرحباً! أنا بوت تداول آلي على Binance Futures.\n"
            f"البيئة: <code>{env}</code>\n"
            f"الرموز: <code>{syms}</code>\n\n"
            f"استخدم /status للحالة، /balance للرصيد، /stats للإحصائيات.",
            parse_mode='HTML',
        )

    async def _cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        bot = self.get_bot()
        if bot is None:
            await update.effective_message.reply_text("⚠️ البوت غير متاح حالياً.")
            return
        await update.effective_message.reply_text(
            self._format_status(bot), parse_mode='HTML'
        )

    async def _cmd_balance(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        bot = self.get_bot()
        if bot is None:
            await update.effective_message.reply_text("⚠️ البوت غير متاح حالياً.")
            return
        status = bot.get_status()
        text = (
            f"<b>💰 الرصيد الحالي</b>\n"
            f"{'─' * 24}\n"
            f"المتاح: ${status.get('balance', 0):,.2f}\n"
            f"صفقات مفتوحة: {status.get('open_count', 0)}"
        )
        await update.effective_message.reply_text(text, parse_mode='HTML')

    async def _cmd_stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        bot = self.get_bot()
        if bot is None:
            await update.effective_message.reply_text("⚠️ البوت غير متاح حالياً.")
            return
        await update.effective_message.reply_text(
            self._format_stats(bot), parse_mode='HTML'
        )

    async def _cmd_stop(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        self.queue_command('stop', chat_id)
        await update.effective_message.reply_text(
            "🛑 طُلب الإيقاف الآمن — سيتوقف البوت في نهاية الدورة الحالية."
        )

    async def _cmd_emergency(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        self.queue_command('emergency', chat_id)
        await update.effective_message.reply_text(
            "🚨 طُلب الإيقاف الطارئ — سيُغلق كل الصفقات فوراً "
            "في نهاية الدورة الحالية."
        )

    # ═══════════════════════════════════════════════════
    # تنسيق الرسائل
    # ═══════════════════════════════════════════════════

    def _format_status(self, bot) -> str:
        """رسالة /status من TradingBot.get_status()."""
        s     = bot.get_status()
        icon  = '🟢 يعمل' if s.get('is_running') else '🔴 متوقف'
        risk  = s.get('risk_summary', {})
        cooldown = risk.get('is_in_cooldown', False)
        lines = [
            f"<b>🤖 حالة TradingBot</b>\n{'─' * 24}",
            f"الحالة: {icon} | مدة: {s.get('uptime', '0m')}",
            f"البيئة: <code>{s.get('environment', '?')}</code>",
            f"الرصيد: ${s.get('balance', 0):,.2f}",
            f"صفقات مفتوحة: {s.get('open_count', 0)}",
            "",
            f"صفقات مغلقة: {s.get('session_summary', {}).get('total_closed', 0)}",
            f"PnL اليومي: ${s.get('risk_summary', {}).get('daily_pnl', 0):+,.2f}",
            f"Win Rate: {risk.get('win_rate', 0):.1f}%",
            f"Cooldown: {'نشط ⏳' if cooldown else 'لا'}",
        ]
        return '\n'.join(lines)

    def _format_stats(self, bot) -> str:
        """رسالة /stats — إحصائيات تراكمية من الجلسة + قاعدة البيانات."""
        status  = bot.get_status()
        summary = status.get('session_summary', {})
        risk    = status.get('risk_summary', {})

        db_stats = "—"
        try:
            from database.trade_logger import TradeLogger
            tl = TradeLogger()
            db_stats = tl.get_all_time_stats()
        except Exception:
            db_stats = "—"

        lines = [
            f"<b>📊 إحصائيات البوت</b>\n{'─' * 24}",
            f"صفقات مغلقة: {summary.get('total_closed', 0)} "
            f"(رابحة: {summary.get('winning_trades', 0)} | "
            f"خاسرة: {summary.get('losing_trades', 0)})",
            f"Profit Factor: {summary.get('profit_factor', 0)}",
            f"Win Rate: {risk.get('win_rate', 0):.1f}%",
        ]
        if isinstance(db_stats, dict) and db_stats.get('total_trades'):
            lines.append("")
            lines.append("— قاعدة البيانات —")
            lines.append(f"إجمالي الصفقات: {db_stats.get('total_trades', 0)}")
            lines.append(f"PnL الكلي: ${db_stats.get('total_pnl', 0):,.2f}")
        return '\n'.join(lines)

    def _format_daily_report(self, bot) -> str:
        """تقرير يومي تلقائي — من TradingBot + RiskManager."""
        status  = bot.get_status()
        risk    = status.get('risk_summary', {})
        summary = status.get('session_summary', {})
        balance = status.get('balance', 0)
        day_pnl = risk.get('daily_pnl', 0)
        pnl_sign = '+' if day_pnl >= 0 else ''
        return (
            f"<b>📅 التقرير اليومي</b>\n{'─' * 24}\n"
            f"الرصيد الحالي: ${balance:,.2f}\n"
            f"PnL اليوم: {pnl_sign}${day_pnl:,.2f}\n"
            f"صفقات اليوم: {risk.get('daily_trades_count', 0)}\n"
            f"Win Rate: {risk.get('win_rate', 0):.1f}%\n"
            f"Cooldown: {'نشط ⏳' if risk.get('is_in_cooldown') else 'لا'}"
        )

    # ═══════════════════════════════════════════════════
    # دوال مساعدة
    # ═══════════════════════════════════════════════════

    @staticmethod
    def _parse_time(value: str) -> Tuple[int, int]:
        """تحليل 'HH:MM' → (ساعة, دقيقة). آمنة مع مدخلات غير صحيحة."""
        try:
            hour, minute = (int(x) for x in str(value).split(':'))
            return max(0, min(23, hour)), max(0, min(59, minute))
        except Exception:
            return 0, 0

    @staticmethod
    def _map_weekday(name: str) -> str:
        """خريطة أسماء الأيام إلى ما تقبله APScheduler cron."""
        mapping = {
            'monday': 'mon', 'tuesday': 'tue', 'wednesday': 'wed',
            'thursday': 'thu', 'friday': 'fri', 'saturday': 'sat',
            'sunday': 'sun',
        }
        return mapping.get(str(name).lower(), 'sun')


# ─────────────────────────────────────────────────────────
# اختبار سريع عند التشغيل المباشر
# ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    n = TelegramNotifier()
    print(f"\nTelegramNotifier مفحوص:")
    print(f"   مُفعّل:      {'نعم ✅' if n.is_enabled() else 'لا ❌ (ضع token في .env)'}")
    print(f"   تقرير يومي:  {DAILY_TIME} (UTC)")
    print(f"   تقرير أسبوعي: {WEEKLY_DAY} {WEEKLY_TIME} (UTC)")
    print()
    print("لاستخدامه من core/bot.py:")
    print("   from notifications.telegram_bot import TelegramNotifier")
    print("   notifier = TelegramNotifier(get_bot=lambda: bot)")
    print("   notifier.start()   # في run()")
    print("   notifier.stop()    # في _shutdown()")
