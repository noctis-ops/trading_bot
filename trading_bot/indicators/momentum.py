"""
═══════════════════════════════════════════════════════════
مؤشرات الزخم المخصصة — Momentum Indicators
═══════════════════════════════════════════════════════════
تغطي احتياجات الاستراتيجية المتعلقة بالزخم:

  - RSI(14)           — قوة السوق النسبية
  - MACD(12,26,9)     — تقاطع الزخم
  - تقييم جودة RSI    (هل هو في المنطقة المثالية 50-70؟)
  - تقييم جودة MACD   (قوة الهيستوجرام)
  - نقاط الزخم       (0-100)

دوال خالصة (pure functions) — لا imports من داخل المشروع.
"""

import pandas as pd
import numpy as np
from typing import Dict, Tuple


# ═══════════════════════════════════════════════════════
# RSI — مؤشر القوة النسبية
# ═══════════════════════════════════════════════════════

def calculate_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """
    حساب RSI (Relative Strength Index)

    الصيغة:
        delta = price.diff()
        gain  = positive deltas rolling mean
        loss  = negative deltas rolling mean
        RS    = gain / loss
        RSI   = 100 - (100 / (1 + RS))

    Args:
        series: سلسلة الأسعار (close)
        period: الفترة (افتراضي: 14)

    Returns:
        pd.Series بقيم RSI (0-100)
    """
    delta = series.diff()
    gain  = delta.where(delta > 0, 0.0)
    loss  = (-delta.where(delta < 0, 0.0))

    avg_gain = gain.ewm(com=period - 1, adjust=False).mean()
    avg_loss = loss.ewm(com=period - 1, adjust=False).mean()

    rs  = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))

    return rsi.fillna(50)   # قيمة محايدة للقيم الأولى


def get_rsi_zone(rsi_value: float) -> str:
    """
    تصنيف قيمة RSI إلى منطقة

    المناطق حسب STRATEGY_COMPLETE_GUIDE.md:
        70-100: ذروة شراء    ← لا تدخل
        50-70:  منطقة الشراء ← هدفنا (الاستراتيجية تشترط هذه المنطقة)
        30-50:  محايد        ← انتظر
        0-30:   ذروة بيع     ← لا تدخل شراء

    Args:
        rsi_value: قيمة RSI

    Returns:
        'overbought' | 'buy_zone' | 'neutral' | 'oversold'
    """
    if   rsi_value >= 70:              return 'overbought'   # ❌ ذروة شراء
    elif 50 <= rsi_value < 70:         return 'buy_zone'     # ✅ منطقة مثالية
    elif 30 <= rsi_value < 50:         return 'neutral'      # ⚠️ محايد
    else:                              return 'oversold'     # ❌ ذروة بيع


def get_rsi_quality_score(rsi_value: float) -> float:
    """
    نقاط جودة RSI (0-40) — تُقيّم موقع RSI داخل منطقة الشراء

    المنطق:
        RSI خارج 50-70     →  0 نقطة (لا تستوفي شرط الاستراتيجية)
        RSI في 50-55       → 20 نقطة (بداية المنطقة — مقبول)
        RSI في 55-65       → 40 نقطة (المنطقة المثالية — أفضل دخول)
        RSI في 65-70       → 25 نقطة (نهاية المنطقة — يُقبل لكن أقل مثالية)

    Args:
        rsi_value: قيمة RSI

    Returns:
        float: نقاط الجودة (0-40)
    """
    if rsi_value < 50 or rsi_value > 70:
        return 0.0
    if 55 <= rsi_value <= 65:
        return 40.0   # المنطقة المثالية
    if 50 <= rsi_value < 55:
        return 20.0   # مقبول
    if 65 < rsi_value <= 70:
        return 25.0   # يُقبل لكن قريب من ذروة الشراء
    return 0.0


# ═══════════════════════════════════════════════════════
# MACD — مؤشر تقارب/تباعد المتوسطات
# ═══════════════════════════════════════════════════════

def calculate_macd(
    series:        pd.Series,
    fast_period:   int = 12,
    slow_period:   int = 26,
    signal_period: int = 9
) -> pd.DataFrame:
    """
    حساب MACD الكامل

    المخرجات:
        macd        — الفرق بين EMA12 و EMA26
        macd_signal — EMA9 للـ MACD
        macd_hist   — الهيستوجرام (macd - signal)

    Args:
        series:        سلسلة الأسعار (close)
        fast_period:   فترة EMA السريعة (افتراضي: 12)
        slow_period:   فترة EMA البطيئة (افتراضي: 26)
        signal_period: فترة خط الإشارة  (افتراضي: 9)

    Returns:
        DataFrame بأعمدة: macd, macd_signal, macd_hist
    """
    ema_fast   = series.ewm(span=fast_period,   adjust=False).mean()
    ema_slow   = series.ewm(span=slow_period,   adjust=False).mean()
    macd_line  = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal_period, adjust=False).mean()
    histogram  = macd_line - signal_line

    return pd.DataFrame({
        'macd':        macd_line,
        'macd_signal': signal_line,
        'macd_hist':   histogram,
    }, index=series.index)


def is_macd_bullish(df: pd.DataFrame) -> bool:
    """
    التحقق من أن MACD في حالة صعودية

    الشرط: MACD > خط الإشارة (macd_signal)
    هذا هو الشرط الخامس في الاستراتيجية.

    Args:
        df: DataFrame مع عمودي macd و macd_signal

    Returns:
        True إذا كان MACD أعلى من خط الإشارة
    """
    if df.empty or len(df) < 2:
        return False

    last = df.iloc[-2]
    return last.get('macd', 0) > last.get('macd_signal', 0)


def get_macd_quality_score(df: pd.DataFrame) -> float:
    """
    نقاط جودة MACD (0-30)

    معايير التقييم:
        macd > signal                → 15 نقطة (شرط الاستراتيجية الأساسي)
        histogram > 0                →  0 (مضمّن في الشرط أعلاه)
        histogram متزايد (أكبر من السابق) → 10 نقطة إضافية (زخم متسارع)
        macd > 0 (فوق خط الصفر)    →  5 نقاط إضافية (تأكيد إضافي)

    Args:
        df: DataFrame مع macd, macd_signal, macd_hist

    Returns:
        float: نقاط الجودة (0-30)
    """
    if df.empty or len(df) < 3:
        return 0.0

    last = df.iloc[-2]
    prev = df.iloc[-3]

    macd_val    = last.get('macd',        0)
    signal_val  = last.get('macd_signal', 0)
    hist_curr   = last.get('macd_hist',   0)
    hist_prev   = prev.get('macd_hist',   0)

    score = 0.0

    # شرط الاستراتيجية الأساسي
    if macd_val > signal_val:
        score += 15.0

    # الهيستوجرام يتزايد (زخم متسارع)
    if hist_curr > hist_prev:
        score += 10.0

    # MACD فوق خط الصفر (تأكيد إضافي)
    if macd_val > 0:
        score += 5.0

    return min(score, 30.0)


def detect_macd_crossover(df: pd.DataFrame) -> str:
    """
    كشف تقاطع MACD حديث

    يفحص آخر شمعتين لتحديد:
        - هل حدث تقاطع صاعد (bullish crossover) مؤخراً؟
        - هل حدث تقاطع هابط (bearish crossover) مؤخراً؟

    Args:
        df: DataFrame مع macd و macd_signal

    Returns:
        'bullish_cross' | 'bearish_cross' | 'bullish' | 'bearish' | 'none'
    """
    if df.empty or len(df) < 3:
        return 'none'

    last = df.iloc[-2]
    prev = df.iloc[-3]

    macd_curr   = last.get('macd',        0)
    signal_curr = last.get('macd_signal', 0)
    macd_prev   = prev.get('macd',        0)
    signal_prev = prev.get('macd_signal', 0)

    # تقاطع صاعد: MACD عبر خط الإشارة من الأسفل للأعلى
    if macd_prev <= signal_prev and macd_curr > signal_curr:
        return 'bullish_cross'   # 🟢 أفضل توقيت للدخول

    # تقاطع هابط: MACD عبر خط الإشارة من الأعلى للأسفل
    if macd_prev >= signal_prev and macd_curr < signal_curr:
        return 'bearish_cross'   # 🔴 تحذير

    # استمرار صاعد بدون تقاطع حديث
    if macd_curr > signal_curr:
        return 'bullish'

    # استمرار هابط
    return 'bearish'


# ═══════════════════════════════════════════════════════
# تقييم الزخم الإجمالي (0-100)
# ═══════════════════════════════════════════════════════

def get_momentum_score(df: pd.DataFrame) -> float:
    """
    حساب نقاط الزخم الإجمالية (0-100)

    التوزيع:
        RSI quality score  → 0-40 نقطة
        MACD quality score → 0-30 نقطة
        Volume strength    → 0-20 نقطة
        Crossover bonus    → 0-10 نقطة

    Args:
        df: DataFrame مع: close, rsi, macd, macd_signal,
                          macd_hist, volume, volume_sma

    Returns:
        float: نقاط الزخم (0-100)
    """
    if df.empty or len(df) < 3:
        return 0.0

    last  = df.iloc[-2]
    score = 0.0

    # ── RSI Quality (0-40) ────────────────────────────
    rsi_val = last.get('rsi', 50)
    score  += get_rsi_quality_score(rsi_val)

    # ── MACD Quality (0-30) ───────────────────────────
    score += get_macd_quality_score(df)

    # ── Volume Strength (0-20) ────────────────────────
    vol     = last.get('volume',     0)
    vol_sma = last.get('volume_sma', 1)
    if vol_sma > 0:
        vol_ratio = vol / vol_sma
        if   vol_ratio >= 2.0: score += 20
        elif vol_ratio >= 1.5: score += 15
        elif vol_ratio >= 1.0: score += 10  # شرط الاستراتيجية الأدنى
        # < 1.0 = 0 نقاط

    # ── Crossover Bonus (0-10) ────────────────────────
    crossover = detect_macd_crossover(df)
    if crossover == 'bullish_cross':
        score += 10   # أفضل توقيت
    elif crossover == 'bullish':
        score +=  5   # مستمر لكن بدون تقاطع حديث

    return min(score, 100.0)


# ═══════════════════════════════════════════════════════
# دوال المساعدة
# ═══════════════════════════════════════════════════════

def get_momentum_summary(df: pd.DataFrame) -> Dict:
    """
    ملخص شامل لجميع مؤشرات الزخم

    Args:
        df: DataFrame مكتمل المؤشرات

    Returns:
        dict يحتوي على جميع قيم ومؤشرات الزخم
    """
    if df.empty or len(df) < 3:
        return {}

    last = df.iloc[-2]

    rsi_val   = last.get('rsi',         50)
    macd_val  = last.get('macd',          0)
    sig_val   = last.get('macd_signal',   0)
    hist_val  = last.get('macd_hist',     0)
    vol       = last.get('volume',        0)
    vol_sma   = last.get('volume_sma',    1)

    crossover = detect_macd_crossover(df)

    return {
        # RSI
        'rsi':              rsi_val,
        'rsi_zone':         get_rsi_zone(rsi_val),
        'rsi_bullish':      50 <= rsi_val <= 70,

        # MACD
        'macd':             macd_val,
        'macd_signal':      sig_val,
        'macd_hist':        hist_val,
        'macd_bullish':     macd_val > sig_val,
        'macd_crossover':   crossover,

        # Volume
        'volume':           vol,
        'volume_sma':       vol_sma,
        'volume_ratio':     vol / vol_sma if vol_sma > 0 else 0,
        'volume_bullish':   vol > vol_sma,

        # Score
        'momentum_score':   get_momentum_score(df),
    }