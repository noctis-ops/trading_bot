"""
═══════════════════════════════════════════════════════════
مؤشرات الاتجاه المتقدمة — Trend Indicators
═══════════════════════════════════════════════════════════
تغطي جميع احتياجات الاستراتيجية المتعلقة بالاتجاه:

  - EMA (200, 50, 21)
  - ADX + DI+ / DI-   (قوة الاتجاه)
  - كشف اتجاه السوق  (bullish / bearish / sideways)
  - تحليل متعدد الأطر الزمنية
  - نقاط تقييم الاتجاه (0-100)

هذا الملف يحتوي على دوال خالصة (pure functions):
  ✅ لا imports من داخل المشروع
  ✅ مدخلات: pd.Series أو pd.DataFrame
  ✅ مخرجات: pd.Series أو dict أو float
"""

import pandas as pd
import numpy as np
from typing import Dict, Tuple


# ═══════════════════════════════════════════════════════
# EMA — متوسط الحركة الأسي
# ═══════════════════════════════════════════════════════

def calculate_ema(series: pd.Series, period: int) -> pd.Series:
    """
    حساب EMA (Exponential Moving Average)

    Args:
        series: سلسلة الأسعار (عادةً close)
        period: الفترة الزمنية

    Returns:
        pd.Series بقيم EMA
    """
    return series.ewm(span=period, adjust=False).mean()


def calculate_all_emas(df: pd.DataFrame) -> pd.DataFrame:
    """
    حساب EMA(200) و EMA(50) و EMA(21) دفعةً واحدة

    Args:
        df: DataFrame يحتوي على عمود 'close'

    Returns:
        DataFrame يحتوي على أعمدة إضافية:
            ema_slow   (200)
            ema_fast   (50)
            ema_medium (21)
    """
    df = df.copy()
    df['ema_slow']   = calculate_ema(df['close'], 200)  # EMA200
    df['ema_fast']   = calculate_ema(df['close'], 50)   # EMA50
    df['ema_medium'] = calculate_ema(df['close'], 21)   # EMA21
    return df


# ═══════════════════════════════════════════════════════
# ADX — مؤشر قوة الاتجاه
# ═══════════════════════════════════════════════════════

def calculate_adx(
    df:     pd.DataFrame,
    period: int = 14
) -> pd.DataFrame:
    """
    حساب ADX مع مؤشرات الاتجاه الإيجابي والسلبي

    المخرجات في DataFrame:
        adx      — قوة الاتجاه (0-100)
                   < 20: سوق راكد (sideways)
                   20-25: اتجاه ضعيف (حد الاستراتيجية عند 25)
                   25-50: اتجاه قوي   ← نريد الدخول هنا
                   > 50:  اتجاه قوي جداً
        plus_di  — قوة الاتجاه الصاعد (+DI)
        minus_di — قوة الاتجاه الهابط (-DI)

    Args:
        df:     DataFrame يحتوي على high, low, close
        period: فترة الحساب (افتراضي: 14)

    Returns:
        DataFrame مع أعمدة adx, plus_di, minus_di مُضافة
    """
    df = df.copy()
    high  = df['high']
    low   = df['low']
    close = df['close']

    # ── True Range ────────────────────────────────────
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low  - close.shift(1)).abs()
    tr  = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.ewm(span=period, adjust=False).mean()

    # ── Directional Movement ─────────────────────────
    up_move   = high.diff()
    down_move = -low.diff()

    plus_dm  = np.where((up_move > down_move)   & (up_move   > 0), up_move,   0.0)
    minus_dm = np.where((down_move > up_move)    & (down_move > 0), down_move, 0.0)

    plus_dm_s  = pd.Series(plus_dm,  index=df.index)
    minus_dm_s = pd.Series(minus_dm, index=df.index)

    # ── Smoothed DM ──────────────────────────────────
    plus_dm_smooth  = plus_dm_s.ewm(span=period,  adjust=False).mean()
    minus_dm_smooth = minus_dm_s.ewm(span=period, adjust=False).mean()

    # ── DI+ و DI- ───────────────────────────────────
    atr_safe = atr.replace(0, np.nan)
    plus_di  = 100 * (plus_dm_smooth  / atr_safe)
    minus_di = 100 * (minus_dm_smooth / atr_safe)

    # ── ADX ─────────────────────────────────────────
    di_sum  = (plus_di + minus_di).replace(0, np.nan)
    dx      = 100 * (plus_di - minus_di).abs() / di_sum
    adx_val = dx.ewm(span=period, adjust=False).mean()

    df['adx']      = adx_val.fillna(0)
    df['plus_di']  = plus_di.fillna(0)
    df['minus_di'] = minus_di.fillna(0)

    return df


# ═══════════════════════════════════════════════════════
# كشف اتجاه السوق
# ═══════════════════════════════════════════════════════

def detect_trend_direction(df: pd.DataFrame) -> str:
    """
    تحديد اتجاه السوق بناءً على EMA وADX

    المنطق:
        bullish  ← EMA50 > EMA200 AND ADX > 25 AND price > EMA200
        bearish  ← EMA50 < EMA200 AND ADX > 25 AND price < EMA200
        sideways ← ADX < 25 (سوق راكد بغض النظر عن EMAs)

    Args:
        df: DataFrame يحتوي على: close, ema_slow, ema_fast, adx

    Returns:
        'bullish' | 'bearish' | 'sideways'
    """
    if df.empty or len(df) < 2:
        return 'sideways'

    last = df.iloc[-2]  # آخر شمعة مكتملة

    adx_val  = last.get('adx', 0)
    close    = last['close']
    ema_slow = last.get('ema_slow', close)
    ema_fast = last.get('ema_fast', close)

    if adx_val < 20:
        return 'sideways'

    if close > ema_slow and ema_fast > ema_slow:
        return 'bullish'

    if close < ema_slow and ema_fast < ema_slow:
        return 'bearish'

    return 'sideways'


def is_ema_aligned_bullish(df: pd.DataFrame) -> bool:
    """
    التحقق من التوافق الكامل للـ EMA في الاتجاه الصاعد

    الشرط: EMA21 > EMA50 > EMA200
            (ترتيب صاعد = اتجاه صاعد قوي)

    Args:
        df: DataFrame مع ema_slow, ema_fast, ema_medium, close

    Returns:
        True إذا كان الترتيب ema_medium > ema_fast > ema_slow
    """
    if df.empty or len(df) < 2:
        return False

    last = df.iloc[-2]
    ema200 = last.get('ema_slow',   0)
    ema50  = last.get('ema_fast',   0)
    ema21  = last.get('ema_medium', 0)
    close  = last['close']

    return (
        close  > ema21  > 0 and
        ema21  > ema50  > 0 and
        ema50  > ema200 > 0
    )


def get_price_distance_from_ema(df: pd.DataFrame, ema_col: str = 'ema_medium') -> float:
    """
    حساب المسافة المئوية بين السعر وEMA21

    تُستخدم في الشرط السادس للاستراتيجية:
        distance ≤ 2% → قريب من EMA → دخول جيد

    Args:
        df:      DataFrame
        ema_col: اسم عمود الـ EMA (افتراضي: ema_medium = EMA21)

    Returns:
        المسافة كنسبة مئوية (موجبة دائماً)
    """
    if df.empty or len(df) < 2:
        return 999.0

    last    = df.iloc[-2]
    close   = last['close']
    ema_val = last.get(ema_col, close)

    if ema_val <= 0:
        return 999.0

    return abs(close - ema_val) / ema_val * 100


# ═══════════════════════════════════════════════════════
# تحليل متعدد الأطر الزمنية
# ═══════════════════════════════════════════════════════

def analyze_multi_timeframe_trend(
    df_1h:  pd.DataFrame,
    df_15m: pd.DataFrame
) -> Dict:
    """
    تحليل التوافق بين إطارين زمنيين

    المنطق:
        - 1H:  يحدد الاتجاه الرئيسي (يجب bullish)
        - 15M: يحدد نقطة الدخول (يجب bullish أيضاً)
        - كلاهما bullish = توافق ممتاز

    Args:
        df_1h:  بيانات الإطار الساعي  (مع ema_slow, ema_fast, adx)
        df_15m: بيانات إطار 15 دقيقة (مع ema_slow, ema_fast, adx)

    Returns:
        dict يحتوي على:
            trend_1h:       اتجاه 1H
            trend_15m:      اتجاه 15M
            aligned:        هل الاتجاهان متوافقان؟
            alignment_score: نقاط التوافق (0-40)
    """
    trend_1h  = detect_trend_direction(df_1h)
    trend_15m = detect_trend_direction(df_15m)
    aligned   = (trend_1h == 'bullish' and trend_15m == 'bullish')

    # ── نقاط التوافق ────────────────────────────────
    score = 0
    if trend_1h  == 'bullish': score += 20  # 1H الأهم (وزن أكبر)
    if trend_15m == 'bullish': score += 15  # 15M الإشارة
    if aligned:                 score +=  5  # بونص التوافق الكامل

    # ── كشف EMA alignment ───────────────────────────
    ema_bullish_1h  = is_ema_aligned_bullish(df_1h)
    ema_bullish_15m = is_ema_aligned_bullish(df_15m)

    return {
        'trend_1h':         trend_1h,
        'trend_15m':        trend_15m,
        'aligned':          aligned,
        'alignment_score':  score,           # 0-40
        'ema_aligned_1h':   ema_bullish_1h,
        'ema_aligned_15m':  ema_bullish_15m,
    }


# ═══════════════════════════════════════════════════════
# تقييم جودة الاتجاه (0-100)
# ═══════════════════════════════════════════════════════

def get_trend_score(df: pd.DataFrame) -> float:
    """
    حساب نقاط جودة الاتجاه على DataFrame واحد (0-100)

    معايير التقييم:
        ADX strength  (0-40 نقطة):
            ADX 25-35  → 20 نقطة (اتجاه قوي — منطقة الاستراتيجية)
            ADX 35-50  → 30 نقطة (اتجاه أقوى)
            ADX > 50   → 40 نقطة (اتجاه استثنائي)

        EMA alignment (0-30 نقطة):
            price > EMA200             → 10 نقطة
            EMA50  > EMA200            → 10 نقطة
            EMA21  > EMA50  > EMA200   → 10 نقطة إضافية (ترتيب كامل)

        EMA distance (0-30 نقطة):
            distance < 0.5% → 30 (دخول ممتاز — قريب جداً من EMA)
            distance < 1.0% → 20
            distance < 2.0% → 10 (حد الاستراتيجية)
            distance ≥ 2.0% →  0 (بعيد — لا تدخل)

    Args:
        df: DataFrame مع: close, ema_slow, ema_fast, ema_medium, adx

    Returns:
        float: نقاط الاتجاه (0-100)
    """
    if df.empty or len(df) < 2:
        return 0.0

    last   = df.iloc[-2]
    score  = 0.0
    close  = last['close']
    ema200 = last.get('ema_slow',   close)
    ema50  = last.get('ema_fast',   close)
    ema21  = last.get('ema_medium', close)
    adx    = last.get('adx', 0)

    # ── ADX Strength (0-40) ────────────────────────────
    if   adx > 50:  score += 40
    elif adx > 35:  score += 30
    elif adx > 25:  score += 20
    elif adx > 20:  score += 10
    # < 20 = 0 نقاط

    # ── EMA Alignment (0-30) ──────────────────────────
    if ema200 > 0 and close > ema200:  score += 10
    if ema200 > 0 and ema50 > ema200:  score += 10
    if ema50  > 0 and ema21 > ema50:   score += 10  # full alignment bonus

    # ── EMA Distance (0-30) ───────────────────────────
    distance = get_price_distance_from_ema(df, 'ema_medium')
    if   distance < 0.5: score += 30
    elif distance < 1.0: score += 20
    elif distance < 2.0: score += 10
    # ≥ 2.0 = 0 نقاط (شرط الاستراتيجية)

    return min(score, 100.0)


# ═══════════════════════════════════════════════════════
# تحديد قوة الاتجاه (وصف نصي)
# ═══════════════════════════════════════════════════════

def get_trend_strength_label(adx_value: float) -> str:
    """
    تحويل قيمة ADX إلى وصف نصي لقوة الاتجاه

    Args:
        adx_value: قيمة ADX (0-100)

    Returns:
        'استثنائي' | 'قوي جداً' | 'قوي' | 'ضعيف' | 'راكد'
    """
    if   adx_value > 50: return 'استثنائي  🔥'
    elif adx_value > 35: return 'قوي جداً  💪'
    elif adx_value > 25: return 'قوي       ✅'
    elif adx_value > 20: return 'ضعيف      ⚠️'
    else:                return 'راكد      ❌'