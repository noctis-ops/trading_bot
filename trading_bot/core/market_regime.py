"""
═══════════════════════════════════════════════════════════
مصنّف حالة السوق — Market Regime Classifier
═══════════════════════════════════════════════════════════
يُحدّد "حالة السوق الكلية" (Regime) لكل زوج قبل أي قرار تداول.

لماذا؟
    خبير التداول لا يقفز مباشرة إلى مؤشر واحد — أولاً يحدد نوع السوق
    (صاعد / هابط / جانبي / متقلب) ثم يختار الاستراتيجية المناسبة.
    البوت كان يستخدم Trend-Following دائماً بغض النظر عن حالة السوق،
    فيُهدر الأسواق الجانبية.

الحالات:
    bull      → صاعد قوي   (يصلح: TrendFollowing Long)
    bear      → هابط قوي   (يصلح: TrendFollowing Short)
    range     → جانبي      (يصلح: MeanReversion)
    volatile  → متقلب      (يصلح: Breakout) — خيار احترازي

المنطق (مبني على مؤشرات المشروع الموجودة):
    - ADX  → قوة الاتجاه (عالٍ = bull/bear، منخفض = range)
    - EMA  → اتجاه السعر (price vs EMA200 + EMA50 vs EMA200)
    - ATR  → مستوى التقلب (مرتفع = volatile)

دوال خالصة (pure functions) — لا تلمس حالة خارجية.
"""

import pandas as pd
from typing import Dict

from indicators.volatility import get_volatility_regime


# ═══════════════════════════════════════════════════════════
# المصنّف
# ═══════════════════════════════════════════════════════════

def classify_market_regime(df: pd.DataFrame) -> str:
    """
    تصنيف حالة السوق الحالية لزوج واحد.

    Args:
        df: DataFrame مكتمل المؤشرات (ema_slow, ema_fast, adx, atr, close)

    Returns:
        'bull' | 'bear' | 'range' | 'volatile'
    """
    if df is None or df.empty or len(df) < 2:
        return 'range'

    last = df.iloc[-2]

    close    = float(last['close'])
    ema200   = float(last.get('ema_slow',   close))
    ema50    = float(last.get('ema_fast',   close))
    adx      = float(last.get('adx',  0))
    regime   = get_volatility_regime(df)

    # ── سوق متقلب بشدة (ATR مرتفع + ADX منخفض) → Breakout mode ──
    if regime == 'high' and adx < 25:
        return 'volatile'

    # ── قوة الاتجاه (ADX) ──────────────────────────────
    if adx >= 25:
        # صاعد قوي
        if close > ema200 and ema50 > ema200:
            return 'bull'
        # هابط قوي
        if close < ema200 and ema50 < ema200:
            return 'bear'

    # ── سوق جانبي (ADX منخفض أو ترتيب EMA متضارب) ────────
    return 'range'


# ═══════════════════════════════════════════════════════════
# الاستراتيجية المناسبة لكل حالة
# ═══════════════════════════════════════════════════════════

REGIME_TO_STRATEGY = {
    'bull':     'trend',      # صاعد → تتبع الاتجاه Long
    'bear':     'trend',      # هابط → تتبع الاتجاه Short
    'range':    'reversion',  # جانبي → ارتداد
    'volatile': 'breakout',   # متقلب → اختراق
}


def strategy_for_regime(regime: str) -> str:
    """
    الاستراتيجية الأنسب لحالة سوق محددة.

    Args:
        regime: 'bull' | 'bear' | 'range' | 'volatile'

    Returns:
        'trend' | 'reversion' | 'breakout'
    """
    return REGIME_TO_STRATEGY.get(regime, 'trend')


# ═══════════════════════════════════════════════════════════
# توجيه الاتجاه حسب الحالة
# ═══════════════════════════════════════════════════════════

def allowed_directions_for_regime(regime: str) -> list:
    """
    الاتجاهات المسموحة في حالة سوق معينة.

    Args:
        regime: 'bull' | 'bear' | 'range' | 'volatile'

    Returns:
        قائمة بالاتجاهات: ['long'], ['short'], ['long','short']
    """
    if regime == 'bull':
        return ['long']
    if regime == 'bear':
        return ['short']
    # range / volatile: يسمح بالاتجاهين (لكن عبر استراتيجية مختلفة)
    return ['long', 'short']


# ═══════════════════════════════════════════════════════════
# ملخص (للتسجيل والطباعة)
# ═══════════════════════════════════════════════════════════

REGIME_LABELS = {
    'bull':     '🐂 صاعد',
    'bear':     '🐻 هابط',
    'range':    '➖ جانبي',
    'volatile': '⚡ متقلب',
}


def regime_label(regime: str) -> str:
    """وصف نصي مع إيموجي لحالة السوق."""
    return REGIME_LABELS.get(regime, regime)
