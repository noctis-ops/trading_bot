"""
═══════════════════════════════════════════════════════════
كاشف ارتباط الأزواج — Pair Correlation (Phase 8.4 جزئي)
═══════════════════════════════════════════════════════════
يحل مشكلة "الأزواج الثلاثة مترابطة (BTC/ETH/BNB)".

لماذا؟
    فتح 3 صفقات على أزواج مرتبطة بشدة (مثل BTC و ETH و BNB) ليس تنويعاً
    حقيقياً — إنه عملياً صفقة واحدة برافعة مضاعفة. إذا هبط BTC، هبطت
    جميعها معاً.

الحل:
    قبل فتح صفقة جديدة، نحسب الارتباط (Correlation) بين الزوج المقترح
    والأزواج المفتوحة حالياً. إذا كان الارتباط مرتفعاً جداً (أعلى من
    حدّ قابل للضبط)، نتخطّى هذا الزوج لنفادى التركيز الزائد في اتجاه واحد.

المنهجية:
    - نحسب معامل ارتباط بيرسون (Pearson) بين عوائد الزوجين على مدى N شمعة.
    - إذا |correlation| > max_correlation (افتراضي 0.7) → تجنّب الدخول
      على زوج مرتبط بزوج مفتوح أصلاً.
    - يبقى السماح بزوج واحد غير مرتبط (تنويع حقيقي).

دوال خالصة (pure functions) — تعمل على DataFrames.
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Tuple, Optional

from utils.logger import logger


# ═══════════════════════════════════════════════════════════
# حساب الارتباط
# ═══════════════════════════════════════════════════════════

def compute_pair_correlation(
    df_a: pd.DataFrame,
    df_b: pd.DataFrame,
    window: int = 50,
) -> float:
    """
    معامل ارتباط بيرسون بين عوائد زوجين على نفس الفترة الزمنية.

    يحسب الارتباط على العوائد اللوغاريتمية (returns) بدل الأسعار الخام
    — لأن الأسعار الخام تعطي ارتباطاً عالياً دائماً حتى للأزواج المختلفة.

    Args:
        df_a: DataFrame الزوج الأول (يحتوي close)
        df_b: DataFrame الزوج الثاني (يحتوي close)
        window: عدد الشموع لنافذة الحساب

    Returns:
        float: معامل الارتباط (-1 إلى 1). إذا تعذّر الحساب → 0.0
    """
    try:
        # نجعل الفهارس متطابقة (على آخر window شمعة)
        a = df_a['close'].tail(window)
        b = df_b['close'].tail(window)

        # العوائد اللوغاريتمية
        ra = np.log(a / a.shift(1)).dropna()
        rb = np.log(b / b.shift(1)).dropna()

        # نطابق على الفهرس المشترك
        common = ra.index.intersection(rb.index)
        if len(common) < 2:
            return 0.0

        corr = ra[common].corr(rb[common])
        if np.isnan(corr):
            return 0.0
        return float(corr)

    except Exception as e:
        logger.debug(f"⚠️ خطأ في compute_pair_correlation: {e}")
        return 0.0


# ═══════════════════════════════════════════════════════════
# المحدد — هل يُسمح بالدخول على زوج؟
# ═══════════════════════════════════════════════════════════

def is_pair_correlated_with_open(
    candidate_symbol: str,
    candidate_df: pd.DataFrame,
    open_symbols: List[str],
    get_df_for_symbol,
    max_correlation: float = 0.7,
) -> Tuple[bool, Optional[str]]:
    """
    هل الزوج المقترح مرتبط بشدة بأي زوج مفتوح حالياً؟

    Args:
        candidate_symbol: الزوج المقترح (مثال 'BTC/USDT')
        candidate_df:     DataFrame الزوج المقترح
        open_symbols:     قائمة الأزواج المفتوحة حالياً
        get_df_for_symbol: callable(symbol) → DataFrame لذلك الزوج
        max_correlation:  حد الارتباط المسموح

    Returns:
        (is_correlated, correlated_with)
            is_correlated: True إذا تجبّب الدخول (ارتباط مرتفع بزوج مفتوح)
            correlated_with: اسم الزوج المرتبط أو None
    """
    if not open_symbols:
        return False, None

    # إن كان الزوج مفتوحاً بالفعل فلا نحتاج (يُفلتر قبل استدعائنا)
    for sym in open_symbols:
        if sym == candidate_symbol:
            continue
        try:
            other_df = get_df_for_symbol(sym)
            if other_df is None or other_df.empty:
                continue
            corr = compute_pair_correlation(candidate_df, other_df)
            if abs(corr) >= max_correlation:
                logger.debug(
                    f"🔗 {candidate_symbol} مرتبط بـ {sym} "
                    f"(corr={corr:.2f} ≥ {max_correlation}) — تجنّب التركيز"
                )
                return True, sym
        except Exception as e:
            logger.debug(f"⚠️ خطأ في فحص ارتباط {sym}: {e}")

    return False, None


# ═══════════════════════════════════════════════════════════
# ترتيب الأزواج حسب فرصة السوق (Level 3 مبدئي)
# ═══════════════════════════════════════════════════════════

def rank_pairs_by_opportunity(
    symbol_dfs: Dict[str, pd.DataFrame],
    strength_fn=None,
    limit: int = 3,
) -> List[Tuple[str, float]]:
    """
    ترتيب الأزواج حسب قوة فرصة السوق (تُستخدم لتحديد أولوية الدخول).

    Args:
        symbol_dfs: {symbol: DataFrame}
        strength_fn: callable(df) → float (درجة الفرصة). إن لم يُعطَ
                     نستخدم ADX كتقدير لقوة الاتجاه.
        limit: عدد الأزواج الأعلى

    Returns:
        قائمة مرتبة [(symbol, score), ...]
    """
    ranked = []
    for sym, df in symbol_dfs.items():
        if df is None or df.empty:
            continue
        try:
            if strength_fn is not None:
                score = float(strength_fn(df))
            else:
                # تقدير: قوة الاتجاه (ADX) × وجود اتجاه
                last = df.iloc[-2]
                adx = float(last.get('adx', 0))
                score = adx
            ranked.append((sym, round(score, 2)))
        except Exception:
            continue

    ranked.sort(key=lambda x: x[1], reverse=True)
    return ranked[:limit]
