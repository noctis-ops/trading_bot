"""
═══════════════════════════════════════════════════════════
مؤشرات التقلب المتقدمة — Volatility Indicators
═══════════════════════════════════════════════════════════
تغطي جميع احتياجات الاستراتيجية المتعلقة بالتقلب:

  - ATR(14)              — النطاق الحقيقي المتوسط
  - Bollinger Bands(20)  — فرقات بولينجر
  - Bandwidth & Squeeze  — كشف الضغط وانفجار التقلب
  - Volatility Regime    — تصنيف بيئة السوق
  - ATR-based Stops      — حساب SL/TP من ATR
  - نقاط جودة التقلب     (0-100)

دوال خالصة (pure functions) — لا imports من داخل المشروع.
"""

import pandas as pd
import numpy as np
from typing import Dict, Tuple, Optional


# ═══════════════════════════════════════════════════════
# ATR — متوسط النطاق الحقيقي
# ═══════════════════════════════════════════════════════

def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    حساب ATR (Average True Range)

    True Range = max(High-Low, |High-PrevClose|, |Low-PrevClose|)
    ATR = EWM(TR, period)   ← نستخدم EWM للاستجابة الأسرع للتقلب الحديث

    Args:
        df:     DataFrame يحتوي على high, low, close
        period: فترة الحساب (افتراضي: 14)

    Returns:
        pd.Series بقيم ATR
    """
    high  = df['high']
    low   = df['low']
    close = df['close']

    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low  - close.shift(1)).abs()

    tr  = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.ewm(span=period, adjust=False).mean()

    return atr


def calculate_atr_percent(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    ATR كنسبة مئوية من السعر الحالي

    مفيد لمقارنة التقلب بين أزواج بأسعار مختلفة.

    Args:
        df:     DataFrame
        period: فترة ATR

    Returns:
        pd.Series — ATR% = ATR / Close × 100
    """
    atr   = calculate_atr(df, period)
    close = df['close']
    return (atr / close.replace(0, np.nan)) * 100


# ═══════════════════════════════════════════════════════
# Bollinger Bands — فرقات بولينجر
# ═══════════════════════════════════════════════════════

def calculate_bollinger_bands(
    series:  pd.Series,
    period:  int   = 20,
    std_dev: float = 2.0
) -> Dict[str, pd.Series]:
    """
    حساب فرقات بولينجر الكاملة

    Args:
        series:  سلسلة الأسعار (close)
        period:  الفترة الزمنية (افتراضي: 20)
        std_dev: عدد الانحرافات المعيارية (افتراضي: 2.0)

    Returns:
        dict يحتوي على:
            upper  — الحد الأعلى
            middle — المتوسط (SMA20)
            lower  — الحد الأدنى
            width  — العرض النسبي (bandwidth)
            pct_b  — موقع السعر داخل الفرقات (0-1)
    """
    middle = series.rolling(window=period).mean()
    std    = series.rolling(window=period).std()
    upper  = middle + (std * std_dev)
    lower  = middle - (std * std_dev)

    # Bandwidth: عرض الفرقات نسبةً للمتوسط (مقياس التقلب)
    width  = (upper - lower) / middle.replace(0, np.nan) * 100

    # %B: موقع السعر بين الفرقات (0=عند Lower, 1=عند Upper, 0.5=عند Middle)
    band_range = (upper - lower).replace(0, np.nan)
    pct_b      = (series - lower) / band_range

    return {
        'upper':  upper,
        'middle': middle,
        'lower':  lower,
        'width':  width,
        'pct_b':  pct_b,
    }


def calculate_bollinger_bandwidth(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """
    حساب Bandwidth وحده (اختصار للاستخدام المتكرر)

    Bandwidth = (Upper - Lower) / Middle × 100

    قيمة عالية = تقلب عالٍ  (السوق في حالة انتشار)
    قيمة منخفضة = تقلب منخفض (السوق في حالة ضغط — Squeeze)

    Args:
        df:     DataFrame
        period: فترة الحساب

    Returns:
        pd.Series بقيم Bandwidth
    """
    bb = calculate_bollinger_bands(df['close'], period)
    return bb['width']


# ═══════════════════════════════════════════════════════
# Bollinger Squeeze — كشف ضغط التقلب
# ═══════════════════════════════════════════════════════

def detect_bollinger_squeeze(
    df:            pd.DataFrame,
    bb_period:     int   = 20,
    kc_period:     int   = 20,
    kc_multiplier: float = 1.5
) -> pd.Series:
    """
    كشف Bollinger Squeeze (ضغط التقلب قبل حركة كبيرة)

    الفكرة: عندما تكون Bollinger Bands داخل Keltner Channels
            → السوق يتجمع قوته ← احتمال انفجار تقلب وشيك

    المنطق:
        Keltner Channels = EMA(20) ± ATR(20) × 1.5
        Squeeze = BB_Upper < KC_Upper AND BB_Lower > KC_Lower

    Args:
        df:            DataFrame
        bb_period:     فترة Bollinger Bands
        kc_period:     فترة Keltner Channels
        kc_multiplier: معامل ATR لـ Keltner

    Returns:
        pd.Series من True/False — True يعني وجود Squeeze
    """
    close = df['close']
    high  = df['high']
    low   = df['low']

    # ── Bollinger Bands ───────────────────────────────
    bb      = calculate_bollinger_bands(close, bb_period)
    bb_upper = bb['upper']
    bb_lower = bb['lower']

    # ── Keltner Channels ─────────────────────────────
    kc_middle = close.ewm(span=kc_period, adjust=False).mean()
    kc_atr    = calculate_atr(df, kc_period)
    kc_upper  = kc_middle + kc_atr * kc_multiplier
    kc_lower  = kc_middle - kc_atr * kc_multiplier

    # ── Squeeze: BB داخل KC ───────────────────────────
    squeeze = (bb_upper < kc_upper) & (bb_lower > kc_lower)

    return squeeze


def get_squeeze_momentum(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """
    قياس زخم ما بعد الـ Squeeze

    يُستخدم لتحديد اتجاه الانفجار المتوقع:
        양수 (موجب) = الانفجار صعودي محتمل
        سالب        = الانفجار هبوطي محتمل

    Args:
        df:     DataFrame
        period: الفترة

    Returns:
        pd.Series بقيم الزخم (موجبة/سالبة)
    """
    close  = df['close']
    high   = df['high']
    low    = df['low']

    # منتصف نطاق السعر (مرجع للزخم)
    delta   = close - ((high.rolling(period).max() + low.rolling(period).min()) / 2
                       + close.rolling(period).mean()) / 2

    # Linreg slope كمؤشر اتجاه الزخم
    momentum = delta.rolling(period).mean()
    return momentum


# ═══════════════════════════════════════════════════════
# تصنيف بيئة التقلب
# ═══════════════════════════════════════════════════════

def get_volatility_regime(df: pd.DataFrame, period: int = 14) -> str:
    """
    تصنيف بيئة التقلب الحالية

    المنطق:
        نقارن ATR الحالي بمتوسط ATR على آخر 50 شمعة

        ATR_ratio = ATR_current / ATR_avg_50

        > 1.5  → تقلب عالٍ   (High)   — صعب للتداول، حجم أصغر
        1.0-1.5→ تقلب طبيعي (Normal) ← الأمثل للاستراتيجية
        0.7-1.0→ تقلب منخفض (Low)    — انتظر Squeeze Breakout
        < 0.7  → تقلب ضئيل جداً (Contracting) — تجنب

    Args:
        df:     DataFrame
        period: فترة ATR

    Returns:
        'high' | 'normal' | 'low' | 'contracting'
    """
    if df.empty or len(df) < 50:
        return 'normal'

    atr_series  = calculate_atr(df, period)
    atr_current = atr_series.iloc[-2]
    atr_avg_50  = atr_series.iloc[-50:].mean()

    if atr_avg_50 <= 0:
        return 'normal'

    ratio = atr_current / atr_avg_50

    if   ratio > 1.5:  return 'high'
    elif ratio > 1.0:  return 'normal'
    elif ratio > 0.7:  return 'low'
    else:              return 'contracting'


def get_volatility_regime_label(regime: str) -> str:
    """تحويل regime إلى وصف نصي مع إيموجي"""
    labels = {
        'high':         'عالٍ        🔥 (حجم أصغر)',
        'normal':       'طبيعي       ✅ (أمثل)',
        'low':          'منخفض       ⚠️ (انتظر Breakout)',
        'contracting':  'ضئيل جداً   ❌ (تجنب)',
    }
    return labels.get(regime, 'غير محدد')


# ═══════════════════════════════════════════════════════
# حساب نقاط SL/TP بناءً على ATR
# ═══════════════════════════════════════════════════════

def calculate_atr_stops(
    entry_price: float,
    atr:         float,
    sl_mult:     float = 1.5,
    tp1_mult:    float = 2.0,
    tp2_mult:    float = 4.5,
    min_rr:      float = 2.0
) -> Dict:
    """
    حساب نقاط Stop Loss و Take Profit من ATR

    الصيغة (حسب STRATEGY_COMPLETE_GUIDE.md v1.1):
        SL  = Entry − ATR × 1.5
        TP1 = Entry + ATR × 2.0  (50% من الصفقة)
        TP2 = Entry + ATR × 4.5  (50% من الصفقة)

    التحقق من R:R:
        Risk   = Entry − SL = ATR × 1.5
        Reward = (TP1_gain × 0.5) + (TP2_gain × 0.5)
               = (ATR×2.0×0.5) + (ATR×4.5×0.5)
               = ATR × 3.25
        R:R    = 3.25 / 1.5 = 2.17 ✓

    Args:
        entry_price: سعر الدخول
        atr:         قيمة ATR الحالية
        sl_mult:     معامل ATR لـ Stop Loss  (افتراضي: 1.5)
        tp1_mult:    معامل ATR لـ TP الأول   (افتراضي: 2.0)
        tp2_mult:    معامل ATR لـ TP الثاني  (افتراضي: 4.5)
        min_rr:      الحد الأدنى لـ R:R      (افتراضي: 2.0)

    Returns:
        dict يحتوي على:
            stop_loss, take_profit_1, take_profit_2,
            risk, reward, risk_reward_ratio, valid
    """
    if entry_price <= 0 or atr <= 0:
        return {'valid': False, 'reason': 'سعر أو ATR صفري أو سالب'}

    stop_loss    = entry_price - (atr * sl_mult)
    take_profit1 = entry_price + (atr * tp1_mult)
    take_profit2 = entry_price + (atr * tp2_mult)

    risk   = entry_price - stop_loss
    reward = (take_profit1 - entry_price) * 0.5 + \
             (take_profit2 - entry_price) * 0.5

    rr = reward / risk if risk > 0 else 0

    return {
        'stop_loss':          stop_loss,
        'take_profit_1':      take_profit1,
        'take_profit_2':      take_profit2,
        'sl_distance':        risk,
        'sl_distance_pct':    risk / entry_price * 100,
        'tp1_distance':       take_profit1 - entry_price,
        'tp2_distance':       take_profit2 - entry_price,
        'risk':               risk,
        'reward':             reward,
        'risk_reward_ratio':  rr,
        'valid':              rr >= min_rr,
    }


# ═══════════════════════════════════════════════════════
# تقييم جودة التقلب للدخول (0-100)
# ═══════════════════════════════════════════════════════

def get_volatility_score(df: pd.DataFrame, period: int = 14) -> float:
    """
    تقييم جودة التقلب للدخول في صفقة (0-100)

    معايير التقييم:

    Volatility Regime (0-40):
        normal      → 40 نقطة (الأمثل للاستراتيجية)
        low         → 25 نقطة (يُقبل، انتظر تحسن)
        high        → 15 نقطة (خطر — حجم أصغر)
        contracting →  0 نقطة (تجنب)

    ATR Consistency (0-30):
        ATR ثابت نسبياً (انحراف < 20%) → 30 نقطة (تقلب يمكن التنبؤ به)
        ATR متذبذب      (انحراف < 40%) → 15 نقطة
        ATR فوضوي       (انحراف ≥ 40%) →  0 نقطة

    Bollinger Position (0-30):
        السعر بالقرب من Middle (30%-70% B%)  → 30 نقطة (منطقة التداول المثالية)
        السعر في النصف السفلي (10%-30% B%)  → 20 نقطة (يُقبل — مع تأكيد صعود)
        السعر قرب Lower أو Upper (< 10% / > 90%) →  5 نقطة (خطر انعكاس)

    Args:
        df:     DataFrame مكتمل المؤشرات
        period: فترة ATR

    Returns:
        float: نقاط التقلب (0-100)
    """
    if df.empty or len(df) < max(period + 5, 25):
        return 0.0

    score = 0.0
    close = df['close']
    last  = df.iloc[-2]

    # ── Volatility Regime (0-40) ────────────────────
    regime = get_volatility_regime(df, period)
    regime_scores = {
        'normal':      40,
        'low':         25,
        'high':        15,
        'contracting':  0,
    }
    score += regime_scores.get(regime, 0)

    # ── ATR Consistency (0-30) ──────────────────────
    atr_series = calculate_atr(df, period)
    if len(atr_series.dropna()) >= 20:
        recent_atr = atr_series.iloc[-20:]
        atr_cv     = recent_atr.std() / recent_atr.mean() \
                     if recent_atr.mean() > 0 else 1.0   # Coefficient of Variation
        if   atr_cv < 0.20: score += 30
        elif atr_cv < 0.40: score += 15
        # ≥ 0.40 = 0 نقاط

    # ── Bollinger Position (0-30) ───────────────────
    bb = calculate_bollinger_bands(close, period=20)
    if not bb['pct_b'].empty and len(bb['pct_b'].dropna()) > 1:
        pct_b = float(bb['pct_b'].iloc[-2])
        if 0.30 <= pct_b <= 0.70:
            score += 30   # منطقة التداول المثالية
        elif 0.10 <= pct_b < 0.30:
            score += 20   # يُقبل — الجزء السفلي من الوسط
        elif 0.70 < pct_b <= 0.90:
            score += 10   # يُقبل لكن قريب من ذروة الشراء
        else:
            score +=  5   # خطر — قريب من الحدود

    return min(score, 100.0)


# ═══════════════════════════════════════════════════════
# ملخص شامل لمؤشرات التقلب
# ═══════════════════════════════════════════════════════

def get_volatility_summary(df: pd.DataFrame, period: int = 14) -> Dict:
    """
    ملخص شامل لجميع مؤشرات التقلب

    Args:
        df:     DataFrame مكتمل المؤشرات
        period: فترة ATR

    Returns:
        dict بجميع مؤشرات وقيم التقلب الحالية
    """
    if df.empty or len(df) < 25:
        return {}

    last  = df.iloc[-2]
    close = df['close']

    atr_series = calculate_atr(df, period)
    atr_val    = float(atr_series.iloc[-2]) if len(atr_series) > 1 else 0

    atr_pct    = atr_val / float(last['close']) * 100 if last['close'] > 0 else 0

    bb         = calculate_bollinger_bands(close, period=20)
    bb_upper   = float(bb['upper'].iloc[-2]) if len(bb['upper']) > 1 else 0
    bb_lower   = float(bb['lower'].iloc[-2]) if len(bb['lower']) > 1 else 0
    bb_width   = float(bb['width'].iloc[-2]) if len(bb['width']) > 1 else 0
    pct_b      = float(bb['pct_b'].iloc[-2]) if len(bb['pct_b']) > 1 else 0.5

    regime     = get_volatility_regime(df, period)
    squeeze    = detect_bollinger_squeeze(df)
    in_squeeze = bool(squeeze.iloc[-2]) if len(squeeze) > 1 else False

    return {
        # ATR
        'atr':              atr_val,
        'atr_pct':          atr_pct,

        # Bollinger Bands
        'bb_upper':         bb_upper,
        'bb_lower':         bb_lower,
        'bb_width':         bb_width,
        'bb_pct_b':         pct_b,

        # Regime
        'regime':           regime,
        'regime_label':     get_volatility_regime_label(regime),
        'in_squeeze':       in_squeeze,

        # Score
        'volatility_score': get_volatility_score(df, period),
    }