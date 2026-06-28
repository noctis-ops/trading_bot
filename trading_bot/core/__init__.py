"""
Core module — المكونات الأساسية للبوت

التصدير:
- BinanceExchange     : الاتصال الحقيقي ببينانس (للإنتاج فقط)
- PaperTradingExchange: التداول الورقي الآمن   (للتطوير والاختبار)
- create_exchange     : factory function لاختيار الوضع تلقائياً
"""

import os
from .exchange      import BinanceExchange
from .paper_trading import PaperTradingExchange


def create_exchange(mode: str = None):
    """
    Factory function — تُعيد كائن التبادل المناسب حسب TRADING_MODE

    القراءة التلقائية من .env:
        TRADING_MODE=paper   → PaperTradingExchange (آمن، موصى به دائماً)
        TRADING_MODE=live    → BinanceExchange      (يحتاج مفاتيح API حقيقية)

    Args:
        mode: 'paper' | 'live' | None (يقرأ من .env)

    Returns:
        PaperTradingExchange أو BinanceExchange

    Raises:
        ValueError: إذا كان TRADING_MODE غير معروف
        EnvironmentError: إذا حاول المستخدم اختيار 'testnet'
    """
    mode = (mode or os.getenv('TRADING_MODE', 'paper')).lower().strip()

    # ── وضع Testnet: غير مدعوم → أعِد توجيه للـ Paper ──────
    if mode == 'testnet':
        from utils.logger import logger
        logger.critical(
            "❌ TRADING_MODE=testnet غير مدعوم!\n"
            "   Binance Futures Testnet أُوقف في CCXT (يونيو 2025)\n"
            "   ✅ الحل: استخدم TRADING_MODE=paper في ملف .env\n"
            "   Paper Trading يوفر نفس الأمان مع بيانات حقيقية."
        )
        raise EnvironmentError(
            "CCXT Testnet لـ Binance Futures متوقف. "
            "استخدم TRADING_MODE=paper"
        )

    # ── وضع Paper Trading (الافتراضي الموصى به) ─────────────
    if mode == 'paper':
        initial_balance = float(
            os.getenv('PAPER_INITIAL_BALANCE', '10000')
        )
        return PaperTradingExchange(initial_balance=initial_balance)

    # ── وضع Live (الإنتاج فقط — بعد اكتمال الاختبار) ────────
    if mode == 'live':
        from utils.logger import logger
        logger.warning(
            "⚠️  TRADING_MODE=live — تداول حقيقي بأموال حقيقية!\n"
            "   تأكد أن البوت اجتاز جميع اختبارات Paper Trading أولاً."
        )
        return BinanceExchange(use_testnet=False)

    # ── وضع غير معروف ────────────────────────────────────────
    raise ValueError(
        f"TRADING_MODE='{mode}' غير مدعوم.\n"
        f"القيم المقبولة: 'paper' | 'live'"
    )


__all__ = [
    'BinanceExchange',
    'PaperTradingExchange',
    'create_exchange',
]
