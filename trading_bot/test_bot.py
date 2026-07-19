"""
═══════════════════════════════════════════════════════════
اختبار سريع للبوت - Quick Test Script
═══════════════════════════════════════════════════════════
استخدم هذا الملف للتحقق من أن كل شيء يعمل بشكل صحيح

═══════════════════════════════════════════════════════════
سجل التحديثات
═══════════════════════════════════════════════════════════
النسخة الأصلية:
    اختبرت BinanceExchange(use_testnet=True) مباشرة — أصبحت غير
    صالحة بعد توقف CCXT لدعم Binance Futures Testnet (يونيو 2025).

النسخة الحالية (Phase 4.7):
    تختبر السلسلة الفعلية الكاملة كما هي مبنية اليوم — بدءاً من
    create_exchange() (الوضع الافتراضي: paper، بلا حاجة لمفاتيح API)
    مروراً بـ TradingStrategy وRiskManager وOrderManager، وصولاً إلى
    TradingBot() الكامل (core/bot.py، Phase 4.5). لا تختبر main.py
    مباشرة (Phase 4.6) لأنه غلاف رفيع فوق TradingBot المُختبَر هنا
    بالفعل بشكل مباشر وأدق.

    كل اختبار مستقل قدر الإمكان: فشل جلب بيانات السوق مثلاً لا يوقف
    اختبار الاستراتيجية أو إدارة المخاطر (لا تعتمد على بيانات مجلوبة)،
    فقط اختبارات الاستيراد والاتصال بالبورصة تُوقف التنفيذ بالكامل عند
    الفشل لأن كل شيء آخر يعتمد عليهما.
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
    """اختبار استيراد جميع الوحدات الأساسية (Phase 1-4 كاملة)"""
    print("=" * 60)
    print("🧪 الاختبار 1: استيراد الوحدات")
    print("=" * 60)

    try:
        from utils.logger import logger
        logger.success("✅ utils.logger")

        from core import create_exchange
        logger.success("✅ core (create_exchange)")

        from core.paper_trading import PaperTradingExchange
        logger.success("✅ core.paper_trading")

        from data.market_data import MarketData
        logger.success("✅ data.market_data")

        from core.strategy import TradingStrategy
        logger.success("✅ core.strategy")

        from core.risk_manager import RiskManager
        logger.success("✅ core.risk_manager")

        from core.order_manager import OrderManager
        logger.success("✅ core.order_manager")

        from core.bot import TradingBot
        logger.success("✅ core.bot")

        logger.success("\n✅ جميع وحدات Phase 1-4 تم استيرادها بنجاح!\n")
        return True

    except ImportError as e:
        print(f"❌ خطأ في الاستيراد: {e}")
        print("\n💡 تأكد من: pip install -r requirements.txt")
        return False


def test_exchange_connection():
    """
    اختبار الاتصال بالبورصة — عبر create_exchange() وليس BinanceExchange
    مباشرة. TRADING_MODE=paper هو الافتراضي الآمن ولا يحتاج مفاتيح API.
    """
    print("=" * 60)
    print("🧪 الاختبار 2: الاتصال بالبورصة (create_exchange)")
    print("=" * 60)

    try:
        from utils.logger import logger
        from core import create_exchange

        mode = os.getenv('TRADING_MODE', 'paper')
        logger.info(f"🔗 جاري الاتصال | TRADING_MODE={mode}...")

        exchange = create_exchange()

        logger.success(f"✅ اتصال ناجح | البيئة: {exchange.environment}")
        logger.success(f"💰 الرصيد المتاح: ${exchange.get_available_balance():,.2f}\n")
        return exchange

    except Exception as e:
        print(f"❌ خطأ في الاتصال: {e}")
        print("\n💡 تأكد من:")
        print("  1. TRADING_MODE=paper في .env (أو غير موجود إطلاقاً — الافتراضي آمن)")
        print("  2. الإنترنت متصل (Paper Trading يحتاج بيانات حقيقية من Binance)")
        print("  3. إذا TRADING_MODE=live: تأكد من BINANCE_API_KEY/BINANCE_SECRET_KEY")
        return None


def test_market_data(exchange):
    """اختبار جلب بيانات السوق وتحويلها لـ DataFrame كامل المؤشرات"""
    print("=" * 60)
    print("🧪 الاختبار 3: جلب بيانات السوق")
    print("=" * 60)

    try:
        from utils.logger import logger
        from data.market_data import MarketData

        market_data = MarketData(exchange)

        logger.info("📊 جاري جلب بيانات ETH/USDT...")
        df = market_data.get_complete_dataframe('ETH/USDT', '15m', limit=50)

        if df.empty:
            logger.error("❌ لم يتم جلب بيانات")
            return None

        logger.success(f"✅ تم جلب {len(df)} شمعة\n")

        print("\n📈 معلومات البيانات:")
        market_data.print_dataframe_info(df)

        return df

    except Exception as e:
        print(f"❌ خطأ: {e}")
        return None


def test_indicators(df):
    """
    اختبار حساب المؤشرات على آخر شمعة مكتملة (iloc[-2]) — نفس منطق
    check_buy_signal في core/strategy.py، وليس iloc[-1] التي قد تكون
    شمعة مفتوحة لم تكتمل بعد.
    """
    print("\n" + "=" * 60)
    print("🧪 الاختبار 4: حساب المؤشرات")
    print("=" * 60)

    try:
        from utils.logger import logger

        last_candle = df.iloc[-2]

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


def test_strategy():
    """
    اختبار TradingStrategy — لا يحتاج بيانات مجلوبة، يستخدم نفس القيم
    المرجعية الموثّقة في STRATEGY_COMPLETE_GUIDE.md وcore/strategy.py
    (entry=43250, atr=150 → R:R يجب أن يكون ≈2.17).
    """
    print("=" * 60)
    print("🧪 الاختبار 5: استراتيجية التداول (TradingStrategy)")
    print("=" * 60)

    try:
        from utils.logger import logger
        from core.strategy import TradingStrategy

        strategy = TradingStrategy()
        logger.success(f"✅ {strategy.name} v{strategy.version} جاهزة")

        exits = strategy.calculate_exits(entry_price=43_250.0, atr=150.0)
        rr = exits.get('risk_reward_ratio', 0)

        print(f"\n🎯 التحقق من حساب SL/TP (مرجع: entry=$43,250, atr=150):")
        print(f"  • SL  = ${exits.get('stop_loss', 0):,.2f}")
        print(f"  • TP1 = ${exits.get('take_profit_1', 0):,.2f}")
        print(f"  • TP2 = ${exits.get('take_profit_2', 0):,.2f}")
        print(f"  • R:R = {rr:.2f}:1")

        if not exits.get('valid', False) or rr < strategy.MIN_RISK_REWARD:
            logger.error(
                f"❌ R:R={rr:.2f} أقل من الحد الأدنى {strategy.MIN_RISK_REWARD} "
                f"— راجع core/strategy.py (Bug 3 في BOT_STATUS_REPORT.md)"
            )
            return False

        logger.success(f"✅ R:R={rr:.2f} ≥ {strategy.MIN_RISK_REWARD} — سليم\n")
        return True

    except Exception as e:
        print(f"❌ خطأ: {e}")
        return False


def test_risk_manager():
    """
    اختبار RiskManager — الإنشاء، calculate_stops، calculate_position_size،
    is_trading_allowed. لا يحتاج بيانات مجلوبة ولا اتصال بالبورصة.
    """
    print("=" * 60)
    print("🧪 الاختبار 6: إدارة المخاطر (RiskManager)")
    print("=" * 60)

    try:
        from utils.logger import logger
        from core.risk_manager import RiskManager

        rm = RiskManager(initial_balance=10_000.0)

        stops = rm.calculate_stops(entry_price=43_250.0, atr=150.0)
        if not stops.get('valid', False):
            logger.error("❌ calculate_stops فشلت")
            return False
        logger.success(f"✅ calculate_stops → R:R={stops['risk_reward_ratio']:.2f}")

        size = rm.calculate_position_size(
            balance=10_000.0,
            entry_price=43_250.0,
            stop_loss_price=stops['stop_loss'],
            signal_score=85.0,
        )
        if not size:
            logger.error("❌ calculate_position_size فشلت")
            return False
        logger.success(
            f"✅ calculate_position_size → "
            f"{size['contract_size']:.6f} عقد | رافعة {size['leverage']:.1f}x"
        )

        allowed, reason = rm.is_trading_allowed(current_balance=10_000.0)
        if not allowed:
            logger.error(f"❌ is_trading_allowed أعادت False بشكل غير متوقع: {reason}")
            return False
        logger.success("✅ is_trading_allowed → مسموح (لا خسائر متتالية بعد)\n")
        return True

    except Exception as e:
        print(f"❌ خطأ: {e}")
        return False


def test_order_manager(exchange):
    """
    اختبار OrderManager — الإنشاء واكتشاف وضع paper/live تلقائياً، والتأكد
    أنه يبدأ بدون أي صفقات مفتوحة.
    """
    print("=" * 60)
    print("🧪 الاختبار 7: مدير الأوامر (OrderManager)")
    print("=" * 60)

    try:
        from utils.logger import logger
        from core.risk_manager import RiskManager
        from core.order_manager import OrderManager

        rm = RiskManager(initial_balance=exchange.get_available_balance())
        om = OrderManager(exchange, rm)

        is_paper = hasattr(exchange, 'check_and_trigger_orders')
        logger.success(
            f"✅ OrderManager جاهز | الوضع المكتشف تلقائياً: "
            f"{'📝 Paper Trading' if is_paper else '⚠️ Live Trading'}"
        )

        if om.get_open_positions_count() != 0 or om.has_open_position('BTC/USDT'):
            logger.error("❌ يجب أن يبدأ OrderManager بدون أي صفقات مفتوحة")
            return False

        logger.success("✅ لا صفقات مفتوحة عند البدء (متوقع)\n")
        return True

    except Exception as e:
        print(f"❌ خطأ: {e}")
        return False


def test_full_bot():
    """
    اختبار TradingBot الكامل (Phase 4.5) — يبني السلسلة الكاملة من الصفر
    عبر TradingBot() نفسها (create_exchange → MarketData → TradingStrategy
    → RiskManager → OrderManager)، ثم يُشغّل دورة واحدة فعلية عبر
    run(max_iterations=1).

    ملاحظة: run() مصمَّمة أصلاً لتصمد أمام فشل الشبكة لأي رمز دون رفع
    استثناء (راجع docstring core/bot.py) — لذا نجاح هذا الاختبار يعني
    "التوصيل الداخلي سليم"، وليس بالضرورة "تم العثور على إشارة تداول".
    """
    print("=" * 60)
    print("🧪 الاختبار 8: البوت الكامل (TradingBot)")
    print("=" * 60)

    try:
        from utils.logger import logger
        from core.bot import TradingBot

        bot = TradingBot()
        logger.success(
            f"✅ TradingBot جاهز | الرموز: {', '.join(bot.SYMBOLS)} | "
            f"صفقات متزامنة قصوى: {bot.MAX_CONCURRENT_POSITIONS}"
        )

        bot.print_status()

        logger.info("🔄 تشغيل دورة واحدة فعلية (run(max_iterations=1))...")
        bot.run(max_iterations=1)

        logger.success("✅ اكتملت الدورة كاملة (بدء → فحص → إيقاف) دون أي انهيار\n")
        return True

    except Exception as e:
        print(f"❌ خطأ: {e}")
        return False


def test_price():
    """اختبار الحصول على السعر الحالي لعدة أزواج — عبر create_exchange()"""
    print("=" * 60)
    print("🧪 الاختبار 9: السعر الحالي")
    print("=" * 60)

    try:
        from utils.logger import logger
        from core import create_exchange

        exchange = create_exchange()

        logger.info("💰 جاري جلب الأسعار الحالية...\n")

        symbols = ['BTC/USDT', 'ETH/USDT', 'BNB/USDT']

        for symbol in symbols:
            try:
                ticker = exchange.fetch_ticker(symbol)
                price = ticker.get('close', 0)
                change = ticker.get('percentage', 0) or 0

                emoji = "📈" if change > 0 else "📉"
                print(f"  {emoji} {symbol}: ${price:,.2f} ({change:+.2f}%)")
            except Exception:
                pass

        logger.success("\n✅ تم جلب الأسعار بنجاح!\n")
        return True

    except Exception as e:
        print(f"❌ خطأ: {e}")
        return False


def test_summary(results):
    """ملخص الاختبارات"""
    print("=" * 60)
    print("✅ ملخص الاختبارات")
    print("=" * 60)

    for name, passed in results:
        icon = "✅" if passed else "❌"
        print(f"  {icon} {name}")

    total = len(results)
    passed = sum(1 for _, p in results if p)

    print(f"\n  النتيجة: {passed}/{total} اختبارات نجحت\n")

    if passed == total:
        print("""
🎉 جميع الاختبارات نجحت! Phase 1-4 بالكامل تعمل بشكل صحيح.

الخطوة التالية حسب BOT_STATUS_REPORT.md (المرجع الرسمي):
   • Phase 5: قاعدة البيانات (database/) + إشعارات Telegram
   • Phase 6: دعم Short/Bearish — أولوية استراتيجية عالية
   • Phase 7: تشغيل Live Paper Trading فعلي 24/7 ومراقبة 200+ صفقة

📚 الموارد المفيدة:
   • BOT_STATUS_REPORT.md  - المرجع الرسمي لحالة المشروع (اقرأه أولاً دائماً)
   • STRATEGY_COMPLETE_GUIDE.md - شرح الاستراتيجية بالتفصيل
   • config.yaml - جميع الإعدادات القابلة للتعديل

⚠️ تذكر:
   • TRADING_MODE=paper هو الافتراضي الآمن دائماً — لا تُغيّره بلا داعٍ
   • لا أموال حقيقية في أي مرحلة قبل اكتمال Phase 7 وتقييم النتائج
   • شغّل البوت فعلياً عبر: python main.py
   • راقب السجلات في logs/bot.log
        """)
    else:
        print("⚠️  بعض الاختبارات فشلت — راجع الأخطاء أعلاه قبل المتابعة للمرحلة التالية.\n")


def main():
    """تشغيل جميع الاختبارات بالترتيب، مع صمود جزئي أمام فشل بيانات السوق"""
    print("\n")
    print("╔" + "═" * 58 + "╗")
    print("║" + " " * 58 + "║")
    print("║" + "🤖 اختبار بوت التداول - Trading Bot Test Suite".center(58) + "║")
    print("║" + "(تغطية Phase 1-4 كاملة)".center(58) + "║")
    print("║" + " " * 58 + "║")
    print("╚" + "═" * 58 + "╝")
    print()

    results = []

    # ── الاستيراد: بوابة صارمة — كل شيء آخر يعتمد عليها ──────────
    if not test_imports():
        print("\n❌ فشلت الاختبارات في مرحلة الاستيراد — توقف")
        test_summary([("استيراد الوحدات", False)])
        return
    results.append(("استيراد الوحدات", True))

    # ── الاتصال بالبورصة: بوابة صارمة أيضاً — باقي الاختبارات تحتاجه ──
    exchange = test_exchange_connection()
    results.append(("الاتصال بالبورصة", exchange is not None))
    if not exchange:
        print("\n❌ فشل الاتصال — لا يمكن متابعة باقي الاختبارات")
        test_summary(results)
        return

    # ── بيانات السوق والمؤشرات: فشلها لا يوقف الاختبارات التالية ──────
    df = test_market_data(exchange)
    results.append(("جلب بيانات السوق", df is not None and not df.empty))

    if df is not None and not df.empty:
        results.append(("حساب المؤشرات", test_indicators(df)))
    else:
        print("\n⚠️ تخطّي اختبار المؤشرات (لا توجد بيانات) — نكمل باقي الاختبارات")
        results.append(("حساب المؤشرات", False))

    # ── الاستراتيجية وإدارة المخاطر: مستقلتان تماماً (لا تحتاجان شبكة) ──
    results.append(("استراتيجية التداول", test_strategy()))
    results.append(("إدارة المخاطر", test_risk_manager()))

    # ── مدير الأوامر والبوت الكامل والأسعار: تحتاج exchange فقط ────────
    results.append(("مدير الأوامر", test_order_manager(exchange)))
    results.append(("البوت الكامل (TradingBot)", test_full_bot()))
    results.append(("السعر الحالي", test_price()))

    test_summary(results)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⚠️ تم إيقاف الاختبار من قبل المستخدم")
    except Exception as e:
        print(f"\n\n❌ خطأ غير متوقع: {e}")
        import traceback
        traceback.print_exc()
