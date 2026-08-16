"""
Notifications module - التنبيهات والرسائل

التصدير (Phase 5.3):
- TelegramNotifier: الواجهة الوحيدة بين TradingBot وTelegram
  (إشعارات فورية + تقارير دورية + أوامر تفاعلية)
"""

from .telegram_bot import TelegramNotifier

__all__ = ['TelegramNotifier']
