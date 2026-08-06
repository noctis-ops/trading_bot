"""
Indicators module — يحتوي على حسابات المؤشرات الفنية

تصدير موحّد لكل دوال المؤشرات (Trend + Momentum + Volatility)
بما فيها مرايا دعم الـ Short (Phase 6):
    Trend     → get_trend_score, get_bearish_trend_score, ...
    Momentum  → get_momentum_score, get_bearish_momentum_score, ...
    Volatility→ get_volatility_score, get_volatility_regime, ...

الاستخدام:
    from indicators import get_trend_score, get_bearish_trend_score
    from indicators import get_momentum_score, get_bearish_momentum_score
"""

# ── Trend (الاتجاه) ──────────────────────────────────────
from .trend import (
    calculate_ema,
    calculate_all_emas,
    calculate_adx,
    detect_trend_direction,
    is_ema_aligned_bullish,
    is_ema_aligned_bearish,
    get_price_distance_from_ema,
    analyze_multi_timeframe_trend,
    get_trend_score,
    get_bearish_trend_score,
    get_trend_strength_label,
    detect_bearish_trend_direction,
)

# ── Momentum (الزخم) ─────────────────────────────────────
from .momentum import (
    calculate_rsi,
    get_rsi_zone,
    get_rsi_quality_score,
    get_rsi_quality_score_bearish,
    calculate_macd,
    is_macd_bullish,
    is_macd_bearish,
    get_macd_quality_score,
    get_macd_quality_score_bearish,
    detect_macd_crossover,
    get_momentum_score,
    get_bearish_momentum_score,
    get_momentum_summary,
    get_bearish_momentum_summary,
    RSI_SHORT_MIN,
    RSI_SHORT_MAX,
)

# ── Volatility (التقلب) ──────────────────────────────────
from .volatility import (
    calculate_atr,
    calculate_atr_percent,
    calculate_bollinger_bands,
    calculate_bollinger_bandwidth,
    detect_bollinger_squeeze,
    get_squeeze_momentum,
    get_volatility_regime,
    get_volatility_regime_label,
    calculate_atr_stops,
    get_volatility_score,
    get_volatility_summary,
)

__all__ = [
    # trend
    'calculate_ema',
    'calculate_all_emas',
    'calculate_adx',
    'detect_trend_direction',
    'is_ema_aligned_bullish',
    'is_ema_aligned_bearish',
    'get_price_distance_from_ema',
    'analyze_multi_timeframe_trend',
    'get_trend_score',
    'get_bearish_trend_score',
    'get_trend_strength_label',
    'detect_bearish_trend_direction',
    # momentum
    'calculate_rsi',
    'get_rsi_zone',
    'get_rsi_quality_score',
    'get_rsi_quality_score_bearish',
    'calculate_macd',
    'is_macd_bullish',
    'is_macd_bearish',
    'get_macd_quality_score',
    'get_macd_quality_score_bearish',
    'detect_macd_crossover',
    'get_momentum_score',
    'get_bearish_momentum_score',
    'get_momentum_summary',
    'get_bearish_momentum_summary',
    'RSI_SHORT_MIN',
    'RSI_SHORT_MAX',
    # volatility
    'calculate_atr',
    'calculate_atr_percent',
    'calculate_bollinger_bands',
    'calculate_bollinger_bandwidth',
    'detect_bollinger_squeeze',
    'get_squeeze_momentum',
    'get_volatility_regime',
    'get_volatility_regime_label',
    'calculate_atr_stops',
    'get_volatility_score',
    'get_volatility_summary',
]
