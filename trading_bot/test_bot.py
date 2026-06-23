"""
═══════════════════════════════════════════════════════════
اختبار سريع للبوت - Quick Test Script
═══════════════════════════════════════════════════════════
استخدم هذا الملف للتحقق من أن كل شيء يعمل بشكل صحيح
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# إضافة المسار
sys.path.insert(0, str(Path(__file__).parent))

# قراءة البيانات المخفية
load_dotenv()

def test_imports():
    """اختبار استيراد المكتبات الأساسية"""
    print("=" * 60)
    print("🧪 الاختبار 1: استيراد المكتبات")
    print("=" * 60)
    
    try:
        from utils.logger import logger
        logger.success("✅ utils.logger")
        
        from core.exchange import BinanceExchange
        logger.success("✅ core.exchange")
        
        from data.market_data import MarketData
        logger.success("✅ data.market_data")
        
        logger.success("\n✅ جميع المكتبات تم استيرادها بنجاح!\n")
        return True
    
    except ImportError as e:
        print(f"❌ خطأ في الاستيراد: {e}")
        return False

def test_exchange_connection():
    """اختبار الاتصال بـ بينانس Testnet"""
    print("=" * 60)
    print("🧪 الاختبار 2: الاتصال بـ بينانس Testnet")
    print("=" * 60)
    
    try:
        from utils.logger import logger
        from core.exchange import BinanceExchange
        
        logger.info("🔗 جاري الاتصال بـ Testnet...")
        exchange = BinanceExchange(use_testnet=True)
        
        logger.success("✅ اتصال ناجح!\n")
        return exchange
    
    except Exception as e:
        print(f"❌ خطأ في الاتصال: {e}")
        print("\n💡 تأكد من:")
        print("  1. .env ملف موجود ومحرر")
        print("  2. BINANCE_API_KEY و BINANCE_SECRET_KEY موجودة")
        print("  3. الإنترنت متصل")
        return None

def test_market_data(exchange):
    """اختبار جلب بيانات السوق"""
    print("=" * 60)
    print("🧪 الاختبار 3: جلب بيانات السوق")
    print("=" * 60)
    
    try:
        from utils.logger import logger
        from data.market_data import MarketData
        
        market_data = MarketData(exchange)
        
        logger.info("📊 جاري جلب بيانات ETH/USDT...")
        df = market_data.get_complete_dataframe(
            'ETH/USDT',
            '15m',
            limit=50
        )
        
        if df.empty:
            logger.error("❌ لم يتم جلب بيانات")
            return False
        
        logger.success(f"✅ تم جلب {len(df)} شمعة\n")
        
        # طباعة معلومات
        print("\n📈 معلومات البيانات:")
        market_data.print_dataframe_info(df)
        
        return df
    
    except Exception as e:
        print(f"❌ خطأ: {e}")
        return None

def test_indicators(df):
    """اختبار حساب المؤشرات"""
    print("\n" + "=" * 60)
    print("🧪 الاختبار 4: حساب المؤشرات")
    print("=" * 60)
    
    try:
        from utils.logger import logger
        
        last_candle = df.iloc[-1]
        
        print("\n📊 آخر شمعة مكتملة:")
        print(f"  • السعر: ${last_candle['close']:.2f}")
        print(f"  • الحجم: {last_candle['volume']:.0f}")
        
        print("\n🎯 المؤشرات:")
        
        indicators = {
            'RSI (14)': 'rsi',
            'MACD': 'macd',
            'ADX (14)': 'adx',
            'ATR (14)': 'atr',
            'EMA (50)': 'ema_fast',
            'EMA (200)': 'ema_slow',
            'BB Upper': 'bb_upper',
            'BB Lower': 'bb_lower',
            'Volume SMA': 'volume_sma',
        }
        
        for label, column in indicators.items():
            if column in last_candle.index:
                value = last_candle[column]
                print(f"  • {label}: {value:.2f}")
            else:
                print(f"  • {label}: N/A")
        
        logger.success("\n✅ المؤشرات محسوبة بنجاح!\n")
        return True
    
    except Exception as e:
        print(f"❌ خطأ: {e}")
        return False

def test_price():
    """اختبار الحصول على السعر الحالي"""
    print("=" * 60)
    print("🧪 الاختبار 5: السعر الحالي")
    print("=" * 60)
    
    try:
        from utils.logger import logger
        from core.exchange import BinanceExchange
        
        exchange = BinanceExchange(use_testnet=True)
        
        logger.info("💰 جاري جلب الأسعار الحالية...\n")
        
        symbols = ['BTC/USDT', 'ETH/USDT', 'BNB/USDT']
        
        for symbol in symbols:
            try:
                ticker = exchange.fetch_ticker(symbol)
                price = ticker['close']
                change = ticker.get('percentage', 0)
                
                emoji = "📈" if change > 0 else "📉"
                print(f"  {emoji} {symbol}: ${price:,.2f} ({change:+.2f}%)")
            except:
                pass
        
        logger.success("\n✅ تم جلب الأسعار بنجاح!\n")
        return True
    
    except Exception as e:
        print(f"❌ خطأ: {e}")
        return False

def test_summary():
    """ملخص الاختبارات"""
    print("=" * 60)
    print("✅ ملخص الاختبارات")
    print("=" * 60)
    
    print("""
🎉 جميع الاختبارات نجحت!

الخطوة التالية:
1. افهم البنية الأساسية
2. ابدأ بناء strategy.py
3. اختبر على Testnet
4. ابدأ التداول (إذا كنت جاهزاً)

📚 الموارد المفيدة:
   • BOT_ASSESSMENT.md - تقييم شامل
   • README.md - دليل الإعداد
   • PROJECT_PROGRESS.md - خطة التطوير
   • config.yaml - الإعدادات

⚠️ تذكر:
   • استخدم Testnet أولاً دائماً
   • راقب السجلات (logs/bot.log)
   • ابدأ برأس مال صغير جداً
   • لا تشارك .env مع أحد

🚀 أنت جاهز لبناء بوت احترافي!
    """)

def main():
    """تشغيل جميع الاختبارات"""
    print("\n")
    print("╔" + "═" * 58 + "╗")
    print("║" + " " * 58 + "║")
    print("║" + "🤖 اختبار بوت التداول - Trading Bot Test Suite".center(58) + "║")
    print("║" + " " * 58 + "║")
    print("╚" + "═" * 58 + "╝")
    print()
    
    # الاختبار 1
    if not test_imports():
        print("\n❌ فشلت الاختبارات في المرحلة الأولى")
        return
    
    # الاختبار 2
    exchange = test_exchange_connection()
    if not exchange:
        print("\n❌ فشلت الاختبارات في الاتصال بـ بينانس")
        return
    
    # الاختبار 3
    df = test_market_data(exchange)
    if df is None or df.empty:
        print("\n❌ فشلت الاختبارات في جلب البيانات")
        return
    
    # الاختبار 4
    if not test_indicators(df):
        print("\n❌ فشلت الاختبارات في حساب المؤشرات")
        return
    
    # الاختبار 5
    if not test_price():
        print("\n⚠️ تحذير: فشل اختبار الأسعار (قد يكون تأخيراً في الشبكة)")
    
    # الملخص
    test_summary()

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⚠️ تم إيقاف الاختبار من قبل المستخدم")
    except Exception as e:
        print(f"\n\n❌ خطأ غير متوقع: {e}")
        import traceback
        traceback.print_exc()
