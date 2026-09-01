"""Core module — المكونات الأساسية للبوت.

Exchange implementations are imported lazily so pure Version B contracts and
unit tests do not require the optional CCXT runtime dependency at import time.
The factory behavior itself remains unchanged.
"""

from __future__ import annotations

import os
from typing import Any


def _load_exchange_classes():
    from .exchange import BinanceExchange
    from .paper_trading import PaperTradingExchange
    return BinanceExchange, PaperTradingExchange


def create_exchange(mode: str = None):
    """Factory function — تُعيد كائن التبادل المناسب حسب TRADING_MODE."""
    mode = (mode or os.getenv("TRADING_MODE", "paper")).lower().strip()

    if mode == "testnet":
        from utils.logger import logger

        logger.critical(
            "❌ TRADING_MODE=testnet غير مدعوم!\n"
            "   Binance Futures Testnet أُوقف في CCXT (يونيو 2025)\n"
            "   ✅ الحل: استخدم TRADING_MODE=paper في ملف .env\n"
            "   Paper Trading يوفر نفس الأمان مع بيانات حقيقية."
        )
        raise EnvironmentError(
            "CCXT Testnet لـ Binance Futures متوقف. استخدم TRADING_MODE=paper"
        )

    BinanceExchange, PaperTradingExchange = _load_exchange_classes()

    if mode == "paper":
        initial_balance = float(os.getenv("PAPER_INITIAL_BALANCE", "10000"))
        return PaperTradingExchange(initial_balance=initial_balance)

    if mode == "live":
        from utils.logger import logger

        logger.warning(
            "⚠️ TRADING_MODE=live — تداول حقيقي بأموال حقيقية!\n"
            "   تأكد أن البوت اجتاز جميع اختبارات Paper Trading أولاً."
        )
        return BinanceExchange(use_testnet=False)

    raise ValueError(
        f"TRADING_MODE='{mode}' غير مدعوم.\n"
        "القيم المقبولة: 'paper' | 'live'"
    )


def __getattr__(name: str) -> Any:
    """Support ``from core import BinanceExchange`` without eager CCXT import."""
    if name == "BinanceExchange":
        return _load_exchange_classes()[0]
    if name == "PaperTradingExchange":
        return _load_exchange_classes()[1]
    raise AttributeError(name)


__all__ = ["BinanceExchange", "PaperTradingExchange", "create_exchange"]
