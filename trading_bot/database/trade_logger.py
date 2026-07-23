"""
═══════════════════════════════════════════════════════════
مسجّل الصفقات — TradeLogger
═══════════════════════════════════════════════════════════
الجسر بين المكونات الحية (OrderManager، RiskManager، TradingStrategy)
وطبقة التخزين الدائم (database/models.py، Phase 5.1). مسؤولية واحدة
فقط: "متى وماذا نُسجِّل ونستعلم عنه" — لا يتخذ أي قرار تداول، ولا
يعرف شيئاً عن Exchange أو CCXT، فقط يحوِّل بيانات حية (dicts) إلى صفوف
دائمة (Peewee rows) والعكس.

مصدر البيانات لكل دالة (بالاسم الحرفي الفعلي من الكود الحقيقي):
    log_signal()   ← TradingStrategy.get_signal_breakdown()
    log_trade()    ← عنصر واحد من OrderManager.closed_positions
                      (نفس شكل closed_record في close_position() و
                      _monitor_paper_positions() — كلاهما يُنتج نفس
                      المفاتيح بالضبط، تحقّقتُ من هذا في الكود مباشرة)
    update_daily_performance() ← RiskManager.get_risk_summary() +
                      OrderManager.get_session_summary()

قرار تصميم — ربط Signal↔Trade عبر ذاكرة مؤقتة لكل رمز:
    صف Trade لا يُنشَأ إلا عند إغلاق الصفقة (closed_record لا يوجد إلا
    حينها) — لكن الإشارة التي فتحت الصفقة تُسجَّل مسبقاً عند log_signal().
    لذا: log_signal() تحفظ صف Signal الذي should_trade=True مؤقتاً في
    self._pending_signal_by_symbol[symbol]؛ عندما تُغلَق الصفقة لاحقاً
    ويُستدعى log_trade() لنفس الرمز، يُربَط تلقائياً عبر
    Signal.resulted_in_trade ثم تُزال من القاموس المؤقت. هذا الربط
    "قدر الإمكان" (best-effort) وليس مضموناً 100% — إن أُعيد تشغيل
    البوت بين فتح الصفقة وإغلاقها، ذاكرة self._pending_signal_by_symbol
    تُفقَد (نفس القيد المعروف لعدم وجود persistence لحالة الجلسة الحية
    نفسها، وليس لبيانات قاعدة البيانات) — الصفقة تُسجَّل بشكل صحيح
    ومستقل تماماً، فقط بدون رابط للإشارة الأصلية في تلك الحالة النادرة.

قرار تصميم — profit_factor=inf:
    OrderManager.get_session_summary() يُعيد float('inf') عندما لا توجد
    خسائر إطلاقاً (تقسيم على صفر). SQLite/Peewee وjson.dumps() لاحقاً لا
    يتعاملان معه بأمان دائماً، فنُحوِّله هنا لقيمة حارسة كبيرة (999.99)
    قبل التخزين — نفس فئة المشكلة التي واجهتها مع numpy.bool_ في 5.1،
    فعالجتها استباقياً بدل انتظار اكتشافها بالاختبار مجدداً.

ما لا يفعله هذا الملف (عمداً):
    - لا يستدعي init_db() تلقائياً عند الاستيراد — استدعاء صريح واحد
      عبر ensure_db_ready() من المستهلك (TradingBot) عند البدء.
    - لا يتصل بـ OrderManager/RiskManager مباشرة ولا يستدعي دوالهما —
      يستقبل فقط البيانات الجاهزة (dicts) التي يُمرِّرها المستدعي.
      الوصل الفعلي (متى بالضبط تُستدعى هذه الدوال من core/bot.py)
      خارج نطاق هذا الملف عمداً — راجع الملاحظة الختامية.
"""

from datetime import datetime, date as date_cls
from typing import Dict, List, Optional

import peewee as pw

from utils.logger import logger
from database.models import Trade, Signal, DailyPerformance, init_db

# ── قيمة حارسة بدل float('inf') (راجع الشرح أعلاه) ──────────
_INF_SENTINEL = 999.99


class TradeLogger:
    """
    مسجّل الصفقات — نقطة الوصول الوحيدة لكتابة/قراءة بيانات التداول
    الدائمة. يُنشَأ مرة واحدة في TradingBot (Phase 5 wiring) ويُستدعى
    من نقاط محدَّدة في حلقة التداول.
    """

    def __init__(self):
        # {symbol → Signal} — إشارات should_trade=True بانتظار ربطها
        # بالصفقة الناتجة عند إغلاقها (راجع docstring الملف)
        self._pending_signal_by_symbol: Dict[str, Signal] = {}

    # ═══════════════════════════════════════════════════
    # التهيئة
    # ═══════════════════════════════════════════════════

    def ensure_db_ready(self) -> bool:
        """يستدعيها المستهلك مرة واحدة عند بدء التشغيل. راجع database/models.py::init_db()."""
        return init_db()

    # ═══════════════════════════════════════════════════
    # الكتابة
    # ═══════════════════════════════════════════════════

    def log_signal(self, symbol: str, breakdown: Dict) -> Optional[Signal]:
        """
        تسجيل إشارة واحدة — المصدر الحرفي: ناتج
        TradingStrategy.get_signal_breakdown(df_1h, df_15m, df_5m).

        تُسجَّل الإشارة سواء أدّت لصفقة أم رُفضت في أي مستوى (gates /
        validate_signal / score) — هذا هو الهدف الأساسي من جدول signals:
        الإجابة على "كم إشارة اجتازت الشروط الستة لكن رُفضت لاحقاً،
        ولماذا؟" وهو سؤال لا تجيب عليه بيانات trades وحدها.

        Args:
            symbol:    الرمز (مثال: 'ETH/USDT')
            breakdown: القاموس الكامل من get_signal_breakdown() — يحوي
                       gate_passed, gate_result, signal_valid,
                       score_result, should_trade, entry_quality

        Returns:
            صف Signal المُنشَأ، أو None عند الفشل (يُسجَّل الخطأ ولا يُرفَع)
        """
        try:
            gate_result  = breakdown.get('gate_result', {}) or {}
            score_result = breakdown.get('score_result', {}) or {}

            sig = Signal.create(
                symbol         = symbol,
                gate_passed    = bool(breakdown.get('gate_passed', False)),
                signal_valid   = bool(breakdown.get('signal_valid', False)),
                should_trade   = bool(breakdown.get('should_trade', False)),
                entry_price    = gate_result.get('entry_price'),
                rsi            = gate_result.get('rsi'),
                adx            = gate_result.get('adx'),
                atr            = gate_result.get('atr'),
                macd           = gate_result.get('macd'),
                total_score    = score_result.get('total_score', 0),
                recommendation = score_result.get('recommendation', 'weak'),
                entry_quality  = breakdown.get('entry_quality', ''),
            )

            conditions = gate_result.get('conditions')
            if conditions:
                sig.set_conditions(conditions)   # تُحوِّل numpy.bool_ داخلياً (راجع models.py)
                sig.save()

            if sig.should_trade:
                # نحتفظ بها لربطها بالصفقة الناتجة عند إغلاقها لاحقاً
                self._pending_signal_by_symbol[symbol] = sig

            logger.debug(f"💾 إشارة سُجِّلت | {sig!r}")
            return sig

        except Exception as e:
            logger.error(f"❌ خطأ في تسجيل الإشارة ({symbol}): {e}")
            return None

    def log_trade(
        self,
        symbol: str,
        closed_record: Dict,
        environment: str = 'paper',
    ) -> Optional[Trade]:
        """
        تسجيل صفقة مكتملة — المصدر الحرفي: عنصر واحد من
        OrderManager.closed_positions (نفس الشكل من close_position()
        و_monitor_paper_positions() — تحقّقتُ أن كلاهما يُنتج نفس
        المفاتيح بالضبط).

        Args:
            symbol:        الرمز
            closed_record: dict الصفقة المغلَقة الكامل
            environment:   'paper' | 'live' — يحدِّدها المستدعي (عادةً
                           عبر hasattr(exchange, 'check_and_trigger_orders')
                           نفس المنطق المستخدم في OrderManager._is_paper)

        Returns:
            صف Trade المُنشَأ، أو None عند الفشل
        """
        try:
            pending_signal = self._pending_signal_by_symbol.pop(symbol, None)

            trade = Trade.create(
                symbol         = symbol,
                side           = closed_record.get('side', 'long'),
                environment    = environment,
                entry_price    = closed_record['entry_price'],
                exit_price     = closed_record['exit_price'],
                contract_size  = closed_record['contract_size'],
                leverage       = closed_record.get('leverage', 1),
                stop_loss      = closed_record.get('stop_loss'),
                take_profit_1  = closed_record.get('take_profit_1'),
                take_profit_2  = closed_record.get('take_profit_2'),
                sl_moved_to_be = bool(closed_record.get('sl_moved_to_be', False)),
                tp1_hit        = bool(closed_record.get('tp1_hit', False)),
                signal_score   = closed_record.get('signal_score', 0),
                rr_ratio       = closed_record.get('rr_ratio', 0),
                pnl            = closed_record['pnl'],
                pnl_pct        = closed_record['pnl_pct'],
                exit_reason    = closed_record.get('exit_reason', 'UNKNOWN'),
                opened_at      = self._parse_dt(closed_record.get('opened_at')),
                closed_at      = self._parse_dt(closed_record.get('closed_at')),
            )

            if pending_signal is not None:
                pending_signal.resulted_in_trade = trade
                pending_signal.save()
                logger.debug(f"🔗 رُبطت الصفقة بإشارتها الأصلية | {symbol}")

            logger.success(f"💾 صفقة سُجِّلت في قاعدة البيانات | {trade!r}")
            return trade

        except KeyError as e:
            logger.error(f"❌ حقل مفقود في closed_record عند تسجيل صفقة {symbol}: {e}")
            return None
        except Exception as e:
            logger.error(f"❌ خطأ في تسجيل الصفقة ({symbol}): {e}")
            return None

    def update_daily_performance(
        self,
        current_balance: float,
        risk_summary: Dict,
        session_summary: Dict,
    ) -> Optional[DailyPerformance]:
        """
        تحديث/إنشاء صف اليوم الحالي (UTC) — آمنة للاستدعاء المتكرر
        (get_or_create ثم تحديث)، وليس فقط عند دوران اليوم فعلياً؛ هذا
        متعمَّد حتى تعكس آخر حالة معروفة إذا تعطَّل البوت قبل نهاية
        اليوم الفعلية — يُستدعى المرجَّح من TradingBot._heartbeat_if_due().

        المصدر الحرفي:
            risk_summary    ← RiskManager.get_risk_summary(current_balance)
            session_summary ← OrderManager.get_session_summary()

        Args:
            current_balance: الرصيد الحالي (exchange.get_available_balance())
            risk_summary:    ناتج RiskManager.get_risk_summary()
            session_summary: ناتج OrderManager.get_session_summary()

        Returns:
            صف DailyPerformance المُحدَّث، أو None عند الفشل
        """
        try:
            today = datetime.utcnow().date()

            row, _created = DailyPerformance.get_or_create(
                date=today,
                defaults={
                    'starting_balance': current_balance,
                    'ending_balance':   current_balance,
                },
            )

            daily_pnl = risk_summary.get('daily_pnl', 0)

            row.ending_balance  = current_balance
            row.total_trades    = session_summary.get('total_closed', 0)
            row.winning_trades  = session_summary.get('winning_trades', 0)
            row.losing_trades   = session_summary.get('losing_trades', 0)
            row.win_rate        = session_summary.get('win_rate', 0)
            row.total_pnl       = daily_pnl
            row.total_pnl_pct   = (
                daily_pnl / row.starting_balance * 100
                if row.starting_balance > 0 else 0
            )
            row.avg_win         = session_summary.get('avg_win', 0)
            row.avg_loss        = session_summary.get('avg_loss', 0)
            row.profit_factor   = self._safe_float(session_summary.get('profit_factor', 0))
            row.save()

            logger.debug(f"💾 أداء اليوم حُدِّث | {row!r}")
            return row

        except Exception as e:
            logger.error(f"❌ خطأ في تحديث الأداء اليومي: {e}")
            return None

    # ═══════════════════════════════════════════════════
    # القراءة (لأوامر /stats و/balance المستقبلية في Telegram — Phase 5.3)
    # ═══════════════════════════════════════════════════

    def get_recent_trades(self, limit: int = 10, symbol: str = None) -> List[Trade]:
        """آخر N صفقة، اختيارياً مُصفَّاة برمز واحد — الأحدث أولاً."""
        try:
            query = Trade.select().order_by(Trade.closed_at.desc())
            if symbol:
                query = query.where(Trade.symbol == symbol)
            return list(query.limit(limit))
        except Exception as e:
            logger.error(f"❌ خطأ في get_recent_trades: {e}")
            return []

    def get_today_performance(self) -> Optional[DailyPerformance]:
        """صف أداء اليوم الحالي (UTC)، أو None إذا لم يُسجَّل شيء اليوم بعد."""
        try:
            today = datetime.utcnow().date()
            return DailyPerformance.get_or_none(DailyPerformance.date == today)
        except Exception as e:
            logger.error(f"❌ خطأ في get_today_performance: {e}")
            return None

    def get_performance_history(self, days: int = 7) -> List[DailyPerformance]:
        """آخر N يوم من الأداء المُجمَّع — الأحدث أولاً."""
        try:
            return list(
                DailyPerformance.select()
                .order_by(DailyPerformance.date.desc())
                .limit(days)
            )
        except Exception as e:
            logger.error(f"❌ خطأ في get_performance_history: {e}")
            return []

    def get_all_time_stats(self, environment: str = None) -> Dict:
        """
        إحصائيات تراكمية منذ بداية التسجيل — تُحسَب على مستوى قاعدة
        البيانات (SUM/COUNT) وليس بسحب كل الصفوف لبايثون، للأداء.

        Args:
            environment: فلترة اختيارية بـ 'paper' أو 'live' (None = الكل)
        """
        try:
            base = Trade.select()
            if environment:
                base = base.where(Trade.environment == environment)

            total = base.count()
            if total == 0:
                return {
                    'total_trades': 0, 'winning_trades': 0, 'losing_trades': 0,
                    'win_rate': 0.0, 'total_pnl': 0.0, 'profit_factor': 0.0,
                }

            wins   = base.where(Trade.pnl > 0)
            losses = base.where(Trade.pnl <= 0)
            win_count   = wins.count()
            loss_count  = losses.count()

            total_pnl        = base.select(pw.fn.SUM(Trade.pnl)).scalar() or 0.0
            total_wins_pnl   = wins.select(pw.fn.SUM(Trade.pnl)).scalar() or 0.0
            total_losses_pnl = losses.select(pw.fn.SUM(Trade.pnl)).scalar() or 0.0

            profit_factor = (
                abs(total_wins_pnl / total_losses_pnl)
                if total_losses_pnl != 0 else _INF_SENTINEL
            )

            return {
                'total_trades':   total,
                'winning_trades': win_count,
                'losing_trades':  loss_count,
                'win_rate':       round(win_count / total * 100, 2),
                'total_pnl':      round(total_pnl, 4),
                'profit_factor':  round(profit_factor, 3),
            }

        except Exception as e:
            logger.error(f"❌ خطأ في get_all_time_stats: {e}")
            return {}

    # ═══════════════════════════════════════════════════
    # دوال مساعدة داخلية
    # ═══════════════════════════════════════════════════

    @staticmethod
    def _parse_dt(value) -> datetime:
        """
        يقبل str بصيغة ISO (كما يُنتجها datetime.utcnow().isoformat()
        في كل أنحاء المشروع) أو datetime جاهزة أو None، ويُعيد datetime
        دائماً. closed_record في order_manager.py يخزِّن التواريخ كنصوص
        ISO، وPeewee DateTimeField تحتاج كائن datetime فعلياً.
        """
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value)
            except ValueError:
                pass
        return datetime.utcnow()

    @staticmethod
    def _safe_float(value) -> float:
        """
        تحويل آمن لقيمة float قد تكون inf (من profit_factor عند عدم
        وجود خسائر) إلى قيمة حارسة قابلة للتخزين — راجع شرح _INF_SENTINEL
        أعلى الملف.
        """
        try:
            v = float(value)
            if v in (float('inf'), float('-inf')) or v != v:  # v != v يكشف NaN
                return _INF_SENTINEL
            return v
        except (TypeError, ValueError):
            return 0.0


# ─────────────────────────────────────────────────────────
# اختبار سريع عند التشغيل المباشر
# ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n🧪 اختبار database/trade_logger.py مباشرةً\n")

    tl = TradeLogger()
    ok = tl.ensure_db_ready()
    print(f"ensure_db_ready() → {'✅ نجح' if ok else '❌ فشل'}")

    stats = tl.get_all_time_stats()
    print(f"\n📊 إحصائيات تراكمية حالية: {stats}")

    today = tl.get_today_performance()
    today_str = repr(today) if today else 'لا يوجد سجل بعد'
    print(f"📅 أداء اليوم: {today_str}")

    print("\n✅ اكتمل الاختبار — استدعِ tl.ensure_db_ready() من core/bot.py قبل الاستخدام الفعلي.")
