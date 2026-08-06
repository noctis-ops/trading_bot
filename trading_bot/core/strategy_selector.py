"""
═══════════════════════════════════════════════════════════
محول اختيار الاستراتيجية — StrategySelector (Phase 8.2 / Level 2)
═══════════════════════════════════════════════════════════
Multi-Strategy Selection — اختيار أنسب استراتيجية للزوج/السوق.

الاستراتيجيات الثلاث (حسب BOT_ASSESSMENT.md و BOT_STATUS_REPORT.md):
    - TrendFollowing  (الحالي)  → للأسواق ذات الاتجاه القوي
    - MeanReversion             → للأسواق الجانبية (الارتداد)
    - Breakout                  → عند اختراق مستويات مهمة (squeeze)

كل استراتيجية تُقيّم "صلاحيتها" للسوق الحالي عبر calculate_fitness(df)
وتُعيد درجة 0-100. ثم يختار StrategySelector الاستراتيجية الأعلى درجة.

═══════════════════════════════════════════════════════════
الفلسفة (توافق مع الخطة):
    البنية مطابقة تماماً للتصميم في BOT_ASSESSMENT.md:
        class StrategySelector:
            trend_following / mean_reversion / breakout
            select_best_strategy(symbol, df) → (best_strategy, score)

    دوال calculate_fitness لكل استراتيجية تُبنى من المؤشرات الموجودة
    أصلاً في المشروع (indicators/ و data/market_data.py) دون إدخال
    مفاهيم خارجية — كل استراتيجية تستخدم المؤشرات التي تناسب هدفها
    الموصوف في المخطط:
        TrendFollowing  ← قوة الاتجاه (get_trend_score / get_bearish_trend_score)
        MeanReversion   ← الأسواق الجانبية (ADX منخفض) + RSI عند الأطراف
        Breakout        ← ضغط التقلب (Squeeze) + حجم أعلى من المتوسط
"""

import pandas as pd
from typing import Dict, Tuple

from utils.logger import logger
from indicators.trend import get_trend_score, get_bearish_trend_score
from indicators.volatility import get_volatility_summary


# ═══════════════════════════════════════════════════════════
# الاستراتيجيات الثلاث
# ═══════════════════════════════════════════════════════════

class TrendFollowingStrategy:
    """
    استراتيجية تتبع الاتجاه (الحالية) — تناسب الأسواق ذات الاتجاه القوي.

    الصلاحية = قوة الاتجاه (صاعد أو هابط):
        أعلى درجة من get_trend_score(df) و get_bearish_trend_score(df)
    """

    name = 'trend'

    def calculate_fitness(self, df: pd.DataFrame) -> float:
        """
        درجة صلاحية تتبع الاتجاه (0-100).

        Args:
            df: DataFrame مكتمل المؤشرات

        Returns:
            float درجة 0-100 — الأعلى كلما كان الاتجاه أقوى
        """
        if df is None or df.empty or len(df) < 2:
            return 0.0

        try:
            trend_up = get_trend_score(df)
            trend_dn = get_bearish_trend_score(df)
            # أقوى اتجاه (صاعد أو هابط) هو ما يهم لتتبع الاتجاه
            return min(100.0, max(trend_up, trend_dn))
        except Exception as e:
            logger.debug(f"⚠️ خطأ في TrendFollowing.calculate_fitness: {e}")
            return 0.0


class MeanReversionStrategy:
    """
    استراتيجية الارتداد (Mean Reversion) — تناسب الأسواق الجانبية.

    الصلاحية = سوق جانبي (ADX منخفض) + RSI عند الأطراف (فرصة ارتداد)
    """

    name = 'reversion'

    def calculate_fitness(self, df: pd.DataFrame) -> float:
        """
        درجة صلاحية الارتداد (0-100).

        Args:
            df: DataFrame مكتمل المؤشرات

        Returns:
            float درجة 0-100 — الأعلى في السوق الجانبي مع RSI عند الأطراف
        """
        if df is None or df.empty or len(df) < 2:
            return 0.0

        try:
            last = df.iloc[-2]
            adx = float(last.get('adx', 0))
            rsi = float(last.get('rsi', 50))

            # ── الأسواق الجانبية (ADX منخفض) مواتية للارتداد ──
            if   adx < 20: range_score = 50
            elif adx < 25: range_score = 35
            elif adx < 30: range_score = 20
            else:          range_score = 0

            # ── RSI عند الأطراف = فرصة ارتداد ──────────────
            if   rsi >= 70 or rsi <= 30: rsi_score = 50
            elif rsi >= 65 or rsi <= 35: rsi_score = 35
            elif rsi >= 55 or rsi <= 45: rsi_score = 15
            else:                        rsi_score = 0

            return min(100.0, range_score + rsi_score)
        except Exception as e:
            logger.debug(f"⚠️ خطأ في MeanReversion.calculate_fitness: {e}")
            return 0.0


class BreakoutStrategy:
    """
    استراتيجية الاختراق (Breakout) — تناسب لحظات اختراق المستويات.

    الصلاحية = ضغط التقلب (Squeeze) + حجم أعلى من المتوسط (تأكيد انفجار)
    """

    name = 'breakout'

    def calculate_fitness(self, df: pd.DataFrame) -> float:
        """
        درجة صلاحية الاختراق (0-100).

        Args:
            df: DataFrame مكتمل المؤشرات

        Returns:
            float درجة 0-100 — الأعلى عند Squeeze مع حجم مرتفع
        """
        if df is None or df.empty or len(df) < 3:
            return 0.0

        try:
            last = df.iloc[-2]

            # ── ضغط التقلب (Squeeze) → انفجار وشيك ─────────
            squeeze_score = 0.0
            vs = get_volatility_summary(df)
            if vs.get('in_squeeze'):
                squeeze_score = 50
            else:
                bb_width = vs.get('bb_width', 100)
                if   bb_width < 5: squeeze_score = 35
                elif bb_width < 8: squeeze_score = 20

            # ── حجم أعلى من المتوسط → تأكيد الانفجار ──────
            vol_score = 0.0
            vol  = float(last.get('volume',      0))
            vsma = float(last.get('volume_sma',  1))
            if vsma > 0:
                ratio = vol / vsma
                if   ratio >= 1.5: vol_score = 50
                elif ratio >= 1.2: vol_score = 35
                elif ratio >= 1.0: vol_score = 20

            return min(100.0, squeeze_score + vol_score)
        except Exception as e:
            logger.debug(f"⚠️ خطأ في Breakout.calculate_fitness: {e}")
            return 0.0


# ═══════════════════════════════════════════════════════════
# محول اختيار الاستراتيجية
# ═══════════════════════════════════════════════════════════

class StrategySelector:
    """
    محول اختيار أنسب استراتيجية للزوج الحالي (Multi-Strategy Selection).

    ══════════════════════════════════════════
    الموقع في معمارية البوت:
    ══════════════════════════════════════════

        StrategySelector                ← "أي استراتيجية للسوق الحالي؟"
             ├─→ TrendFollowingStrategy   (الاتجاه القوي)
             ├─→ MeanReversionStrategy    (الأسواق الجانبية)
             └─→ BreakoutStrategy         (اختراق المستويات)

    لا يتخذ قرار الدخول/الخروج بنفسه — فقط يقيّم أي استراتيجية أنسب
    للسوق الحالي ويُعيدها مع درجتها. قرار التداول الفعلي يبقى في
    core/strategy.py و core/bot.py.
    """

    def __init__(self):
        self.trend_following = TrendFollowingStrategy()
        self.mean_reversion  = MeanReversionStrategy()
        self.breakout        = BreakoutStrategy()
        logger.success(
            "✅ StrategySelector جاهز (Level 2): trend / reversion / breakout"
        )

    def get_scores(self, symbol: str, df: pd.DataFrame) -> Dict[str, float]:
        """
        درجات صلاحية الاستراتيجيات الثلاث للسوق الحالي.

        Args:
            symbol: الزوج (للسجلات فقط)
            df:     DataFrame مكتمل المؤشرات

        Returns:
            dict {'trend': float, 'reversion': float, 'breakout': float}
        """
        return {
            'trend':     self.trend_following.calculate_fitness(df),
            'reversion': self.mean_reversion.calculate_fitness(df),
            'breakout':  self.breakout.calculate_fitness(df),
        }

    def select_best_strategy(self, symbol: str, df: pd.DataFrame) -> Tuple[str, float]:
        """
        اختيار أنسب استراتيجية للزوج الحالي.

        Args:
            symbol: الزوج
            df:     DataFrame مكتمل المؤشرات

        Returns:
            (best_strategy, score)
                best_strategy: 'trend' | 'reversion' | 'breakout'
                score:         درجة الاستراتيجية المختارة (0-100)
        """
        scores = self.get_scores(symbol, df)
        best_strategy = max(scores, key=scores.get)
        return best_strategy, scores[best_strategy]

    def print_selection(self, symbol: str, df: pd.DataFrame):
        """طباعة درجات الاستراتيجيات والاختيار بشكل منسق."""
        scores = self.get_scores(symbol, df)
        best, score = self.select_best_strategy(symbol, df)

        labels = {
            'trend':     '📈 Trend Following',
            'reversion': '🔁 Mean Reversion',
            'breakout':  '⚡ Breakout',
        }

        print("\n" + "═" * 60)
        print(f"🎯 اختيار الاستراتيجية | {symbol}")
        print("═" * 60)
        for k, label in labels.items():
            v = scores[k]
            bar = '█' * int(v / 10) + '░' * (10 - int(v / 10))
            mark = '✅' if k == best else '  '
            print(f"   {mark} {label:<20} [{bar}] {v:.1f}")
        print(f"\n   🏆 الاستراتيجية المختارة: {labels.get(best, best)} "
              f"(درجة {score:.1f}/100)")
        print("═" * 60)


# ─────────────────────────────────────────────────────────
# اختبار سريع عند التشغيل المباشر
# ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    s = StrategySelector()
    print(f"\nStrategySelector جاهز:")
    print(f"   الاستراتيجيات: {s.trend_following.name}, "
          f"{s.mean_reversion.name}, {s.breakout.name}")
    print("\nلاستخدامه من الكود:")
    print("   from core.strategy_selector import StrategySelector")
    print("   selector = StrategySelector()")
    print("   best, score = selector.select_best_strategy(symbol, df)")
