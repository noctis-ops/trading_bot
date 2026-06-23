"""
═══════════════════════════════════════════════════════════
نظام التسجيل المتقدم - Logger Module
═══════════════════════════════════════════════════════════
يوفر تسجيل شامل مع ألوان وملفات وتنسيق احترافي
"""

import os
import sys
from datetime import datetime
from pathlib import Path
from loguru import logger as _logger
import yaml

# ─────────────────────────────────────────────────────────
# قراءة الإعدادات
# ─────────────────────────────────────────────────────────

CONFIG_PATH = Path(__file__).parent.parent / 'config.yaml'

with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
    config = yaml.safe_load(f)

logging_config = config.get('logging', {})
log_level = logging_config.get('level', 'INFO')
log_path = logging_config.get('file', {}).get('path', './logs/bot.log')
console_enabled = logging_config.get('console', {}).get('enabled', True)
file_enabled = logging_config.get('file', {}).get('enabled', True)
max_size = logging_config.get('file', {}).get('max_size_mb', 100) * 1024 * 1024
backup_count = logging_config.get('file', {}).get('backup_count', 10)

# ─────────────────────────────────────────────────────────
# إنشاء مجلد السجلات
# ─────────────────────────────────────────────────────────

log_dir = Path(log_path).parent
log_dir.mkdir(parents=True, exist_ok=True)

# ─────────────────────────────────────────────────────────
# إزالة المسجل الافتراضي
# ─────────────────────────────────────────────────────────

_logger.remove()

# ─────────────────────────────────────────────────────────
# تنسيق الرسائل
# ─────────────────────────────────────────────────────────

# تنسيق Console (ملون وجميل)
console_format = (
    "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
    "<level>{message}</level>"
)

# تنسيق File (بدون ألوان)
file_format = (
    "{time:YYYY-MM-DD HH:mm:ss} | "
    "{level: <8} | "
    "{name}:{function}:{line} - "
    "{message}"
)

# ─────────────────────────────────────────────────────────
# إضافة Console Handler
# ─────────────────────────────────────────────────────────

if console_enabled:
    _logger.add(
        sys.stdout,
        format=console_format,
        level=log_level,
        colorize=True,
        backtrace=True,
        diagnose=True
    )

# ─────────────────────────────────────────────────────────
# إضافة File Handler
# ─────────────────────────────────────────────────────────

if file_enabled:
    _logger.add(
        log_path,
        format=file_format,
        level=log_level,
        rotation=max_size,  # تدوير الملف
        retention=backup_count,  # احتفظ بـ N نسخة
        backtrace=True,
        diagnose=True,
        encoding='utf-8'
    )

# ─────────────────────────────────────────────────────────
# Wrapper class للـ Logger
# ─────────────────────────────────────────────────────────

class TradingLogger:
    """
    مسجل متقدم مع دوال مخصصة للتداول
    """
    
    def __init__(self):
        self.logger = _logger
    
    # ─────────────────────────────────────────────────────
    # الدوال الأساسية
    # ─────────────────────────────────────────────────────
    
    def debug(self, message, **kwargs):
        """رسالة تصحيح"""
        self.logger.debug(message, **kwargs)
    
    def info(self, message, **kwargs):
        """رسالة معلومات"""
        self.logger.info(message, **kwargs)
    
    def success(self, message, **kwargs):
        """رسالة نجاح"""
        self.logger.success(message, **kwargs)
    
    def warning(self, message, **kwargs):
        """تحذير"""
        self.logger.warning(message, **kwargs)
    
    def error(self, message, **kwargs):
        """خطأ"""
        self.logger.error(message, **kwargs)
    
    def critical(self, message, **kwargs):
        """خطأ حرج"""
        self.logger.critical(message, **kwargs)
    
    # ─────────────────────────────────────────────────────
    # دوال خاصة بالتداول
    # ─────────────────────────────────────────────────────
    
    def trade_entry(self, symbol, direction, price, qty, leverage, tp1, tp2, sl):
        """تسجيل فتح صفقة"""
        self.logger.success(
            f"🟢 صفقة جديدة | {symbol} {direction.upper()} @ ${price:.2f} "
            f"| qty={qty} | lev={leverage}x | TP1=${tp1:.2f} | TP2=${tp2:.2f} | SL=${sl:.2f}"
        )
    
    def trade_exit(self, symbol, direction, entry, exit_price, pnl, pnl_pct, reason):
        """تسجيل إغلاق صفقة"""
        if pnl > 0:
            self.logger.success(
                f"✅ صفقة مربحة | {symbol} {direction.upper()} "
                f"| IN=${entry:.2f} > OUT=${exit_price:.2f} "
                f"| P&L=${pnl:.2f} ({pnl_pct:+.2f}%) [{reason}]"
            )
        else:
            self.logger.warning(
                f"❌ صفقة خاسرة | {symbol} {direction.upper()} "
                f"| IN=${entry:.2f} > OUT=${exit_price:.2f} "
                f"| P&L=${pnl:.2f} ({pnl_pct:+.2f}%) [{reason}]"
            )
    
    def signal_detected(self, symbol, timeframe, signal_type, conditions_met):
        """رصد إشارة تداول"""
        self.logger.info(
            f"📊 إشارة مرصودة | {symbol} {timeframe} | {signal_type} "
            f"| الشروط: {conditions_met}"
        )
    
    def position_update(self, symbol, unrealized_pnl, pnl_pct, duration):
        """تحديث الصفقة المفتوحة"""
        self.logger.info(
            f"📈 تحديث الصفقة | {symbol} | "
            f"P&L=${unrealized_pnl:+.2f} ({pnl_pct:+.2f}%) | مدة={duration}"
        )
    
    def api_call(self, method, endpoint, status):
        """تسجيل طلبات API"""
        if status == 'success':
            self.logger.debug(f"✓ API {method} {endpoint} - OK")
        else:
            self.logger.warning(f"✗ API {method} {endpoint} - {status}")
    
    def balance_update(self, balance, available, used):
        """تحديث الرصيد"""
        self.logger.info(
            f"💰 الرصيد | الكلي=${balance:.2f} | "
            f"المتاح=${available:.2f} | المستخدم=${used:.2f}"
        )
    
    def bot_status(self, status, trades_count, daily_pnl, uptime):
        """حالة البوت العامة"""
        status_emoji = "🟢" if status == "running" else "🔴"
        self.logger.info(
            f"{status_emoji} حالة البوت | {status.upper()} | "
            f"الصفقات={trades_count} | "
            f"الربح اليومي=${daily_pnl:+.2f} | "
            f"مدة التشغيل={uptime}"
        )
    
    def error_recovery(self, error_type, action_taken):
        """محاولة التعافي من الخطأ"""
        self.logger.warning(
            f"⚠️ معالجة خطأ | نوع={error_type} | "
            f"الإجراء={action_taken}"
        )
    
    def market_analysis(self, symbol, trend, adx, volatility, recommendation):
        """تحليل السوق"""
        self.logger.info(
            f"📈 تحليل {symbol} | اتجاه={trend} | ADX={adx:.2f} | "
            f"التقلب={volatility} | التوصية={recommendation}"
        )

# ─────────────────────────────────────────────────────────
# إنشاء instance عام
# ─────────────────────────────────────────────────────────

logger = TradingLogger()

# ─────────────────────────────────────────────────────────
# رسالة البداية
# ─────────────────────────────────────────────────────────

logger.success("="*60)
logger.success("🚀 نظام التسجيل جاهز")
logger.success(f"📋 مستوى السجلات: {log_level}")
logger.success(f"📁 ملف السجلات: {log_path}")
logger.success("="*60)

# ─────────────────────────────────────────────────────────
# مثال على الاستخدام:
# ─────────────────────────────────────────────────────────
# from utils.logger import logger
#
# logger.trade_entry("ETH/USDT", "long", 3542.50, 0.014, 10, 3621, 3762, 3452)
# logger.bot_status("running", 5, 12.45, "3d 4h 32m")
# logger.signal_detected("ETH/USDT", "15m", "LONG", "EMA + RSI + MACD")
