"""
═══════════════════════════════════════════════════════════
نماذج قاعدة البيانات — Database Models (Peewee / SQLite)
═══════════════════════════════════════════════════════════
الطبقة الوحيدة المسؤولة عن "أين تُخزَّن البيانات وبأي شكل" — لا تحتوي
على أي منطق تداول أو إحصائيات محسوبة، فقط تعريف المخطط (schema) وإدارة
الاتصال. منطق "متى وماذا نُسجِّل" مسؤولية database/trade_logger.py
(Phase 5.2)، تماماً كما أن core/strategy.py يقرر "هل ندخل؟" وrisk_manager.py
يقرر "كم الحجم؟" وorder_manager.py "ينفّذ" — كل ملف بمسؤولية واحدة فقط.

الجداول الثلاثة (حسب PROJECT_PROGRESS.md وBOT_STATUS_REPORT.md):
    Trade            → كل صفقة مكتملة (فُتحت ثم أُغلقت)
    Signal           → كل إشارة حُلِّلَت، سواء أدّت لصفقة أم رُفضت
                        (لتتبّع جودة الإشارات بمرور الوقت — كم إشارة
                        رُفضت ولماذا، وليس فقط الصفقات المنفَّذة)
    DailyPerformance → لقطة أداء يومية واحدة مجمَّعة (حدود اليوم UTC،
                        نفس حدود RiskManager._reset_daily_if_new_day())

الإعدادات (من config.yaml → database:):
    type: 'sqlite'
    sqlite.path: './data/trading_bot.db'   ← نسبي لمجلد التشغيل الحالي
                                              (نفس نمط logging.file.path
                                              في utils/logger.py — وليس
                                              نسبياً لموقع هذا الملف)

قرار تصميم — WAL mode:
    فعّلنا journal_mode=WAL بدل الوضع الافتراضي (rollback journal) لأن
    Phase 5.3 (notifications/telegram_bot.py) سيحتاج قراءة من نفس قاعدة
    البيانات بينما حلقة البوت الرئيسية (core/bot.py) تكتب صفقات جديدة —
    WAL يسمح بقراءة متزامنة أثناء الكتابة دون أخطاء "database is locked"
    التي يسبّبها الوضع الافتراضي في هذا السيناريو تحديداً.

قرار تصميم — Signal.conditions_json:
    الشروط الستة من TradingStrategy.check_buy_signal() تُخزَّن كـ JSON
    نصي (set_conditions/get_conditions) بدل 6+ أعمدة boolean منفصلة —
    حتى لا يحتاج المخطط تعديلاً (migration) عند إضافة شروط Short في
    Phase 6 (check_short_signal لن يُنتج نفس مفاتيح check_buy_signal
    بالضبط).

ما لا يفعله هذا الملف (عمداً، خارج نطاق 5.1):
    - لا يُستدعى init_db() تلقائياً عند الاستيراد (بخلاف utils/logger.py) —
      الاتصال بقاعدة بيانات عملية أثقل من إعداد logger، ويجب أن تكون
      صريحة. الاستدعاء الفعلي من core/bot.py سيأتي مع الوصل (wiring)
      في Phase 5.2، وليس هنا.
    - لا يحوّل بيانات OrderManager.closed_positions / TradingStrategy
      .get_signal_breakdown() مباشرة إلى صفوف — هذا التحويل مسؤولية
      trade_logger.py (5.2)، وليس مسؤولية طبقة المخطط نفسها.
    - لا نسخ احتياطي (auto_backup في config.yaml) — سيُدار من 5.2 أيضاً،
      لأنه يخص دورة حياة البيانات المُسجَّلة، لا تعريف المخطط.
"""

import json
from datetime import datetime
from pathlib import Path

import peewee as pw
import yaml

from utils.logger import logger

# ─────────────────────────────────────────────────────────
# قراءة الإعدادات
# ─────────────────────────────────────────────────────────

CONFIG_PATH = Path(__file__).parent.parent / 'config.yaml'

with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
    _config = yaml.safe_load(f)

_db_cfg     = _config.get('database', {})
_sqlite_cfg = _db_cfg.get('sqlite', {})

# نفس نمط logging.file.path في utils/logger.py: مسار نسبي لمجلد
# التشغيل الحالي (project root عند تشغيل main.py)، وليس لموقع هذا الملف
DB_PATH = _sqlite_cfg.get('path', './data/trading_bot.db')

# ── ضمان وجود مجلد قاعدة البيانات قبل فتح الاتصال ──────────
Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)


# ─────────────────────────────────────────────────────────
# كائن الاتصال بقاعدة البيانات
# ─────────────────────────────────────────────────────────

db = pw.SqliteDatabase(
    DB_PATH,
    pragmas={
        'journal_mode': 'wal',      # قراءة متزامنة مع الكتابة (راجع الشرح أعلاه)
        'foreign_keys': 1,
        'cache_size':   -1 * 64_000,  # 64MB cache (قيمة سالبة = كيلوبايت في Peewee/SQLite)
        'synchronous':  'normal',   # توازن أمان/أداء معقول مع WAL
    },
)


# ─────────────────────────────────────────────────────────
# فئة أساس مشتركة
# ─────────────────────────────────────────────────────────

class BaseModel(pw.Model):
    """
    فئة أساس مشتركة لكل النماذج — تربطها جميعاً بنفس قاعدة البيانات
    دون تكرار `class Meta: database = db` في كل فئة.
    """
    class Meta:
        database = db


# ─────────────────────────────────────────────────────────
# جدول trades — كل صفقة مكتملة
# ─────────────────────────────────────────────────────────

class Trade(BaseModel):
    """
    صفقة واحدة مكتملة (فُتحت ثم أُغلقت بالكامل أو جزئياً).

    الحقول تطابق مباشرةً بنية closed_record في
    OrderManager.close_position() / _monitor_paper_positions() —
    التحويل الفعلي يتم في database/trade_logger.py (Phase 5.2).
    """

    # ── هوية الصفقة ──────────────────────────────────────
    symbol       = pw.CharField(index=True)
    side         = pw.CharField(default='long')    # 'long' اليوم؛ 'short' مع Phase 6
    environment  = pw.CharField(default='paper')    # 'paper' | 'live'

    # ── معاملات التنفيذ ──────────────────────────────────
    entry_price    = pw.FloatField()
    exit_price     = pw.FloatField()
    contract_size  = pw.FloatField()
    leverage       = pw.FloatField()

    # ── SL/TP وحالتهما (من RiskManager.calculate_stops) ──
    stop_loss      = pw.FloatField(null=True)
    take_profit_1  = pw.FloatField(null=True)
    take_profit_2  = pw.FloatField(null=True)
    sl_moved_to_be = pw.BooleanField(default=False)   # نُقل SL لـ Breakeven بعد TP1؟
    tp1_hit        = pw.BooleanField(default=False)

    # ── جودة الإشارة والمخاطرة (من TradingStrategy/RiskManager) ──
    signal_score = pw.FloatField(default=0)    # 0-100 من calculate_signal_score
    rr_ratio     = pw.FloatField(default=0)    # Risk:Reward المحسوبة عند الفتح

    # ── النتيجة ───────────────────────────────────────────
    pnl         = pw.FloatField()
    pnl_pct     = pw.FloatField()
    exit_reason = pw.CharField()   # 'STOP_LOSS' | 'TAKE_PROFIT_1' | 'TAKE_PROFIT_2' | ...

    # ── التوقيت ───────────────────────────────────────────
    opened_at = pw.DateTimeField()
    closed_at = pw.DateTimeField(default=datetime.utcnow, index=True)

    class Meta:
        table_name = 'trades'
        indexes = (
            # الاستعلام الأكثر شيوعاً لاحقاً في trade_logger.py/telegram_bot.py:
            # "آخر صفقات هذا الرمز" أو "صفقات في فترة زمنية معيّنة"
            (('symbol', 'closed_at'), False),
        )

    @property
    def is_win(self) -> bool:
        """هل هذه الصفقة رابحة؟ (نفس منطق risk_manager.register_trade_result)"""
        return self.pnl > 0

    def __repr__(self) -> str:
        icon = '✅' if self.is_win else '❌'
        return (
            f"<Trade {icon} {self.symbol} {self.side} | "
            f"${self.entry_price:,.2f}→${self.exit_price:,.2f} | "
            f"PnL=${self.pnl:+,.2f} ({self.pnl_pct:+.2f}%) | {self.exit_reason}>"
        )


# ─────────────────────────────────────────────────────────
# جدول signals — كل إشارة حُلِّلَت (نُفِّذت أو رُفضت)
# ─────────────────────────────────────────────────────────

class Signal(BaseModel):
    """
    سجل كل إشارة حلّلها TradingStrategy.get_signal_breakdown() — سواء
    نتج عنها صفقة (should_trade=True) أم رُفضت في أي من المستويات
    الأربعة (gates / validate_signal / score / validate_trade).

    الهدف: تتبّع جودة الإشارات بمرور الوقت — مثال: "كم إشارة اجتازت
    الشروط الستة لكن رُفضت بواسطة validate_signal، ولماذا؟" — سؤال لا
    يمكن الإجابة عليه من جدول trades وحده لأنه يحتوي فقط الصفقات
    المنفَّذة فعلياً.
    """

    symbol    = pw.CharField(index=True)
    timestamp = pw.DateTimeField(default=datetime.utcnow, index=True)

    # ── نتائج المستويات الأربعة (راجع core/strategy.py) ──
    gate_passed  = pw.BooleanField()             # المستوى 1: الشروط الستة
    signal_valid = pw.BooleanField(default=False) # المستوى 2: validate_signal
    should_trade = pw.BooleanField(default=False) # القرار النهائي

    # ── بيانات الإشارة عند اللحظة ─────────────────────────
    entry_price = pw.FloatField(null=True)
    rsi         = pw.FloatField(null=True)
    adx         = pw.FloatField(null=True)
    atr         = pw.FloatField(null=True)
    macd        = pw.FloatField(null=True)

    # ── التقييم (المستوى 3: calculate_signal_score) ──────
    total_score    = pw.FloatField(default=0)
    recommendation = pw.CharField(default='weak')   # 'strong' | 'good' | 'weak'

    # ── وصف نصي وتفاصيل كاملة ─────────────────────────────
    entry_quality   = pw.TextField(null=True)   # breakdown['entry_quality']
    conditions_json = pw.TextField(null=True)   # الشروط الستة كاملة (JSON)

    # ── ربط اختياري بالصفقة الناتجة (إن وُجدت) ────────────
    resulted_in_trade = pw.ForeignKeyField(
        Trade, backref='originating_signal', null=True, on_delete='SET NULL'
    )

    class Meta:
        table_name = 'signals'
        indexes = (
            (('symbol', 'timestamp'), False),
        )

    def set_conditions(self, conditions: dict):
        """
        تخزين قاموس الشروط الستة (من signal_data['conditions']) كـ JSON نصي.

        ملاحظة: بعض قيم هذا القاموس تصل كـ numpy.bool_ وليس bool بايثون
        الأصلية (مصدرها indicators/trend.py::get_price_distance_from_ema
        التي تُرجع numpy.float64 رغم توقيعها المُعلَن -> float، فتُنتج أي
        مقارنة عليها numpy.bool_) — وnumpy.bool_ لا يقبله json.dumps()
        القياسي رغم أنه يبدو كـ bool عادية. نُحوِّل هنا صراحةً كل قيمة
        لـ bool بايثون أصيلة قبل التخزين، دون تعديل أي ملف آخر في المشروع.
        """
        safe_conditions = {k: bool(v) for k, v in conditions.items()}
        self.conditions_json = json.dumps(safe_conditions, ensure_ascii=False)

    def get_conditions(self) -> dict:
        """استرجاع قاموس الشروط الستة من التخزين النصي."""
        return json.loads(self.conditions_json) if self.conditions_json else {}

    def __repr__(self) -> str:
        icon = '✅' if self.should_trade else ('⚠️' if self.gate_passed else '➖')
        return (
            f"<Signal {icon} {self.symbol} | Score={self.total_score:.0f} | "
            f"{self.recommendation} | should_trade={self.should_trade}>"
        )


# ─────────────────────────────────────────────────────────
# جدول daily_performance — لقطة أداء يومية مجمَّعة
# ─────────────────────────────────────────────────────────

class DailyPerformance(BaseModel):
    """
    لقطة أداء ليوم واحد (حدود UTC — نفس حدود
    RiskManager._reset_daily_if_new_day()). صف واحد لكل يوم تقويمي؛
    يُملأ/يُحدَّث عند دوران اليوم في database/trade_logger.py (5.2)
    من بيانات RiskManager.get_risk_summary() وOrderManager
    .get_session_summary() المُجمَّعة خلال ذلك اليوم.
    """

    date = pw.DateField(unique=True, index=True)   # يوم تقويمي واحد (UTC)

    starting_balance = pw.FloatField()
    ending_balance    = pw.FloatField()

    total_trades   = pw.IntegerField(default=0)
    winning_trades = pw.IntegerField(default=0)
    losing_trades  = pw.IntegerField(default=0)
    win_rate       = pw.FloatField(default=0)    # نسبة مئوية 0-100

    total_pnl     = pw.FloatField(default=0)
    total_pnl_pct = pw.FloatField(default=0)     # نسبة من starting_balance
    avg_win       = pw.FloatField(default=0)
    avg_loss      = pw.FloatField(default=0)
    profit_factor = pw.FloatField(default=0)

    class Meta:
        table_name = 'daily_performance'

    def __repr__(self) -> str:
        sign = '+' if self.total_pnl >= 0 else ''
        return (
            f"<DailyPerformance {self.date} | "
            f"{self.total_trades} صفقة | "
            f"PnL={sign}${self.total_pnl:,.2f} ({sign}{self.total_pnl_pct:.2f}%) | "
            f"WinRate={self.win_rate:.1f}%>"
        )


# ─────────────────────────────────────────────────────────
# إدارة الاتصال — تهيئة صريحة (لا تلقائية عند الاستيراد)
# ─────────────────────────────────────────────────────────

ALL_MODELS = [Trade, Signal, DailyPerformance]


def init_db():
    """
    فتح الاتصال (إن لم يكن مفتوحاً) وإنشاء الجداول إن لم تكن موجودة.

    آمنة للاستدعاء المتكرر (idempotent) — create_tables(safe=True) لا
    يرفع خطأ إذا كانت الجداول موجودة بالفعل، لذا يمكن استدعاؤها في بداية
    كل تشغيل للبوت (core/bot.py، Phase 5.2) دون داعٍ لفحص يدوي مسبق.
    """
    try:
        if db.is_closed():
            db.connect(reuse_if_open=True)

        db.create_tables(ALL_MODELS, safe=True)

        table_names = ', '.join(m._meta.table_name for m in ALL_MODELS)
        logger.success(
            f"✅ قاعدة البيانات جاهزة | {DB_PATH} | الجداول: {table_names}"
        )
        return True

    except Exception as e:
        logger.error(f"❌ خطأ في تهيئة قاعدة البيانات: {e}")
        return False


def close_db():
    """إغلاق الاتصال بأمان — يُستدعى عند إيقاف البوت (TradingBot._shutdown، Phase 5.2)."""
    try:
        if not db.is_closed():
            db.close()
            logger.debug("🔒 تم إغلاق اتصال قاعدة البيانات")
    except Exception as e:
        logger.warning(f"⚠️ خطأ في إغلاق قاعدة البيانات: {e}")


def get_db_stats() -> dict:
    """
    إحصائيات سريعة عن حجم البيانات المخزَّنة — مفيدة لأمر /stats
    المستقبلي في Telegram (Phase 5.3) ولـ TradingBot.get_status().

    Returns:
        dict بعدد الصفوف في كل جدول، أو {} إذا لم تكن قاعدة البيانات
        مهيَّأة بعد (init_db() لم تُستدعَ).
    """
    if db.is_closed():
        return {}

    try:
        return {
            'trades_count':  Trade.select().count(),
            'signals_count': Signal.select().count(),
            'daily_records': DailyPerformance.select().count(),
            'db_path':       DB_PATH,
        }
    except Exception as e:
        logger.error(f"❌ خطأ في جلب إحصائيات قاعدة البيانات: {e}")
        return {}


# ─────────────────────────────────────────────────────────
# اختبار سريع عند التشغيل المباشر
# ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n🧪 اختبار database/models.py مباشرةً\n")

    ok = init_db()
    print(f"init_db() → {'✅ نجح' if ok else '❌ فشل'}")

    stats = get_db_stats()
    print(f"\n📊 إحصائيات قاعدة البيانات الحالية:")
    for k, v in stats.items():
        print(f"   {k}: {v}")

    print(f"\n📁 مسار قاعدة البيانات: {DB_PATH}")
    print(f"📋 الجداول: {[m._meta.table_name for m in ALL_MODELS]}")

    close_db()
    print("\n✅ اكتمل الاختبار — استدعِ init_db() من كودك قبل أي استخدام آخر للنماذج أعلاه.")
