"""
═══════════════════════════════════════════════════════════
إدارة المخاطر الشاملة — RiskManager
═══════════════════════════════════════════════════════════
المسؤولية الوحيدة لهذا الملف: الحفاظ على رأس المال.

يُغطي:
    ① حساب حجم الصفقة الديناميكي     (Dynamic Position Sizing)
    ② إدارة الرافعة المالية            (Leverage Management)
    ③ حساب Stop Loss و Take Profit     (ATR-based Exits)
    ④ حساب وتحقق نسبة Risk:Reward     (R:R Validation)
    ⑤ إدارة الخسائر المتتالية          (Consecutive Loss Control)
    ⑥ حد الخسارة اليومية               (Daily Loss Limit)
    ⑦ تتبع حالة التداول                (Trading State Tracking)
    ⑧ الانتقال بـ SL إلى Breakeven     (Trailing SL Logic)
    ⑨ تقرير شامل لحالة المخاطر         (Risk Summary Report)

══════════════════════════════════════════
الإعدادات (من config.yaml):
══════════════════════════════════════════
    risk_percent_per_trade: 2.0    ← 2% من الرصيد لكل صفقة
    max_daily_loss_percent: 6.0    ← أقصى خسارة يومية قبل الإيقاف
    max_consecutive_losses: 3      ← عدد الخسائر المتتالية قبل Cooldown
    cooldown_minutes:       60     ← فترة التوقف بعد الخسائر المتتالية
    max_leverage:           10     ← أقصى رافعة مالية
    min_risk_reward_ratio:  2.0    ← أدنى نسبة R:R مقبولة
    move_sl_to_breakeven:   true   ← نقل SL إلى نقطة التعادل بعد TP1

══════════════════════════════════════════
تسلسل الاستدعاء الموصى به:
══════════════════════════════════════════
    1. is_trading_allowed()          ← هل يُسمح بالتداول الآن؟
    2. calculate_stops()             ← احسب SL و TP من ATR
    3. validate_risk_reward()        ← تحقق من R:R ≥ 2.0
    4. calculate_position_size()     ← احسب حجم الصفقة والرافعة
    5. [بعد التنفيذ] register_trade_result() ← سجّل النتيجة
    6. [عند TP1]     should_move_sl_to_breakeven() ← انقل SL؟
"""

import yaml
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from utils.logger import logger
from indicators.volatility import (
    calculate_atr_stops,
    get_volatility_regime,
    get_volatility_regime_label,
)

# ─────────────────────────────────────────────────────────
# قراءة الإعدادات
# ─────────────────────────────────────────────────────────

CONFIG_PATH = Path(__file__).parent.parent / 'config.yaml'

with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
    _config = yaml.safe_load(f)

_risk_cfg    = _config.get('risk_management', {})
_adv_cfg     = _config.get('advanced',        {})
_strat_cfg   = _config.get('strategy',        {})
_vol_cfg     = _strat_cfg.get('volatility',   {})


# ═══════════════════════════════════════════════════════════
# الفئة الرئيسية
# ═══════════════════════════════════════════════════════════

class RiskManager:
    """
    مدير المخاطر المركزي للبوت.

    يحتفظ بحالة داخلية مستمرة خلال جلسة التداول:
        - عداد الخسائر المتتالية
        - وقت بدء الـ Cooldown
        - سجل نتائج اليوم الحالي
        - إجمالي الخسارة اليومية

    التصميم:
        RiskManager لا يتخذ قرار الدخول أو الخروج —
        هذه مهمة strategy.py. مهمته الوحيدة هي:
            "ما الحجم الصحيح؟"  و  "هل الظروف آمنة للتداول؟"
    """

    # ── ثوابت مستخرجة من config.yaml ─────────────────────
    RISK_PCT         = float(_risk_cfg.get('risk_percent_per_trade', 2.0)) / 100
    MAX_DAILY_LOSS   = float(_risk_cfg.get('max_daily_loss_percent', 6.0)) / 100
    MAX_CONSEC_LOSS  = int(  _risk_cfg.get('max_consecutive_losses',  3))
    COOLDOWN_MIN     = int(  _risk_cfg.get('cooldown_minutes',        60))
    MAX_LEVERAGE     = int(  _risk_cfg.get('max_leverage',            10))
    MIN_RR           = float(_risk_cfg.get('min_risk_reward_ratio',  2.0))
    MOVE_SL_BREAKEVEN= bool( _risk_cfg.get('move_sl_to_breakeven_after_tp1', True))
    CLOSE_LOSING     = bool( _risk_cfg.get('close_losing_early',      False))
    CLOSE_LOSING_PCT = float(_risk_cfg.get('close_losing_percent',    5.0)) / 100

    # ── مضاعفات SL/TP (تتطابق مع strategy.py وconfig.yaml) ─
    ATR_SL_MULT  = float(_vol_cfg.get('atr_stop_loss_multiplier', 1.5))
    ATR_TP1_MULT = float(_vol_cfg.get('atr_tp1_multiplier',       2.0))
    ATR_TP2_MULT = float(_vol_cfg.get('atr_tp2_multiplier',       4.5))

    # ── تعديل الرافعة حسب نظام التقلب ─────────────────────
    LEVERAGE_FACTORS = {
        'high':        0.60,   # تقلب عالٍ   → أقصى 60% من MAX_LEVERAGE
        'normal':      1.00,   # تقلب طبيعي  → أقصى 100% من MAX_LEVERAGE
        'low':         0.80,   # تقلب منخفض  → أقصى 80%  (احتياط)
        'contracting': 0.50,   # ركود        → أقصى 50%  (طوارئ)
    }

    # ── تعديل حجم الصفقة حسب الخسائر المتتالية ───────────
    LOSS_SIZE_FACTORS = {
        0: 1.00,   # لا خسائر      → حجم كامل
        1: 0.75,   # خسارة واحدة  → 75%
        2: 0.50,   # خسارتان      → 50%
        3: 0.25,   # ثلاث (حد الـ cooldown) → 25% (احتياط ما بعد الـ cooldown)
    }

    def __init__(self, initial_balance: float = 0.0):
        """
        تهيئة RiskManager.

        Args:
            initial_balance: الرصيد الأولي عند بداية الجلسة.
                             يُستخدم لحساب الخسارة اليومية كنسبة.
                             يمكن تحديثه لاحقاً بـ update_balance().
        """
        self.initial_balance = initial_balance

        # ── حالة الخسائر المتتالية ──────────────────────
        self.consecutive_losses:    int               = 0
        self.cooldown_started_at:   Optional[datetime] = None
        self.is_in_cooldown:        bool               = False

        # ── تتبع اليوم الحالي ───────────────────────────
        self.daily_start_balance:   float              = initial_balance
        self.daily_pnl:             float              = 0.0
        self.daily_trades:          List[Dict]         = []
        self.daily_reset_date:      Optional[str]      = None
        self._reset_daily_if_new_day()

        # ── سجل جميع النتائج ───────────────────────────
        self.trade_history: List[Dict] = []
        self.total_trades:  int        = 0
        self.winning_trades: int       = 0

        logger.success(
            f"✅ RiskManager جاهز | "
            f"Risk/Trade={self.RISK_PCT*100:.1f}% | "
            f"MaxLev={self.MAX_LEVERAGE}x | "
            f"MaxDailyLoss={self.MAX_DAILY_LOSS*100:.1f}% | "
            f"CooldownAfter={self.MAX_CONSEC_LOSS} خسائر"
        )

    # ═══════════════════════════════════════════════════
    # ① حساب نقاط SL و TP
    # ═══════════════════════════════════════════════════

    def calculate_stops(
        self,
        entry_price: float,
        atr:         float,
    ) -> Dict:
        """
        حساب Stop Loss و Take Profit من ATR.

        يُفوَّض الحساب لـ indicators/volatility.py::calculate_atr_stops()
        للاتساق مع بقية المشروع.

        الصيغة:
            SL  = Entry − ATR × 1.5        (حد الخسارة)
            TP1 = Entry + ATR × 2.0 (50%)  (الهدف الأول)
            TP2 = Entry + ATR × 4.5 (50%)  (الهدف الثاني)
            R:R = 3.25 / 1.5 = 2.17 ✓

        Args:
            entry_price: سعر الدخول المتوقع
            atr:         قيمة ATR الحالية من المؤشرات

        Returns:
            dict يحتوي على:
                stop_loss, take_profit_1, take_profit_2,
                risk, reward, risk_reward_ratio, valid,
                sl_distance_pct (مسافة SL كنسبة من السعر)
        """
        if entry_price <= 0 or atr <= 0:
            logger.warning(
                f"⚠️ calculate_stops: "
                f"entry_price={entry_price} أو atr={atr} غير صالح"
            )
            return {'valid': False, 'reason': 'entry_price أو ATR صفري/سالب'}

        result = calculate_atr_stops(
            entry_price = entry_price,
            atr         = atr,
            sl_mult     = self.ATR_SL_MULT,
            tp1_mult    = self.ATR_TP1_MULT,
            tp2_mult    = self.ATR_TP2_MULT,
            min_rr      = self.MIN_RR,
        )

        if result.get('valid', False):
            logger.debug(
                f"✓ Stops | Entry=${entry_price:,.2f} | "
                f"SL=${result['stop_loss']:,.2f} | "
                f"TP1=${result['take_profit_1']:,.2f} | "
                f"TP2=${result['take_profit_2']:,.2f} | "
                f"R:R={result['risk_reward_ratio']:.2f}"
            )
        else:
            logger.warning(
                f"⚠️ R:R غير كافٍ: "
                f"{result.get('risk_reward_ratio', 0):.2f} < {self.MIN_RR}"
            )
        return result

    # ═══════════════════════════════════════════════════
    # ② التحقق من R:R
    # ═══════════════════════════════════════════════════

    def validate_risk_reward(
        self,
        entry_price:   float,
        stop_loss:     float,
        take_profit_1: float,
        take_profit_2: float,
    ) -> Tuple[bool, float]:
        """
        التحقق من أن نسبة Risk:Reward تستوفي الحد الأدنى.

        يحسب R:R بناءً على حجم الخروج المقسَّم:
            50% من الصفقة عند TP1
            50% من الصفقة عند TP2

        Args:
            entry_price:   سعر الدخول
            stop_loss:     سعر وقف الخسارة
            take_profit_1: هدف الربح الأول
            take_profit_2: هدف الربح الثاني

        Returns:
            (is_valid, actual_rr_ratio)
                is_valid:      True إذا كان R:R ≥ MIN_RR
                actual_rr_ratio: القيمة الفعلية لـ R:R
        """
        try:
            risk = entry_price - stop_loss
            if risk <= 0:
                logger.warning(
                    f"⚠️ validate_risk_reward: "
                    f"risk ≤ 0 (SL={stop_loss:.2f} ≥ Entry={entry_price:.2f})"
                )
                return False, 0.0

            # الربح المرجَّح: 50% من TP1 + 50% من TP2
            reward = (
                (take_profit_1 - entry_price) * 0.5 +
                (take_profit_2 - entry_price) * 0.5
            )
            rr = reward / risk

            is_valid = rr >= self.MIN_RR

            if not is_valid:
                logger.warning(
                    f"⚠️ R:R غير كافٍ: {rr:.3f} < {self.MIN_RR} | "
                    f"Risk=${risk:.2f} | Reward=${reward:.2f}"
                )
            else:
                logger.debug(f"✓ R:R صالح: {rr:.3f} ≥ {self.MIN_RR}")

            return is_valid, round(rr, 4)

        except Exception as e:
            logger.error(f"❌ خطأ في validate_risk_reward: {e}")
            return False, 0.0

    # ═══════════════════════════════════════════════════
    # ③ حساب حجم الصفقة الديناميكي
    # ═══════════════════════════════════════════════════

    def calculate_position_size(
        self,
        balance:         float,
        entry_price:     float,
        stop_loss_price: float,
        signal_score:    float = 100.0,
        volatility_df=None,
    ) -> Dict:
        """
        حساب حجم الصفقة الديناميكي المُعدَّل بعوامل متعددة.

        العوامل المُعدِّلة للحجم:
        ──────────────────────────
        ① جودة الإشارة (signal_score):
            ≥ 80 → quality_factor = 1.00  (100% — إشارة قوية)
            60-79→ quality_factor = 0.75  ( 75% — إشارة جيدة)
            < 60 → quality_factor = 0.50  ( 50% — احتياطي نادر)

        ② الخسائر المتتالية (consecutive_losses):
            0 → loss_factor = 1.00  (لا تعديل)
            1 → loss_factor = 0.75  (تقليل احترازي)
            2 → loss_factor = 0.50  (تقليل أكبر)
            ≥3→ loss_factor = 0.25  (طوارئ — بعد انتهاء cooldown)

        الصيغة النهائية:
            effective_risk    = balance × 2% × quality_factor × loss_factor
            stop_distance_pct = (entry - SL) / entry
            position_notional = effective_risk / stop_distance_pct
            leverage          = min(notional/balance, max_leverage × lev_factor)
            contract_size     = position_notional / entry_price

        Args:
            balance:         الرصيد المتاح (USDT)
            entry_price:     سعر الدخول
            stop_loss_price: سعر وقف الخسارة
            signal_score:    درجة الإشارة من strategy.calculate_signal_score()
            volatility_df:   DataFrame اختياري لضبط الرافعة حسب التقلب

        Returns:
            dict شامل بجميع معاملات الحجم، أو {} عند الفشل
        """
        try:
            # ── التحقق الأولي من المدخلات ────────────────
            if balance <= 0:
                logger.warning("⚠️ calculate_position_size: balance ≤ 0")
                return {}
            if entry_price <= 0 or stop_loss_price <= 0:
                logger.warning("⚠️ calculate_position_size: أسعار غير صالحة")
                return {}
            if stop_loss_price >= entry_price:
                logger.warning(
                    f"⚠️ SL ({stop_loss_price:.4f}) ≥ Entry ({entry_price:.4f})"
                )
                return {}

            # ── ① معامل جودة الإشارة ─────────────────────
            if   signal_score >= 80:  quality_factor = 1.00
            elif signal_score >= 60:  quality_factor = 0.75
            else:                     quality_factor = 0.50

            # ── ② معامل الخسائر المتتالية ─────────────────
            loss_key     = min(self.consecutive_losses, 3)
            loss_factor  = self.LOSS_SIZE_FACTORS.get(loss_key, 0.25)

            # ── الحساب الأساسي ────────────────────────────
            base_risk         = balance * self.RISK_PCT
            effective_risk    = base_risk * quality_factor * loss_factor
            stop_distance     = entry_price - stop_loss_price
            stop_distance_pct = stop_distance / entry_price

            if stop_distance_pct <= 0:
                logger.warning("⚠️ stop_distance_pct ≤ 0 — لا يمكن حساب الحجم")
                return {}

            position_notional = effective_risk / stop_distance_pct

            # ── ③ إدارة الرافعة ────────────────────────────
            leverage = self._calculate_leverage(
                position_notional = position_notional,
                balance           = balance,
                volatility_df     = volatility_df,
            )

            # ── إعادة حساب الـ notional بعد تطبيق الرافعة ─
            max_notional  = balance * leverage
            if position_notional > max_notional:
                position_notional = max_notional

            contract_size = position_notional / entry_price

            # ── ملخص التعديلات ────────────────────────────
            total_factor = quality_factor * loss_factor

            logger.info(
                f"📐 حجم الصفقة | "
                f"Notional=${position_notional:,.2f} | "
                f"Contracts={contract_size:.6f} | "
                f"Lev={leverage:.1f}x | "
                f"QualityF={quality_factor:.2f} | "
                f"LossF={loss_factor:.2f} | "
                f"Score={signal_score:.0f}"
            )

            return {
                # الحجم
                'contract_size':     round(contract_size,     6),
                'position_notional': round(position_notional, 4),
                'leverage':          round(leverage,           2),

                # المخاطرة
                'base_risk':         round(base_risk,          4),
                'effective_risk':    round(effective_risk,     4),
                'risk_pct':          round(self.RISK_PCT * 100, 2),
                'effective_risk_pct':round(self.RISK_PCT * 100 * total_factor, 2),

                # المسافات
                'stop_distance':     round(stop_distance,      4),
                'stop_distance_pct': round(stop_distance_pct * 100, 4),

                # معاملات التعديل
                'quality_factor':    quality_factor,
                'loss_factor':       loss_factor,
                'total_factor':      round(total_factor, 2),
                'signal_score':      signal_score,
                'consecutive_losses':self.consecutive_losses,
            }

        except Exception as e:
            logger.error(f"❌ خطأ في calculate_position_size: {e}")
            return {}

    # ═══════════════════════════════════════════════════
    # ④ إدارة الرافعة المالية
    # ═══════════════════════════════════════════════════

    def _calculate_leverage(
        self,
        position_notional: float,
        balance:           float,
        volatility_df=None,
    ) -> float:
        """
        تحديد الرافعة المناسبة مع مراعاة نظام التقلب.

        المنطق:
            raw_leverage = position_notional / balance
            lev_factor   = من LEVERAGE_FACTORS حسب نظام التقلب
            final_lev    = min(raw_leverage, MAX_LEVERAGE × lev_factor)

        تعديلات نظام التقلب:
            high:        أقصى = MAX_LEVERAGE × 0.60 = 6x
            normal:      أقصى = MAX_LEVERAGE × 1.00 = 10x
            low:         أقصى = MAX_LEVERAGE × 0.80 = 8x
            contracting: أقصى = MAX_LEVERAGE × 0.50 = 5x

        Args:
            position_notional: حجم الصفقة بالـ USDT
            balance:           الرصيد المتاح
            volatility_df:     DataFrame اختياري لاستخراج نظام التقلب

        Returns:
            الرافعة النهائية (float, 1.0 ≤ lev ≤ MAX_LEVERAGE)
        """
        # الرافعة الخام المطلوبة من الحجم
        raw_leverage = position_notional / balance if balance > 0 else 1.0

        # معامل الرافعة حسب نظام التقلب
        lev_factor = 1.0  # الافتراضي: كامل
        if volatility_df is not None:
            try:
                regime     = get_volatility_regime(volatility_df)
                lev_factor = self.LEVERAGE_FACTORS.get(regime, 1.0)
                if lev_factor < 1.0:
                    logger.debug(
                        f"🔧 تقليل رافعة: نظام التقلب '{regime}' "
                        f"→ factor={lev_factor:.2f}"
                    )
            except Exception:
                pass  # استخدم الافتراضي عند الفشل

        max_allowed = self.MAX_LEVERAGE * lev_factor
        leverage    = min(raw_leverage, max_allowed)
        leverage    = max(1.0, leverage)

        return round(leverage, 2)

    def get_max_allowed_leverage(self, volatility_df=None) -> float:
        """
        الحصول على أقصى رافعة مسموح بها في الظروف الحالية.

        Args:
            volatility_df: DataFrame اختياري لنظام التقلب

        Returns:
            أقصى رافعة مسموح بها (float)
        """
        if volatility_df is None:
            return float(self.MAX_LEVERAGE)

        try:
            regime     = get_volatility_regime(volatility_df)
            lev_factor = self.LEVERAGE_FACTORS.get(regime, 1.0)
            return round(self.MAX_LEVERAGE * lev_factor, 1)
        except Exception:
            return float(self.MAX_LEVERAGE)

    # ═══════════════════════════════════════════════════
    # ⑤ التحقق من إذن التداول
    # ═══════════════════════════════════════════════════

    def is_trading_allowed(self, current_balance: float = None) -> Tuple[bool, str]:
        """
        التحقق الشامل من أن التداول مسموح به في الوقت الحالي.

        الأسباب التي تمنع التداول:
            1. الـ Cooldown بعد خسائر متتالية
            2. تجاوز حد الخسارة اليومية
            3. [مستقبلاً] حد الصفقات المفتوحة

        Args:
            current_balance: الرصيد الحالي (اختياري لحساب الخسارة اليومية)

        Returns:
            (allowed, reason)
                allowed: True إذا كان التداول مسموحاً
                reason:  وصف السبب في كلا الحالتين
        """
        self._reset_daily_if_new_day()

        # ── التحقق من الـ Cooldown ──────────────────────
        if self.is_in_cooldown and self.cooldown_started_at:
            elapsed   = datetime.utcnow() - self.cooldown_started_at
            remaining = timedelta(minutes=self.COOLDOWN_MIN) - elapsed

            if remaining.total_seconds() > 0:
                mins = int(remaining.total_seconds() / 60)
                secs = int(remaining.total_seconds() % 60)
                reason = (
                    f"🔴 Cooldown نشط | "
                    f"متبقٍ: {mins}د {secs}ث | "
                    f"بسبب {self.MAX_CONSEC_LOSS} خسائر متتالية"
                )
                logger.warning(reason)
                return False, reason

            # انتهى الـ Cooldown
            self._exit_cooldown()

        # ── التحقق من حد الخسارة اليومية ───────────────
        if current_balance is not None and self.daily_start_balance > 0:
            daily_loss_pct = (
                self.daily_start_balance - current_balance
            ) / self.daily_start_balance

            if daily_loss_pct >= self.MAX_DAILY_LOSS:
                reason = (
                    f"🔴 حد الخسارة اليومية مُكتمَل | "
                    f"الخسارة: {daily_loss_pct*100:.2f}% ≥ "
                    f"الحد: {self.MAX_DAILY_LOSS*100:.1f}% | "
                    f"توقف حتى اليوم التالي"
                )
                logger.warning(reason)
                return False, reason

        # إضافة: حساب من سجل اليوم إذا لم يُعطَ الرصيد
        if current_balance is None and self.daily_pnl < 0:
            if (self.daily_start_balance > 0 and
                    abs(self.daily_pnl) / self.daily_start_balance >= self.MAX_DAILY_LOSS):
                reason = (
                    f"🔴 حد الخسارة اليومية (PnL) | "
                    f"PnL=${self.daily_pnl:.2f}"
                )
                return False, reason

        # ── كل شيء مقبول ─────────────────────────────────
        reason = (
            f"✅ التداول مسموح | "
            f"خسائر متتالية: {self.consecutive_losses}/{self.MAX_CONSEC_LOSS} | "
            f"PnL اليوم: ${self.daily_pnl:+.2f}"
        )
        return True, reason

    # ═══════════════════════════════════════════════════
    # ⑥ تسجيل نتيجة الصفقة
    # ═══════════════════════════════════════════════════

    def register_trade_result(
        self,
        symbol:    str,
        pnl:       float,
        pnl_pct:   float,
        exit_type: str,
        extra:     Dict = None,
    ) -> Dict:
        """
        تسجيل نتيجة صفقة منتهية وتحديث الحالة الداخلية.

        يجب استدعاء هذه الدالة بعد كل صفقة مُغلَقة
        (سواء كانت ربحاً أو خسارة) لتحديث:
            - عداد الخسائر المتتالية
            - حالة الـ Cooldown
            - إجمالي PnL اليوم
            - سجل نتائج اليوم

        Args:
            symbol:    الزوج المتداول (مثل 'ETH/USDT')
            pnl:       الربح/الخسارة بالـ USDT
            pnl_pct:   الربح/الخسارة كنسبة مئوية
            exit_type: نوع الخروج ('STOP_LOSS' | 'TAKE_PROFIT_1' | 'TAKE_PROFIT_2')
            extra:     بيانات إضافية اختيارية (entry, exit_price, إلخ)

        Returns:
            dict يحتوي على حالة RiskManager بعد التحديث
        """
        is_win = pnl > 0
        self.total_trades   += 1
        self.daily_pnl      += pnl
        self.winning_trades += (1 if is_win else 0)

        record = {
            'symbol':      symbol,
            'pnl':         pnl,
            'pnl_pct':     pnl_pct,
            'exit_type':   exit_type,
            'is_win':      is_win,
            'timestamp':   datetime.utcnow().isoformat(),
            **(extra or {}),
        }
        self.daily_trades.append(record)
        self.trade_history.append(record)

        # ── تحديث عداد الخسائر المتتالية ────────────────
        if is_win:
            if self.consecutive_losses > 0:
                logger.success(
                    f"✅ انتهت سلسلة الخسائر | "
                    f"كانت {self.consecutive_losses} خسائر متتالية | "
                    f"P&L=${pnl:+.2f}"
                )
            self.consecutive_losses = 0
        else:
            self.consecutive_losses += 1
            logger.warning(
                f"⚠️ خسارة متتالية #{self.consecutive_losses} | "
                f"P&L=${pnl:.2f} ({pnl_pct:.2f}%) | {exit_type}"
            )

            # تحقق من تفعيل الـ Cooldown
            if self.consecutive_losses >= self.MAX_CONSEC_LOSS:
                self._enter_cooldown()

        # ── تقرير الحالة ─────────────────────────────────
        state_after = {
            'consecutive_losses': self.consecutive_losses,
            'is_in_cooldown':     self.is_in_cooldown,
            'daily_pnl':          self.daily_pnl,
            'total_trades':       self.total_trades,
            'win_rate':           self._current_win_rate(),
        }

        if is_win:
            logger.success(
                f"✅ صفقة مربحة | {symbol} | "
                f"P&L=${pnl:+.2f} ({pnl_pct:+.2f}%) | "
                f"{exit_type} | PnL يومي=${self.daily_pnl:+.2f}"
            )
        return state_after

    # ═══════════════════════════════════════════════════
    # ⑦ نقل SL إلى Breakeven بعد TP1
    # ═══════════════════════════════════════════════════

    def should_move_sl_to_breakeven(
        self,
        current_price: float,
        entry_price:   float,
        take_profit_1: float,
        current_sl:    float,
    ) -> Tuple[bool, Optional[float]]:
        """
        التحقق من ضرورة نقل SL إلى نقطة التعادل (Breakeven).

        المنطق:
            إذا وصل السعر إلى TP1 → نقل SL إلى Entry Price
            (لضمان أن الصفقة لن تتحول إلى خسارة)

            يُفعَّل فقط إذا كان config:
                move_sl_to_breakeven_after_tp1: true

        Args:
            current_price: السعر الحالي
            entry_price:   سعر دخول الصفقة
            take_profit_1: هدف الربح الأول
            current_sl:    مستوى SL الحالي

        Returns:
            (should_move, new_sl)
                should_move: True إذا يجب نقل الـ SL
                new_sl:      السعر الجديد للـ SL (= entry_price) أو None
        """
        if not self.MOVE_SL_BREAKEVEN:
            return False, None

        # TP1 تحقق وSL لم يُنقل بعد
        if current_price >= take_profit_1 and current_sl < entry_price:
            logger.info(
                f"🔄 نقل SL إلى Breakeven | "
                f"السعر ${current_price:,.2f} وصل TP1 ${take_profit_1:,.2f} | "
                f"SL الجديد = ${entry_price:,.2f}"
            )
            return True, entry_price

        return False, None

    def should_close_losing_early(
        self,
        current_price: float,
        entry_price:   float,
        stop_loss:     float,
    ) -> Tuple[bool, str]:
        """
        التحقق من الإغلاق المبكر للصفقات الخاسرة.

        يُفعَّل فقط إذا كان config:
            close_losing_early: true
            close_losing_percent: 5.0

        المنطق:
            إذا كانت الصفقة خاسرة بأكثر من close_losing_percent
            من رأس المال المخصص لها → إغلاق مبكر

        Args:
            current_price: السعر الحالي
            entry_price:   سعر الدخول
            stop_loss:     مستوى SL للمرجع

        Returns:
            (should_close, reason)
        """
        if not self.CLOSE_LOSING:
            return False, ''

        loss_pct = (entry_price - current_price) / entry_price

        if loss_pct >= self.CLOSE_LOSING_PCT:
            reason = (
                f"إغلاق مبكر | "
                f"خسارة {loss_pct*100:.2f}% ≥ {self.CLOSE_LOSING_PCT*100:.1f}%"
            )
            logger.warning(f"⚠️ {reason}")
            return True, reason

        return False, ''

    # ═══════════════════════════════════════════════════
    # إدارة الرصيد
    # ═══════════════════════════════════════════════════

    def update_balance(self, new_balance: float):
        """
        تحديث الرصيد المرجعي لحسابات اليوم.

        يجب استدعاؤه عند بداية كل جلسة
        أو بعد إيداع/سحب من الحساب.
        """
        if new_balance > 0:
            old = self.initial_balance
            self.initial_balance = new_balance
            logger.info(
                f"💰 تحديث الرصيد | ${old:,.2f} → ${new_balance:,.2f}"
            )

    def update_daily_start_balance(self, balance: float):
        """تحديث رصيد بداية اليوم لحسابات الخسارة اليومية."""
        if balance > 0:
            self.daily_start_balance = balance
            logger.info(f"📅 رصيد بداية اليوم: ${balance:,.2f}")

    # ═══════════════════════════════════════════════════
    # تقرير شامل لحالة المخاطر
    # ═══════════════════════════════════════════════════

    def get_risk_summary(self, current_balance: float = None) -> Dict:
        """
        تقرير شامل يعكس حالة RiskManager الكاملة.

        يُستخدم لـ:
            - Telegram notifications
            - Database logging
            - Dashboard display
            - Debugging

        Args:
            current_balance: الرصيد الحالي (اختياري)

        Returns:
            dict شامل بحالة المخاطر
        """
        allowed, allow_reason = self.is_trading_allowed(current_balance)

        # حساب الخسارة اليومية
        daily_loss_pct = 0.0
        if self.daily_start_balance > 0 and current_balance:
            daily_loss_pct = (
                (self.daily_start_balance - current_balance)
                / self.daily_start_balance * 100
            )

        # Cooldown المتبقي
        cooldown_remaining_secs = 0
        if self.is_in_cooldown and self.cooldown_started_at:
            elapsed   = datetime.utcnow() - self.cooldown_started_at
            remaining = timedelta(minutes=self.COOLDOWN_MIN) - elapsed
            cooldown_remaining_secs = max(0, int(remaining.total_seconds()))

        summary = {
            # الحالة العامة
            'trading_allowed':  allowed,
            'allow_reason':     allow_reason,

            # الخسائر المتتالية
            'consecutive_losses':    self.consecutive_losses,
            'max_consecutive_losses': self.MAX_CONSEC_LOSS,
            'is_in_cooldown':        self.is_in_cooldown,
            'cooldown_remaining_sec':cooldown_remaining_secs,

            # إحصائيات اليوم
            'daily_pnl':          round(self.daily_pnl, 4),
            'daily_trades_count': len(self.daily_trades),
            'daily_loss_pct':     round(daily_loss_pct, 2),
            'max_daily_loss_pct': self.MAX_DAILY_LOSS * 100,

            # إحصائيات كاملة
            'total_trades':       self.total_trades,
            'winning_trades':     self.winning_trades,
            'losing_trades':      self.total_trades - self.winning_trades,
            'win_rate':           round(self._current_win_rate(), 1),

            # الإعدادات الفعلية
            'config': {
                'risk_pct':       self.RISK_PCT * 100,
                'max_leverage':   self.MAX_LEVERAGE,
                'min_rr':         self.MIN_RR,
                'atr_sl_mult':    self.ATR_SL_MULT,
                'atr_tp1_mult':   self.ATR_TP1_MULT,
                'atr_tp2_mult':   self.ATR_TP2_MULT,
                'cooldown_min':   self.COOLDOWN_MIN,
                'move_sl_be':     self.MOVE_SL_BREAKEVEN,
            },
        }
        return summary

    def print_risk_summary(self, current_balance: float = None):
        """طباعة تقرير المخاطر بشكل منسق."""
        s = self.get_risk_summary(current_balance)

        print("\n" + "═" * 65)
        print("🛡️  تقرير إدارة المخاطر — RiskManager")
        print("═" * 65)

        # حالة التداول
        allow_icon = "✅" if s['trading_allowed'] else "🔴"
        print(f"\n  {allow_icon} {s['allow_reason']}")

        # الخسائر المتتالية
        print(f"\n  الخسائر المتتالية: "
              f"{s['consecutive_losses']}/{s['max_consecutive_losses']}")
        if s['is_in_cooldown']:
            rem = s['cooldown_remaining_sec']
            print(f"  ⏳ Cooldown: {rem//60}د {rem%60}ث متبقية")

        # إحصائيات اليوم
        print(f"\n  📅 إحصائيات اليوم:")
        print(f"     PnL:       ${s['daily_pnl']:+.2f}")
        print(f"     صفقات:     {s['daily_trades_count']}")
        pnl_pct = s['daily_loss_pct']
        max_pct  = s['max_daily_loss_pct']
        print(f"     خسارة:     {abs(pnl_pct):.2f}% / {max_pct:.1f}%")

        # الإحصائيات الكاملة
        print(f"\n  📊 الإجمالي:")
        print(f"     صفقات:     {s['total_trades']}")
        print(f"     رابحة:     {s['winning_trades']}")
        print(f"     خاسرة:     {s['losing_trades']}")
        print(f"     Win Rate:  {s['win_rate']:.1f}%")

        # الإعدادات
        cfg = s['config']
        print(f"\n  ⚙️  الإعدادات الفعلية:")
        print(f"     Risk/Trade:  {cfg['risk_pct']:.1f}%")
        print(f"     MaxLev:      {cfg['max_leverage']}x")
        print(f"     Min R:R:     {cfg['min_rr']}")
        print(f"     SL/TP:       ×{cfg['atr_sl_mult']}/×{cfg['atr_tp1_mult']}/×{cfg['atr_tp2_mult']}")

        print("═" * 65)

    # ═══════════════════════════════════════════════════
    # دوال داخلية مساعدة
    # ═══════════════════════════════════════════════════

    def _enter_cooldown(self):
        """تفعيل فترة التوقف الإجباري."""
        self.is_in_cooldown       = True
        self.cooldown_started_at  = datetime.utcnow()
        until = self.cooldown_started_at + timedelta(minutes=self.COOLDOWN_MIN)
        logger.warning(
            f"🔴 تفعيل Cooldown | "
            f"{self.consecutive_losses} خسائر متتالية | "
            f"التداول معلَّق حتى: {until.strftime('%H:%M:%S')} UTC"
        )

    def _exit_cooldown(self):
        """إنهاء فترة التوقف الإجباري."""
        self.is_in_cooldown      = False
        self.cooldown_started_at = None
        self.consecutive_losses  = 0   # إعادة تعيين العداد بعد انتهاء الـ cooldown
        logger.success(
            "✅ انتهى Cooldown — التداول مسموح مجدداً | "
            "حجم الصفقة مخفض (25%) لأول صفقة بعد الـ Cooldown"
        )

    def _reset_daily_if_new_day(self):
        """إعادة تعيين إحصائيات اليوم عند بداية يوم جديد (UTC)."""
        today = datetime.utcnow().strftime('%Y-%m-%d')
        if self.daily_reset_date != today:
            if self.daily_reset_date is not None:
                logger.info(
                    f"📅 يوم جديد ({today}) | "
                    f"إعادة تعيين إحصائيات اليوم | "
                    f"PnL الأمس: ${self.daily_pnl:+.2f}"
                )
            self.daily_pnl        = 0.0
            self.daily_trades     = []
            self.daily_reset_date = today
            # تحديث رصيد بداية اليوم إذا كان initial_balance معروفاً
            if self.initial_balance > 0:
                self.daily_start_balance = self.initial_balance

    def _current_win_rate(self) -> float:
        """حساب نسبة الربح الحالية."""
        if self.total_trades == 0:
            return 0.0
        return (self.winning_trades / self.total_trades) * 100

    def reset_consecutive_losses(self):
        """إعادة تعيين عداد الخسائر المتتالية يدوياً (للاختبار أو التدخل اليدوي)."""
        self.consecutive_losses = 0
        self.is_in_cooldown     = False
        self.cooldown_started_at = None
        logger.info("🔄 تمت إعادة تعيين عداد الخسائر المتتالية يدوياً")


# ─────────────────────────────────────────────────────────
# اختبار سريع عند التشغيل المباشر
# ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    rm = RiskManager(initial_balance=10_000.0)

    # ── اختبار calculate_stops ───────────────────────
    stops = rm.calculate_stops(entry_price=43_250.0, atr=150.0)
    print(f"\n🧮 Stops:")
    print(f"   SL  = ${stops.get('stop_loss',      0):,.2f}")
    print(f"   TP1 = ${stops.get('take_profit_1',  0):,.2f}")
    print(f"   TP2 = ${stops.get('take_profit_2',  0):,.2f}")
    print(f"   R:R = {stops.get('risk_reward_ratio',0):.3f}:1  "
          f"{'✅' if stops.get('valid') else '❌'}")

    # ── اختبار calculate_position_size ───────────────
    size = rm.calculate_position_size(
        balance         = 10_000.0,
        entry_price     = 43_250.0,
        stop_loss_price = stops.get('stop_loss', 43_025.0),
        signal_score    = 85.0,
    )
    print(f"\n📐 Position Size (score=85):")
    print(f"   Contracts  = {size.get('contract_size',     0):.6f}")
    print(f"   Notional   = ${size.get('position_notional',0):,.2f}")
    print(f"   Leverage   = {size.get('leverage',          0):.1f}x")
    print(f"   Risk       = ${size.get('effective_risk',   0):.2f} "
          f"({size.get('effective_risk_pct', 0):.2f}%)")

    # ── اختبار is_trading_allowed ────────────────────
    allowed, reason = rm.is_trading_allowed(current_balance=10_000.0)
    print(f"\n🚦 التداول مسموح؟ {'✅ نعم' if allowed else '❌ لا'}")
    print(f"   {reason}")

    # ── اختبار register_trade_result ─────────────────
    rm.register_trade_result('BTC/USDT', pnl=-120.0, pnl_pct=-1.2, exit_type='STOP_LOSS')
    rm.register_trade_result('BTC/USDT', pnl=-95.0,  pnl_pct=-0.9, exit_type='STOP_LOSS')
    rm.register_trade_result('BTC/USDT', pnl=-110.0, pnl_pct=-1.1, exit_type='STOP_LOSS')

    allowed, reason = rm.is_trading_allowed(current_balance=9_675.0)
    print(f"\n🚦 بعد 3 خسائر — مسموح؟ {'✅ نعم' if allowed else '❌ لا'}")
    print(f"   {reason}")

    rm.print_risk_summary(current_balance=9_675.0)
