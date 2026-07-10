"""
═══════════════════════════════════════════════════════════
استراتيجية التداول الأساسية — TradingStrategy v1.2
═══════════════════════════════════════════════════════════
Multi-Timeframe Trend Following + Momentum

الأطر الزمنية:
    1H  → الاتجاه الرئيسي  (trend bias)
    15M → الإشارة والتأكيد (entry signal)
    5M  → تفاصيل إضافية   (optional confirmation)

═══════════════════════════════════════════════════════════
سجل التغييرات
═══════════════════════════════════════════════════════════
v1.0 — النسخة الأصلية

v1.1 — إصلاح الأخطاء (Bug Fix):
    ADX threshold:   20  → 25      (توافق STRATEGY_COMPLETE_GUIDE)
    RSI range:    45-70  → 50-70   (توافق STRATEGY_COMPLETE_GUIDE)
    EMA distance:   3.0% → 2.0%   (توافق STRATEGY_COMPLETE_GUIDE)
    TP2 multiplier: 3.5  → 4.5    (إصلاح R:R: 1.83 → 2.17 ✓)
        R:R = (ATR×2.0×0.5 + ATR×4.5×0.5) / ATR×1.5
            = 3.25 / 1.5 = 2.17 ✓

v1.2 — ميزات جديدة (Feature Update):
    + تكامل كامل مع indicators/ (trend, momentum, volatility)
      بدلاً من تكرار المنطق داخل هذا الملف
    + calculate_signal_score(): درجة 0-100 موضوعية
      (40% trend + 40% momentum + 20% volatility)
    + validate_signal(): تحقق من جودة الإشارة (منفصل عن validate_trade)
    + _analyze_multi_timeframe(): تحليل منظم لتوافق الأطر الزمنية
    + get_signal_breakdown(): تقرير شامل يجمع gates + score + MTF
    + calculate_position_size() يأخذ signal_score لتحديد حجم الصفقة
    + print_score_report(): طباعة تقرير الدرجة منسقاً
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Tuple, Optional
from datetime import datetime

from utils.logger import logger

# ─────────────────────────────────────────────────────────
# استيراد وحدات المؤشرات (بدلاً من تكرار المنطق هنا)
# ─────────────────────────────────────────────────────────

# الاتجاه — trend.py
from indicators.trend import (
    get_trend_score,
    get_trend_strength_label,
    analyze_multi_timeframe_trend,
    get_price_distance_from_ema,
    detect_trend_direction,
    is_ema_aligned_bullish,
)

# الزخم — momentum.py
from indicators.momentum import (
    get_momentum_score,
    get_momentum_summary,
    get_rsi_zone,
    detect_macd_crossover,
    get_rsi_quality_score,
    get_macd_quality_score,
)

# التقلب — volatility.py
from indicators.volatility import (
    get_volatility_score,
    get_volatility_summary,
    get_volatility_regime,
    get_volatility_regime_label,
    calculate_atr_stops,
)


# ─────────────────────────────────────────────────────────
# الفئة الرئيسية
# ─────────────────────────────────────────────────────────

class TradingStrategy:
    """
    استراتيجية Trend Following + Momentum

    ══════════════════════════════════════════
    معمارية القرار (Decision Architecture):
    ══════════════════════════════════════════

    المستوى 1 — check_buy_signal()  [Hard Gates]
    ─────────────────────────────────────────────
    6 شروط ثنائية يجب أن تتحقق جميعها:
        1. السعر > EMA200        (1H)
        2. EMA50 > EMA200        (1H)  — Golden Cross
        3. ADX > 25              (15M) — اتجاه قوي
        4. Volume > SMA20        (15M) — تأكيد الحجم
        5. RSI بين 50-70         (15M) — منطقة الزخم
        6. MACD > Signal         (15M) — زخم صاعد
        [+] السعر ضمن 2% من EMA21 (15M) — جودة الدخول

    المستوى 2 — validate_signal()  [Signal Quality Gate]
    ───────────────────────────────────────────────────────
    تحقق من جودة الإشارة بعد اجتياز الشروط الستة:
        • score ≥ SCORE_MIN_TRADE (60)
        • توافق الأطر الزمنية
        • ATR كافٍ (لا يساوي صفراً)
        • Volatility regime مناسب

    المستوى 3 — calculate_signal_score()  [Quality Metric]
    ─────────────────────────────────────────────────────────
    درجة موضوعية 0-100:
        40% ← get_trend_score(df_1h)          [indicators/trend.py]
        40% ← get_momentum_score(df_15m)      [indicators/momentum.py]
        20% ← get_volatility_score(df_15m)    [indicators/volatility.py]

    المستوى 4 — validate_trade()  [Execution Safety Gate]
    ────────────────────────────────────────────────────────
    تحقق من صحة معاملات التنفيذ (منفصل تماماً عن validate_signal):
        • الحقول المطلوبة موجودة
        • الأسعار منطقية
        • SL < Entry < TP
    """

    # ══ ثوابت SL/TP (بوحدات ATR) ══════════════════════
    ATR_SL_MULT   = 1.5   # SL  = Entry - ATR×1.5
    ATR_TP1_MULT  = 2.0   # TP1 = Entry + ATR×2.0  (50% من الصفقة)
    ATR_TP2_MULT  = 4.5   # TP2 = Entry + ATR×4.5  (50% من الصفقة)
    # R:R = (2.0×0.5 + 4.5×0.5) / 1.5 = 3.25 / 1.5 = 2.17 ✓

    # ══ ثوابت إدارة المخاطر ═════════════════════════════
    MIN_RISK_REWARD = 2.0   # الحد الأدنى المقبول لـ R:R
    RISK_PER_TRADE  = 0.02  # 2% من الرصيد لكل صفقة
    MAX_LEVERAGE    = 10    # أقصى رافعة مسموح بها

    # ══ ثوابت فلترة الإشارات (Hard Gates) ══════════════
    ADX_THRESHOLD    = 25   # ADX > 25 = اتجاه قوي
    RSI_MIN          = 50   # RSI ≥ 50  = في منطقة الصعود
    RSI_MAX          = 70   # RSI ≤ 70  = لم يبلغ ذروة الشراء
    MAX_EMA_DISTANCE = 2.0  # السعر ≤ 2% من EMA21

    # ══ ثوابت نظام التقييم ══════════════════════════════
    WEIGHT_TREND      = 0.40  # وزن درجة الاتجاه
    WEIGHT_MOMENTUM   = 0.40  # وزن درجة الزخم
    WEIGHT_VOLATILITY = 0.20  # وزن درجة التقلب

    SCORE_MIN_TRADE = 60    # أدنى درجة لفتح صفقة
    SCORE_STRONG    = 80    # درجة الإشارة القوية (حجم كامل)

    # ══ حد أدنى لـ ATR (تجنب أسواق الركود الكامل) ═════
    MIN_ATR_RATIO   = 0.001  # ATR يجب ≥ 0.1% من السعر

    def __init__(self):
        self.name    = "Trend Following + Momentum"
        self.version = "1.2"
        logger.success(f"✅ استراتيجية جاهزة: {self.name} v{self.version}")

    # ═══════════════════════════════════════════════════
    # المستوى 1: Hard Gates — الشروط الستة الصارمة
    # ═══════════════════════════════════════════════════

    def check_buy_signal(
        self,
        df_1h:  pd.DataFrame,
        df_15m: pd.DataFrame,
        df_5m:  pd.DataFrame,
    ) -> Tuple[bool, Dict]:
        """
        التحقق من الشروط الستة الصارمة (Binary Hard Gates).

        هذه الشروط ثنائية 100% — لا يوجد "جزئياً":
        إذا فشل أي شرط واحد → لا صفقة، بغض النظر
        عن أي درجة أو مؤشر آخر.

        Args:
            df_1h:  DataFrame الإطار الساعي   (مع مؤشرات مضافة)
            df_15m: DataFrame إطار 15 دقيقة  (مع مؤشرات مضافة)
            df_5m:  DataFrame إطار 5 دقائق   (مع مؤشرات مضافة)

        Returns:
            (True,  signal_data) — إذا اجتازت جميع الشروط
            (False, fail_data)   — إذا فشل أي شرط مع تفاصيل الفشل
        """
        try:
            # ── الحد الأدنى للبيانات ─────────────────────
            for name, df in [('1H', df_1h), ('15M', df_15m), ('5M', df_5m)]:
                if len(df) < 21:
                    return False, {
                        'reason': f'بيانات {name} غير كافية ({len(df)} < 21 شمعة)',
                        'conditions': {},
                    }
                if len(df) < 2:
                    return False, {
                        'reason': f'بيانات {name} ناقصة جداً',
                        'conditions': {},
                    }

            # ── آخر شمعة مكتملة (iloc[-2]) ──────────────
            # iloc[-1] = شمعة مفتوحة لم تكتمل → لا تُحلَّل
            # iloc[-2] = آخر شمعة مكتملة     → هذه نحللها
            c1h  = df_1h.iloc[-2]
            c15m = df_15m.iloc[-2]

            conditions: Dict[str, bool] = {}

            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # 1️⃣  السعر فوق EMA200 (1H) — الاتجاه الرئيسي صاعد
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            ema200_1h = float(c1h.get('ema_slow', 0))
            conditions['1h_price_above_ema200'] = (
                ema200_1h > 0 and float(c1h['close']) > ema200_1h
            )

            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # 2️⃣  EMA50 فوق EMA200 (1H) — Golden Cross
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            ema50_1h  = float(c1h.get('ema_fast', 0))
            conditions['1h_ema50_above_ema200'] = (
                ema200_1h > 0 and ema50_1h > ema200_1h
            )

            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # 3️⃣  ADX > 25 (15M) — اتجاه قوي وليس Sideways
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            adx_val = float(c15m.get('adx', 0))
            conditions['15m_adx_above_25'] = adx_val > self.ADX_THRESHOLD

            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # 4️⃣  Volume > SMA20 (15M) — تأكيد الحجم
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            vol     = float(c15m.get('volume',     0))
            vol_sma = float(c15m.get('volume_sma', 0))
            conditions['15m_volume_above_sma20'] = (
                vol_sma > 0 and vol > vol_sma
            )

            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # 5️⃣  RSI بين 50-70 (15M)
            #     50+: منطقة الزخم الإيجابي
            #     ≤70: لم يبلغ ذروة الشراء بعد
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            rsi_val = float(c15m.get('rsi', 50))
            conditions['15m_rsi_in_range_50_70'] = (
                self.RSI_MIN <= rsi_val <= self.RSI_MAX
            )

            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # 6️⃣  MACD > Signal (15M) — زخم صاعد
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            macd_val   = float(c15m.get('macd',        0))
            signal_val = float(c15m.get('macd_signal', 0))
            conditions['15m_macd_above_signal'] = macd_val > signal_val

            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # [+]  السعر ضمن 2% من EMA21 (15M)
            #      تجنب الدخول إذا كان السعر بعيداً جداً
            #      (إشارة مبالغة في الحركة → خطر تصحيح)
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # نستخدم get_price_distance_from_ema من indicators/trend.py
            # بدلاً من إعادة كتابة المنطق هنا
            ema21_dist = get_price_distance_from_ema(df_15m, 'ema_medium')
            conditions['15m_price_near_ema21'] = (
                ema21_dist <= self.MAX_EMA_DISTANCE
            )

            # ── هل اجتازت جميع الشروط؟ ───────────────────
            failed = [k for k, v in conditions.items() if not v]
            all_passed = len(failed) == 0

            if not all_passed:
                return False, {
                    'reason':     f"شروط فاشلة ({len(failed)}): {', '.join(failed)}",
                    'conditions': conditions,
                    'failed':     failed,
                }

            # ── تجميع بيانات الإشارة ────────────────────
            atr_val = float(c15m.get('atr', 0))
            signal_data = {
                'signal':      'BUY',
                'entry_price': float(c15m['close']),
                'atr':         atr_val,
                'rsi':         rsi_val,
                'adx':         adx_val,
                'macd':        macd_val,
                'macd_signal': signal_val,
                'ema21_dist':  ema21_dist,
                'conditions':  conditions,
                'all_passed':  True,
            }
            return True, signal_data

        except Exception as e:
            logger.error(f"❌ خطأ في check_buy_signal: {e}")
            return False, {'reason': f'استثناء: {e}', 'conditions': {}}

    # ═══════════════════════════════════════════════════
    # المستوى 2: validate_signal() — جودة الإشارة
    # (منفصل تماماً عن validate_trade)
    # ═══════════════════════════════════════════════════

    def validate_signal(
        self,
        df_1h:       pd.DataFrame,
        df_15m:      pd.DataFrame,
        df_5m:       pd.DataFrame,
        signal_data: Dict,
    ) -> Tuple[bool, Dict]:
        """
        التحقق من جودة الإشارة — المستوى الثاني من الفلترة.

        الفرق عن validate_trade():
        ─────────────────────────
        validate_signal()  ← هل السوق مناسب للدخول الآن؟
                             (تحقق من جودة الإشارة والبيئة)

        validate_trade()   ← هل معاملات الصفقة صحيحة للتنفيذ؟
                             (تحقق من صحة الأسعار والأحجام)

        يُستدعى بعد check_buy_signal() وقبل تنفيذ الصفقة.

        الشروط:
            ① score ≥ SCORE_MIN_TRADE (60) — الإشارة قوية كافياً
            ② ATR كافٍ (ليس صفراً أو قريباً من الصفر)
            ③ توافق الأطر الزمنية (1H و 15M كلاهما bullish)
            ④ نظام التقلب مناسب (ليس 'contracting')

        Args:
            df_1h, df_15m, df_5m: DataFrames مع المؤشرات
            signal_data: ناتج check_buy_signal()

        Returns:
            (True,  validation_info) — الإشارة صالحة للمتابعة
            (False, validation_info) — الإشارة مرفوضة مع السبب
        """
        try:
            rejections = []
            info       = {}

            # ── ① درجة الإشارة ───────────────────────────
            score_result = self.calculate_signal_score(df_1h, df_15m, df_5m)
            total_score  = score_result.get('total_score', 0)
            info['score_result'] = score_result

            if total_score < self.SCORE_MIN_TRADE:
                rejections.append(
                    f"درجة الإشارة {total_score:.1f} < الحد الأدنى {self.SCORE_MIN_TRADE}"
                )

            # ── ② ATR كافٍ (تجنب أسواق الركود الكامل) ────
            entry_price = float(signal_data.get('entry_price', 0))
            atr_val     = float(signal_data.get('atr', 0))
            atr_ratio   = atr_val / entry_price if entry_price > 0 else 0

            info['atr']       = atr_val
            info['atr_ratio'] = atr_ratio

            if atr_ratio < self.MIN_ATR_RATIO:
                rejections.append(
                    f"ATR ({atr_val:.4f}) أقل من الحد الأدنى "
                    f"({self.MIN_ATR_RATIO*100:.2f}% من السعر)"
                )

            # ── ③ توافق الأطر الزمنية ─────────────────────
            # نستخدم analyze_multi_timeframe_trend من indicators/trend.py
            mtf = analyze_multi_timeframe_trend(df_1h, df_15m)
            info['mtf'] = mtf

            if not mtf.get('aligned', False):
                rejections.append(
                    f"الأطر الزمنية غير متوافقة: "
                    f"1H={mtf.get('trend_1h','?')} / "
                    f"15M={mtf.get('trend_15m','?')}"
                )

            # ── ④ نظام التقلب مناسب ───────────────────────
            # نستخدم get_volatility_regime من indicators/volatility.py
            regime = get_volatility_regime(df_15m)
            info['volatility_regime'] = regime

            if regime == 'contracting':
                rejections.append(
                    "نظام التقلب: contracting — "
                    "السوق في ركود شبه كامل (ATR متناقص بشدة)"
                )

            # ── النتيجة ─────────────────────────────────────
            is_valid = len(rejections) == 0
            info['rejections'] = rejections
            info['is_valid']   = is_valid

            if is_valid:
                rec = score_result.get('recommendation', 'good')
                info['decision'] = (
                    f"✅ إشارة صالحة — Score={total_score:.1f} | "
                    f"توصية: {rec} | "
                    f"توافق الأطر: {'✅' if mtf.get('aligned') else '⚠️'}"
                )
            else:
                info['decision'] = (
                    f"❌ إشارة مرفوضة ({len(rejections)} سبب)"
                )

            return is_valid, info

        except Exception as e:
            logger.error(f"❌ خطأ في validate_signal: {e}")
            return False, {'rejections': [f'استثناء: {e}'], 'is_valid': False}

    # ═══════════════════════════════════════════════════
    # المستوى 3: calculate_signal_score() — التقييم 0-100
    # ═══════════════════════════════════════════════════

    def calculate_signal_score(
        self,
        df_1h:  pd.DataFrame,
        df_15m: pd.DataFrame,
        df_5m:  pd.DataFrame,
    ) -> Dict:
        """
        حساب درجة جودة الإشارة الموضوعية (0-100).

        يستخدم indicators/ مباشرةً بدلاً من تكرار المنطق:

            trend_score     = get_trend_score(df_1h)       [indicators/trend.py]
            momentum_score  = get_momentum_score(df_15m)   [indicators/momentum.py]
            volatility_score= get_volatility_score(df_15m) [indicators/volatility.py]

        الأوزان:
            40% × trend_score
            40% × momentum_score
            20% × volatility_score
            ─────────────────────
            = total_score (0-100)

        الدرجة → القرار → الحجم:
            ≥ 80 → strong  → 100% من الحجم المحسوب
            60-79→ good    →  75% من الحجم المحسوب
            < 60 → weak    →  skip (لا صفقة بعد validate_signal)

        Args:
            df_1h:  بيانات الإطار الساعي  (مع مؤشرات مُضافة)
            df_15m: بيانات إطار 15 دقيقة (مع مؤشرات مُضافة)
            df_5m:  بيانات إطار 5 دقائق  (غير مستخدم حالياً — للتوسع)

        Returns:
            dict يحتوي على:
                total_score:      الدرجة الإجمالية (0-100)
                trend_score:      درجة الاتجاه     (0-100 قبل الوزن)
                momentum_score:   درجة الزخم       (0-100 قبل الوزن)
                volatility_score: درجة التقلب      (0-100 قبل الوزن)
                weighted:         dict الدرجات بعد الوزن
                recommendation:   'strong' | 'good' | 'weak'
                mtf_analysis:     تحليل متعدد الأطر (من _analyze_multi_timeframe)
        """
        try:
            # ── الدرجات الخام من وحدات المؤشرات ───────────
            raw_trend      = get_trend_score(df_1h)
            raw_momentum   = get_momentum_score(df_15m)
            raw_volatility = get_volatility_score(df_15m)

            # ── الدرجات بعد تطبيق الأوزان ──────────────────
            w_trend      = raw_trend      * self.WEIGHT_TREND
            w_momentum   = raw_momentum   * self.WEIGHT_MOMENTUM
            w_volatility = raw_volatility * self.WEIGHT_VOLATILITY

            total = round(min(w_trend + w_momentum + w_volatility, 100.0), 1)

            # ── التحليل متعدد الأطر (helper مخصص) ─────────
            mtf = self._analyze_multi_timeframe(df_1h, df_15m)

            # ── التوصية ──────────────────────────────────────
            if total >= self.SCORE_STRONG:
                recommendation = 'strong'
            elif total >= self.SCORE_MIN_TRADE:
                recommendation = 'good'
            else:
                recommendation = 'weak'

            return {
                # الدرجات الخام
                'trend_score':      round(raw_trend,      1),
                'momentum_score':   round(raw_momentum,   1),
                'volatility_score': round(raw_volatility, 1),

                # الدرجات بعد الوزن
                'weighted': {
                    'trend':      round(w_trend,      1),
                    'momentum':   round(w_momentum,   1),
                    'volatility': round(w_volatility, 1),
                },

                # النتيجة
                'total_score':    total,
                'recommendation': recommendation,

                # التحليل متعدد الأطر
                'mtf_analysis': mtf,
            }

        except Exception as e:
            logger.error(f"❌ خطأ في calculate_signal_score: {e}")
            return {
                'total_score':    0.0,
                'recommendation': 'weak',
                'trend_score':    0.0,
                'momentum_score': 0.0,
                'volatility_score': 0.0,
                'weighted': {'trend': 0, 'momentum': 0, 'volatility': 0},
                'mtf_analysis': {},
                'error': str(e),
            }

    # ═══════════════════════════════════════════════════
    # التحليل متعدد الأطر الزمنية (helper داخلي)
    # ═══════════════════════════════════════════════════

    def _analyze_multi_timeframe(
        self,
        df_1h:  pd.DataFrame,
        df_15m: pd.DataFrame,
    ) -> Dict:
        """
        تحليل منظم لتوافق الأطر الزمنية.

        يربط بين:
            1H  → الاتجاه الرئيسي (هل السوق في uptrend؟)
            15M → توقيت الدخول   (هل الإشارة مع الاتجاه؟)

        القاعدة الذهبية:
            كلا الإطارين bullish → توافق كامل → أعلى احتمال نجاح

        يستخدم دوال indicators/trend.py بدلاً من تكرار المنطق:
            detect_trend_direction()
            is_ema_aligned_bullish()
            analyze_multi_timeframe_trend()
            get_trend_strength_label()
            detect_macd_crossover()  [من momentum.py]

        Returns:
            dict شامل لتحليل الأطر الزمنية
        """
        try:
            # ── توافق الإطارين (من indicators/trend.py) ────
            mtf_base = analyze_multi_timeframe_trend(df_1h, df_15m)

            # ── بيانات إضافية من 1H ─────────────────────
            last_1h  = df_1h.iloc[-2]  if len(df_1h)  >= 2 else df_1h.iloc[-1]
            adx_1h   = float(last_1h.get('adx', 0))
            rsi_1h   = float(last_1h.get('rsi', 50))

            # ── بيانات إضافية من 15M ────────────────────
            last_15m = df_15m.iloc[-2] if len(df_15m) >= 2 else df_15m.iloc[-1]
            adx_15m  = float(last_15m.get('adx',  0))
            rsi_15m  = float(last_15m.get('rsi',  50))
            atr_15m  = float(last_15m.get('atr',  0))

            # ── كشف MACD Crossover على 15M ─────────────
            crossover = detect_macd_crossover(df_15m)

            # ── منطقة RSI ───────────────────────────────
            rsi_zone_1h  = get_rsi_zone(rsi_1h)
            rsi_zone_15m = get_rsi_zone(rsi_15m)

            # ── قوة الاتجاه (وصفية) على الإطارين ─────────
            strength_1h  = get_trend_strength_label(adx_1h)
            strength_15m = get_trend_strength_label(adx_15m)

            # ── EMA alignment الكامل (ema21>ema50>ema200) ─
            ema_full_1h  = is_ema_aligned_bullish(df_1h)
            ema_full_15m = is_ema_aligned_bullish(df_15m)

            # ── نظام التقلب ─────────────────────────────
            regime = get_volatility_regime(df_15m)

            # ── تقييم قوة التوافق الكلية ────────────────
            alignment_quality = self._rate_alignment_quality(
                aligned      = mtf_base['aligned'],
                ema_1h       = ema_full_1h,
                ema_15m      = ema_full_15m,
                crossover    = crossover,
                adx_1h       = adx_1h,
                adx_15m      = adx_15m,
            )

            return {
                # من analyze_multi_timeframe_trend
                'trend_1h':          mtf_base['trend_1h'],
                'trend_15m':         mtf_base['trend_15m'],
                'aligned':           mtf_base['aligned'],
                'alignment_score':   mtf_base['alignment_score'],

                # EMA alignment
                'ema_aligned_1h':    ema_full_1h,
                'ema_aligned_15m':   ema_full_15m,

                # مؤشرات نقطة الدخول
                'adx_1h':            adx_1h,
                'adx_15m':           adx_15m,
                'rsi_1h':            rsi_1h,
                'rsi_15m':           rsi_15m,
                'rsi_zone_1h':       rsi_zone_1h,
                'rsi_zone_15m':      rsi_zone_15m,
                'atr_15m':           atr_15m,
                'macd_crossover':    crossover,

                # تقييمات وصفية
                'trend_strength_1h':  strength_1h,
                'trend_strength_15m': strength_15m,
                'volatility_regime':  get_volatility_regime_label(regime),

                # تقييم إجمالي للتوافق
                'alignment_quality': alignment_quality,
            }

        except Exception as e:
            logger.error(f"❌ خطأ في _analyze_multi_timeframe: {e}")
            return {'aligned': False, 'error': str(e)}

    @staticmethod
    def _rate_alignment_quality(
        aligned:   bool,
        ema_1h:    bool,
        ema_15m:   bool,
        crossover: str,
        adx_1h:    float,
        adx_15m:   float,
    ) -> str:
        """
        تقييم وصفي لقوة توافق الأطر الزمنية.

        يُعطي مؤشراً سريعاً عن جودة الإشارة من منظور MTF.

        Returns:
            'excellent' | 'strong' | 'moderate' | 'weak'
        """
        if not aligned:
            return 'weak'

        score = 0
        if ema_1h:               score += 2   # EMA21>EMA50>EMA200 على 1H
        if ema_15m:              score += 2   # EMA21>EMA50>EMA200 على 15M
        if adx_1h  > 35:         score += 1   # اتجاه قوي على 1H
        if adx_15m > 35:         score += 1   # اتجاه قوي على 15M
        if crossover == 'bullish_cross': score += 2  # تقاطع حديث

        if   score >= 6: return 'excellent'
        elif score >= 4: return 'strong'
        elif score >= 2: return 'moderate'
        else:            return 'weak'

    # ═══════════════════════════════════════════════════
    # التقرير التفصيلي الشامل
    # ═══════════════════════════════════════════════════

    def get_signal_breakdown(
        self,
        df_1h:  pd.DataFrame,
        df_15m: pd.DataFrame,
        df_5m:  pd.DataFrame,
    ) -> Dict:
        """
        تقرير شامل يجمع gates + validate_signal + score + MTF.

        يُستخدم لـ:
            • التسجيل في قاعدة البيانات (كل إشارة محللة)
            • إرسال تقرير تفصيلي عبر Telegram
            • تتبع جودة الإشارات بمرور الوقت
            • تصحيح الأخطاء والمراجعة اليدوية

        Returns:
            dict متكامل يمر بالترتيب:
                timestamp        ← وقت التحليل
                gate_passed      ← هل اجتازت الشروط الستة؟
                gate_result      ← نتيجة check_buy_signal() كاملة
                signal_valid     ← هل اجتازت validate_signal()؟
                validation_info  ← نتيجة validate_signal() كاملة
                score_result     ← نتيجة calculate_signal_score() كاملة
                momentum_detail  ← ملخص مؤشرات الزخم (من indicators/)
                volatility_detail← ملخص مؤشرات التقلب (من indicators/)
                entry_quality    ← وصف نصي شامل لجودة الدخول
                should_trade     ← القرار النهائي (True/False)
        """
        ts = datetime.utcnow().isoformat()

        # ── المستوى 1: الشروط الستة ────────────────────
        gate_passed, gate_result = self.check_buy_signal(df_1h, df_15m, df_5m)

        breakdown: Dict = {
            'timestamp':   ts,
            'gate_passed': gate_passed,
            'gate_result': gate_result,
        }

        if not gate_passed:
            breakdown.update({
                'signal_valid':    False,
                'should_trade':    False,
                'entry_quality':   'rejected_by_gates',
                'score_result':    {'total_score': 0, 'recommendation': 'weak'},
            })
            return breakdown

        # ── المستوى 3: حساب الدرجة ─────────────────────
        score_result = self.calculate_signal_score(df_1h, df_15m, df_5m)
        breakdown['score_result'] = score_result

        # ── المستوى 2: validate_signal ─────────────────
        signal_valid, validation_info = self.validate_signal(
            df_1h, df_15m, df_5m, gate_result
        )
        breakdown['signal_valid']   = signal_valid
        breakdown['validation_info'] = validation_info

        # ── ملخصات المؤشرات من indicators/ ────────────
        try:
            breakdown['momentum_detail']    = get_momentum_summary(df_15m)
            breakdown['volatility_detail']  = get_volatility_summary(df_15m)
        except Exception as e:
            logger.warning(f"⚠️ خطأ في ملخصات المؤشرات: {e}")

        # ── القرار النهائي ───────────────────────────────
        breakdown['should_trade'] = gate_passed and signal_valid

        # ── وصف شامل لجودة الدخول ───────────────────────
        total  = score_result.get('total_score', 0)
        rec    = score_result.get('recommendation', 'weak')
        mtf    = score_result.get('mtf_analysis', {})
        aq     = mtf.get('alignment_quality', 'weak')
        regime = mtf.get('volatility_regime', '')

        if not signal_valid:
            rejections = validation_info.get('rejections', [])
            breakdown['entry_quality'] = (
                f"مرفوضة بـ validate_signal: {'; '.join(rejections)}"
            )
        elif rec == 'strong' and aq in ('excellent', 'strong'):
            breakdown['entry_quality'] = (
                f"✅ ممتاز — Score={total:.0f} | "
                f"توافق: {aq} | تقلب: {regime}"
            )
        elif rec == 'strong':
            breakdown['entry_quality'] = (
                f"✅ قوي — Score={total:.0f} | توافق: {aq}"
            )
        elif rec == 'good':
            breakdown['entry_quality'] = (
                f"⚡ جيد — Score={total:.0f} | حجم: 75%"
            )
        else:
            breakdown['entry_quality'] = (
                f"⚠️ ضعيف — Score={total:.0f} — تجاوز الحد الأدنى بالكاد"
            )

        return breakdown

    # ═══════════════════════════════════════════════════
    # حساب حجم الصفقة (مُحدَّث: يأخذ signal_score)
    # ═══════════════════════════════════════════════════

    def calculate_position_size(
        self,
        balance:         float,
        entry_price:     float,
        stop_loss_price: float,
        max_leverage:    int   = None,
        signal_score:    float = 100.0,
    ) -> Dict:
        """
        حساب حجم الصفقة الديناميكي.

        v1.2: يأخذ signal_score لتحديد نسبة الحجم:
            score ≥ 80 → size_factor = 1.00 (100%) — إشارة قوية
            score 60-79→ size_factor = 0.75  (75%) — إشارة جيدة
            score < 60 → size_factor = 0.50  (50%) — احتياطي (نادر)

        الصيغة:
            risk_amount       = balance × 2% × size_factor
            stop_distance_pct = (entry - SL) / entry
            position_notional = risk_amount / stop_distance_pct
            leverage          = min(notional / balance, max_leverage)
            contract_size     = notional / entry_price

        Args:
            balance:         الرصيد المتاح (USDT)
            entry_price:     سعر الدخول
            stop_loss_price: سعر وقف الخسارة
            max_leverage:    الرافعة القصوى (افتراضي: 10)
            signal_score:    درجة الإشارة (0-100)

        Returns:
            dict بجميع معاملات الحجم، أو {} عند فشل التحقق
        """
        try:
            if max_leverage is None:
                max_leverage = self.MAX_LEVERAGE

            if balance <= 0 or entry_price <= 0 or stop_loss_price <= 0:
                logger.warning("⚠️ مدخلات غير صالحة في calculate_position_size")
                return {}

            if stop_loss_price >= entry_price:
                logger.warning(
                    f"⚠️ SL ({stop_loss_price:.2f}) ≥ Entry ({entry_price:.2f})"
                )
                return {}

            # ── معامل الحجم حسب درجة الإشارة ─────────────
            if signal_score >= self.SCORE_STRONG:
                size_factor = 1.00
            elif signal_score >= self.SCORE_MIN_TRADE:
                size_factor = 0.75
            else:
                size_factor = 0.50

            # ── حسابات الحجم ─────────────────────────────
            risk_amount       = balance * self.RISK_PER_TRADE * size_factor
            stop_distance     = entry_price - stop_loss_price
            stop_distance_pct = stop_distance / entry_price

            if stop_distance_pct <= 0:
                logger.warning("⚠️ stop_distance_pct ≤ 0")
                return {}

            position_notional = risk_amount / stop_distance_pct
            leverage          = min(position_notional / balance, max_leverage)
            leverage          = max(1.0, leverage)
            contract_size     = position_notional / entry_price

            return {
                'risk_amount':        round(risk_amount, 4),
                'stop_distance':      round(stop_distance, 4),
                'stop_distance_pct':  round(stop_distance_pct * 100, 4),
                'position_notional':  round(position_notional, 4),
                'leverage':           round(leverage, 2),
                'contract_size':      round(contract_size, 6),
                'size_factor':        size_factor,
                'signal_score':       signal_score,
            }

        except Exception as e:
            logger.error(f"❌ خطأ في calculate_position_size: {e}")
            return {}

    # ═══════════════════════════════════════════════════
    # حساب نقاط الخروج (يُفوَّض لـ indicators/volatility.py)
    # ═══════════════════════════════════════════════════

    def calculate_exits(self, entry_price: float, atr: float) -> Dict:
        """
        حساب SL و TP1 و TP2 من ATR.

        يُفوَّض الحساب لـ calculate_atr_stops في indicators/volatility.py
        بدلاً من تكرار المنطق هنا.

        الصيغة (v1.1 مُصلَحة):
            SL  = Entry - ATR × 1.5
            TP1 = Entry + ATR × 2.0  (50%)
            TP2 = Entry + ATR × 4.5  (50%)
            R:R = 3.25 / 1.5 = 2.17 ✓
        """
        if entry_price <= 0 or atr <= 0:
            return {'valid': False, 'reason': 'سعر أو ATR صفري أو سالب'}

        result = calculate_atr_stops(
            entry_price = entry_price,
            atr         = atr,
            sl_mult     = self.ATR_SL_MULT,
            tp1_mult    = self.ATR_TP1_MULT,
            tp2_mult    = self.ATR_TP2_MULT,
            min_rr      = self.MIN_RISK_REWARD,
        )

        if not result.get('valid', False):
            logger.warning(
                f"⚠️ R:R غير كافٍ: "
                f"{result.get('risk_reward_ratio', 0):.2f} "
                f"< {self.MIN_RISK_REWARD}"
            )
        return result

    # ═══════════════════════════════════════════════════
    # التحقق من شروط الخروج
    # ═══════════════════════════════════════════════════

    def should_exit(
        self,
        current_price: float,
        stop_loss:     float,
        take_profit_1: float,
        take_profit_2: float,
    ) -> Tuple[bool, Optional[str], Optional[float]]:
        """
        التحقق من شروط الخروج في كل دورة من حلقة البوت.

        الأولوية (من الأعلى):
            1. Stop Loss     ← حماية رأس المال أولاً
            2. Take Profit 2 ← الهدف الكامل الثاني
            3. Take Profit 1 ← الهدف الأول

        Args:
            current_price:  السعر الحالي
            stop_loss:      مستوى وقف الخسارة
            take_profit_1:  الهدف الأول
            take_profit_2:  الهدف الثاني

        Returns:
            (should_exit, reason, exit_price)
            reason: 'STOP_LOSS' | 'TAKE_PROFIT_1' | 'TAKE_PROFIT_2' | None
        """
        if stop_loss and current_price <= stop_loss:
            return True, 'STOP_LOSS', stop_loss

        if take_profit_2 and current_price >= take_profit_2:
            return True, 'TAKE_PROFIT_2', take_profit_2

        if take_profit_1 and current_price >= take_profit_1:
            return True, 'TAKE_PROFIT_1', take_profit_1

        return False, None, None

    # ═══════════════════════════════════════════════════
    # المستوى 4: validate_trade() — صحة معاملات التنفيذ
    # (منفصل تماماً عن validate_signal)
    # ═══════════════════════════════════════════════════

    def validate_trade(self, trade: Dict) -> bool:
        """
        التحقق النهائي من صحة معاملات الصفقة قبل الإرسال.

        الفرق عن validate_signal():
        ────────────────────────────
        validate_signal() ← هل إشارة السوق جيدة؟   (جودة الفرصة)
        validate_trade()  ← هل معاملات التنفيذ صحيحة؟ (أمان التنفيذ)

        يُشكّل خط الدفاع الأخير قبل إرسال أي أمر للبورصة.
        حتى لو اجتازت الإشارة كل المستويات السابقة،
        هذا التحقق يضمن أن الأسعار والكميات منطقية.

        Args:
            trade: dict يحتوي على معاملات الصفقة:
                   entry_price, stop_loss, take_profit_1, contract_size

        Returns:
            True إذا كانت جميع المعاملات صحيحة
            False مع تسجيل تحذير توضيحي عند أي خطأ
        """
        try:
            # ── التحقق من الحقول المطلوبة ─────────────────
            required = ['entry_price', 'stop_loss', 'take_profit_1', 'contract_size']
            for field in required:
                if field not in trade or trade[field] is None:
                    logger.warning(f"⚠️ validate_trade: حقل مفقود — {field}")
                    return False

            entry         = float(trade['entry_price'])
            sl            = float(trade['stop_loss'])
            tp1           = float(trade['take_profit_1'])
            contract_size = float(trade['contract_size'])

            # ── أسعار منطقية ─────────────────────────────
            if entry <= 0:
                logger.warning(f"⚠️ validate_trade: entry_price غير منطقي ({entry})")
                return False

            if contract_size <= 0:
                logger.warning(f"⚠️ validate_trade: contract_size غير منطقي ({contract_size})")
                return False

            # ── العلاقة المنطقية: SL < Entry < TP ─────────
            if sl >= entry:
                logger.warning(
                    f"⚠️ validate_trade: SL ({sl:.4f}) ≥ Entry ({entry:.4f}) "
                    f"— يجب أن يكون SL أقل من Entry"
                )
                return False

            if tp1 <= entry:
                logger.warning(
                    f"⚠️ validate_trade: TP1 ({tp1:.4f}) ≤ Entry ({entry:.4f}) "
                    f"— يجب أن يكون TP1 أعلى من Entry"
                )
                return False

            # ── TP2 إذا كان موجوداً ─────────────────────
            tp2 = trade.get('take_profit_2')
            if tp2 is not None:
                tp2 = float(tp2)
                if tp2 <= tp1:
                    logger.warning(
                        f"⚠️ validate_trade: TP2 ({tp2:.4f}) ≤ TP1 ({tp1:.4f})"
                    )
                    return False

            return True

        except (TypeError, ValueError) as e:
            logger.error(f"❌ validate_trade: خطأ في تحويل القيم — {e}")
            return False
        except Exception as e:
            logger.error(f"❌ validate_trade: خطأ غير متوقع — {e}")
            return False

    # ═══════════════════════════════════════════════════
    # دوال الطباعة والتقرير
    # ═══════════════════════════════════════════════════

    def print_signal(self, signal_data: Dict):
        """طباعة تفاصيل إشارة الشراء بشكل منسق."""
        print("\n" + "═" * 65)
        print("📊 تحليل إشارة الشراء")
        print("═" * 65)

        if not signal_data.get('all_passed', False):
            print(f"\n❌ مرفوضة: {signal_data.get('reason', 'غير محدد')}")
            if 'conditions' in signal_data:
                failed = [k for k, v in signal_data['conditions'].items() if not v]
                for f in failed:
                    print(f"   ✗ {f}")
            print("═" * 65)
            return

        print(f"\n✅ الشروط الستة:")
        for cond, passed in signal_data.get('conditions', {}).items():
            icon = "✅" if passed else "❌"
            print(f"   {icon} {cond}")

        print(f"\n💰 بيانات الإشارة:")
        print(f"   سعر الدخول : ${signal_data.get('entry_price', 0):>12,.2f}")
        print(f"   ATR        : {signal_data.get('atr',          0):>12.2f}")
        print(f"   RSI        : {signal_data.get('rsi',          0):>12.1f}")
        print(f"   ADX        : {signal_data.get('adx',          0):>12.1f}")
        print(f"   EMA21 dist : {signal_data.get('ema21_dist',   0):>11.2f}%")

        atr   = signal_data.get('atr',         0)
        entry = signal_data.get('entry_price', 0)
        if atr > 0 and entry > 0:
            exits = self.calculate_exits(entry, atr)
            print(f"\n🎯 نقاط الخروج المحسوبة:")
            print(f"   SL  : ${exits.get('stop_loss',      0):>12,.2f}")
            print(f"   TP1 : ${exits.get('take_profit_1',  0):>12,.2f}")
            print(f"   TP2 : ${exits.get('take_profit_2',  0):>12,.2f}")
            print(f"   R:R : {exits.get('risk_reward_ratio', 0):>12.2f}:1"
                  f"  {'✅' if exits.get('valid') else '❌'}")

        print("═" * 65)

    def print_score_report(self, score_result: Dict):
        """طباعة تقرير درجة الإشارة بشكل منسق."""
        print("\n" + "─" * 65)
        print("🏆 Signal Score Report")
        print("─" * 65)

        total = score_result.get('total_score', 0)
        rec   = score_result.get('recommendation', 'weak')
        rec_labels = {
            'strong': '🟢 قوية  — حجم كامل (100%)',
            'good':   '🟡 جيدة  — حجم مخفض (75%)',
            'weak':   '🔴 ضعيفة — تجنب الدخول',
        }

        bar_filled = int(total / 5)
        bar = '█' * bar_filled + '░' * (20 - bar_filled)

        print(f"\n   [{bar}] {total:.1f}/100")
        print(f"   التوصية: {rec_labels.get(rec, rec)}")

        print(f"\n   التفاصيل (الدرجات الخام × الوزن):")
        w = score_result.get('weighted', {})
        print(f"   Trend     (×40%): {score_result.get('trend_score',      0):5.1f}"
              f"  → {w.get('trend',      0):5.1f}")
        print(f"   Momentum  (×40%): {score_result.get('momentum_score',   0):5.1f}"
              f"  → {w.get('momentum',   0):5.1f}")
        print(f"   Volatility(×20%): {score_result.get('volatility_score', 0):5.1f}"
              f"  → {w.get('volatility', 0):5.1f}")

        mtf = score_result.get('mtf_analysis', {})
        if mtf:
            print(f"\n   تحليل الأطر الزمنية (MTF):")
            aligned = '✅ متوافق' if mtf.get('aligned') else '⚠️ غير متوافق'
            print(f"   1H  Trend : {mtf.get('trend_1h',  'N/A'):>12}  "
                  f"ADX={mtf.get('adx_1h',  0):.1f}  "
                  f"{mtf.get('trend_strength_1h',  '')}")
            print(f"   15M Trend : {mtf.get('trend_15m', 'N/A'):>12}  "
                  f"ADX={mtf.get('adx_15m', 0):.1f}  "
                  f"{mtf.get('trend_strength_15m', '')}")
            print(f"   التوافق   : {aligned}")
            print(f"   جودة التوافق: {mtf.get('alignment_quality', 'N/A')}")
            print(f"   MACD Cross: {mtf.get('macd_crossover', 'N/A')}")
            print(f"   التقلب    : {mtf.get('volatility_regime', 'N/A')}")

        print("─" * 65)


# ─────────────────────────────────────────────────────────
# اختبار سريع عند التشغيل المباشر
# ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    s = TradingStrategy()

    # التحقق من R:R
    exits = s.calculate_exits(entry_price=43_250.0, atr=150.0)
    print(f"\n🧮 التحقق من R:R:")
    print(f"   SL  = ${exits.get('stop_loss',     0):,.2f}")
    print(f"   TP1 = ${exits.get('take_profit_1', 0):,.2f}")
    print(f"   TP2 = ${exits.get('take_profit_2', 0):,.2f}")
    print(f"   R:R = {exits.get('risk_reward_ratio', 0):.3f}:1  "
          f"{'✅' if exits.get('valid') else '❌'}")
    print(f"\n📋 الإصدار: v{s.version}")

