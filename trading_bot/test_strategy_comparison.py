"""
═══════════════════════════════════════════════════════════
اختبار مقارنة شامل: Backtesting vs Live Trading
═══════════════════════════════════════════════════════════
نفس الاستراتيجية بالضبط في الاثنين
"""

from pathlib import Path
from datetime import datetime
import json

from utils.logger import logger
from core.exchange import BinanceExchange
from backtesting.backtesting_advanced import AdvancedBacktestingEngine

def test_backtesting_only():
    """اختبار Backtesting فقط"""
    print("\n" + "=" * 70)
    print("🧪 الاختبار 1: Backtesting محلي (نفس الاستراتيجية)")
    print("=" * 70)
    
    try:
        # إنشاء محرك Backtesting
        engine = AdvancedBacktestingEngine(initial_balance=10000.0)
        logger.success("✅ تم إنشاء محرك Backtesting")
        
        # توليد البيانات التجريبية
        print("\n📊 توليد البيانات...")
        df_1h, df_15m, df_5m = engine.generate_sample_data(days=60)
        
        if df_1h is None:
            logger.error("❌ فشل توليد البيانات")
            return None
        
        # تشغيل الباكتست
        print("\n💹 تشغيل Backtesting...")
        report = engine.backtest(df_1h, df_15m, df_5m)
        
        # طباعة التقرير
        engine.print_report()
        
        logger.success("\n✅ اختبار Backtesting نجح!")
        
        return report
    
    except Exception as e:
        logger.error(f"❌ فشل Backtesting: {e}")
        return None

def test_live_trading_simulation():
    """محاكاة Live Trading على البيانات الحقيقية"""
    print("\n" + "=" * 70)
    print("🧪 الاختبار 2: محاكاة Live Trading (نفس الاستراتيجية)")
    print("=" * 70)
    
    try:
        logger.warning("⚠️ محاكاة Live Trading باستخدام البيانات الحقيقية من API")
        
        # الاتصال بـ Binance
        print("\n🔗 الاتصال بـ Binance...")
        exchange = BinanceExchange(use_testnet=False)  # Live API
        
        logger.success("✅ تم الاتصال بـ Binance")
        
        # إنشاء محرك Backtesting
        engine = AdvancedBacktestingEngine(initial_balance=1000.0)  # رأس مال صغير
        logger.success("✅ تم إنشاء محرك Backtesting")
        
        # تحميل البيانات الحقيقية
        print("\n📊 تحميل البيانات الحقيقية من API...")
        df_1h, df_15m, df_5m = engine.load_and_prepare_data(exchange, 'BTC/USDT')
        
        if df_1h is None:
            logger.error("❌ فشل تحميل البيانات")
            return None
        
        # جلب الرصيد الحالي
        balance = exchange.get_available_balance()
        logger.success(f"💰 الرصيد الحالي: ${balance:.2f}")
        
        # عرض آخر سعر
        last_price = df_1h.iloc[-1]['close']
        logger.success(f"📈 آخر سعر BTC/USDT: ${last_price:.2f}")
        
        # تشغيل الباكتست على البيانات الحقيقية
        print("\n💹 تشغيل محاكاة Live Trading...")
        report = engine.backtest(df_1h, df_15m, df_5m)
        
        # طباعة التقرير
        engine.print_report()
        
        logger.success("\n✅ اختبار Live Trading نجح!")
        
        return report
    
    except Exception as e:
        logger.error(f"❌ فشل Live Trading: {e}")
        return None

def compare_results(backtest_report: dict, live_report: dict):
    """مقارنة نتائج الاختبارات"""
    if not backtest_report or not live_report:
        logger.warning("⚠️ لا يمكن المقارنة - بيانات ناقصة")
        return
    
    print("\n" + "=" * 70)
    print("📊 مقارنة النتائج: Backtesting vs Live Trading")
    print("=" * 70)
    
    # المقارنة
    metrics = {
        'عدد الصفقات': ('total_trades', 'عدد'),
        'نسبة النجاح': ('win_rate', '%'),
        'إجمالي الربح': ('total_profit_pct', '%'),
        'أقصى خسارة': ('max_loss', '$'),
    }
    
    print(f"\n{'المقياس':<20} {'Backtesting':<20} {'Live Trading':<20} {'الفرق':<15}")
    print("-" * 75)
    
    for label, (key, unit) in metrics.items():
        bt_value = backtest_report.get(key, 0)
        lt_value = live_report.get(key, 0)
        diff = lt_value - bt_value
        
        if unit == '%':
            print(f"{label:<20} {bt_value:>18.2f}% {lt_value:>18.2f}% {diff:>+13.2f}%")
        elif unit == '$':
            print(f"{label:<20} ${bt_value:>17,.2f} ${lt_value:>17,.2f} ${diff:>+12,.2f}")
        else:
            print(f"{label:<20} {int(bt_value):>19} {int(lt_value):>19} {int(diff):>+14}")
    
    print("\n" + "=" * 70)
    
    # التحليل
    print("\n🔍 التحليل:")
    
    bt_wr = backtest_report.get('win_rate', 0)
    lt_wr = live_report.get('win_rate', 0)
    wr_diff = abs(lt_wr - bt_wr)
    
    if wr_diff < 5:
        logger.success("✅ نسبة النجاح متقاربة (فرق < 5%)")
    else:
        logger.warning(f"⚠️ فرق في نسبة النجاح: {wr_diff:.2f}%")
    
    bt_profit = backtest_report.get('total_profit_pct', 0)
    lt_profit = live_report.get('total_profit_pct', 0)
    profit_diff = abs(lt_profit - bt_profit)
    
    if profit_diff < 10:
        logger.success("✅ نسبة الربح متقاربة (فرق < 10%)")
    else:
        logger.warning(f"⚠️ فرق في نسبة الربح: {profit_diff:.2f}%")

def main_menu():
    """القائمة الرئيسية"""
    print("\n" + "╔" + "═" * 68 + "╗")
    print("║" + "🔬 اختبار مقارنة شامل: Backtesting vs Live Trading".center(68) + "║")
    print("║" + "(نفس الاستراتيجية بالضبط)".center(68) + "║")
    print("╚" + "═" * 68 + "╝")
    
    print("\nاختر وضع الاختبار:")
    print("1️⃣  Backtesting فقط (محاكاة محلية - آمن)")
    print("2️⃣  Live Trading (بيانات حقيقية من API)")
    print("3️⃣  كل الاثنين (مقارنة شاملة)")
    print("4️⃣  خروج")
    
    choice = input("\nاختيارك (1-4): ").strip()
    
    if choice == '1':
        print("\n" + "▶️ بدء Backtesting المحلي...")
        report = test_backtesting_only()
        
        if report:
            print("\n💾 حفظ النتائج...")
            with open('backtest_results.json', 'w') as f:
                json.dump(report, f, indent=2)
            logger.success("✅ تم حفظ النتائج في: backtest_results.json")
    
    elif choice == '2':
        print("\n" + "▶️ بدء محاكاة Live Trading...")
        report = test_live_trading_simulation()
        
        if report:
            print("\n💾 حفظ النتائج...")
            with open('live_results.json', 'w') as f:
                json.dump(report, f, indent=2)
            logger.success("✅ تم حفظ النتائج في: live_results.json")
    
    elif choice == '3':
        print("\n" + "▶️ بدء الاختبار الشامل...")
        
        # Backtesting أولاً
        print("\n" + "█" * 70)
        print("المرحلة 1: Backtesting")
        print("█" * 70)
        backtest_report = test_backtesting_only()
        
        # Live Trading ثانياً
        print("\n" + "█" * 70)
        print("المرحلة 2: Live Trading")
        print("█" * 70)
        live_report = test_live_trading_simulation()
        
        # المقارنة
        if backtest_report and live_report:
            print("\n" + "█" * 70)
            print("المرحلة 3: المقارنة")
            print("█" * 70)
            compare_results(backtest_report, live_report)
            
            # حفظ النتائج
            print("\n💾 حفظ النتائج...")
            with open('comparison_results.json', 'w') as f:
                json.dump({
                    'backtesting': backtest_report,
                    'live_trading': live_report,
                    'timestamp': datetime.now().isoformat()
                }, f, indent=2)
            logger.success("✅ تم حفظ النتائج في: comparison_results.json")
    
    elif choice == '4':
        print("\n👋 وداعاً!")
        return
    
    else:
        logger.error("❌ اختيار غير صحيح")

if __name__ == "__main__":
    try:
        main_menu()
    except KeyboardInterrupt:
        print("\n\n👋 تم الخروج بواسطة المستخدم")
    except Exception as e:
        logger.critical(f"❌ خطأ عام: {e}")
