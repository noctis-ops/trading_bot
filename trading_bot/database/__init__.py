"""
Database module - قاعدة البيانات والتخزين

التصدير (Phase 5.1):
- Trade, Signal, DailyPerformance : نماذج Peewee (الجداول الثلاثة)
- init_db, close_db, get_db_stats : إدارة الاتصال
- db                              : كائن SqliteDatabase الخام (للاستخدام المتقدم)
"""

from .models import (
    Trade,
    Signal,
    DailyPerformance,
    init_db,
    close_db,
    get_db_stats,
    db,
)

__all__ = [
    'Trade',
    'Signal',
    'DailyPerformance',
    'init_db',
    'close_db',
    'get_db_stats',
    'db',
]
