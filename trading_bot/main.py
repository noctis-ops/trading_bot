"""
═══════════════════════════════════════════════════════════
main.py — نقطة الدخول الرئيسية لبوت التداول
═══════════════════════════════════════════════════════════
غلاف رفيع فوق core.bot.TradingBot:
    ① رسالة ترحيب
    ② معالجة وسائط سطر الأوامر (argparse)
    ③ تأكيد تفاعلي إضافي عند TRADING_MODE=live (طبقة أمان فوق
       create_exchange() نفسها، اتساقاً مع مبدأ المشروع الموثّق في
       BOT_STATUS_REPORT.md: "لا أموال حقيقية في أي مرحلة من مراحل
       التطوير" وREADME.md: "ابدأ بـ Testnet/Paper دائماً")
    ④ تفويض كامل لـ TradingBot لبقية العمل — لا منطق تداول هنا إطلاقاً

الاستخدام:
    python main.py                   ← تشغيل عادي بلا نهاية (Ctrl+C للإيقاف)
    python main.py --status          ← اطبع الحالة الحالية ثم اخرج
    python main.py --iterations 5    ← 5 دورات فقط ثم توقف (اختبار آلي)
    python main.py --balance 5000    ← رصيد ورقي مبدئي مخصص لهذا التشغيل فقط
    python main.py --yes             ← تخطّي تأكيد live التفاعلي (للتشغيل
                                        غير التفاعلي، مثل خدمة systemd لاحقاً)
    python main.py --version         ← معلومات الإصدار

قيد معماري مهم — لماذا لا يوجد وسيط --config:
    config.yaml يُقرأ مرة واحدة عند استيراد كل وحدة (module-level) من
    مسار ثابت (CONFIG_PATH) في جميع ملفات core/ وdata/ وutils/. بحلول
    وقت تنفيذ argparse هنا تكون بعض الوحدات (عبر استيرادات لاحقة) ستقرأ
    نفس الملف الثابت دائماً، فوسيط --config لن يُغيّر شيئاً فعلياً. أي
    تعديل على الإعدادات يجب أن يكون في config.yaml مباشرة قبل التشغيل.

ترتيب التنفيذ داخل main():
    1. تحميل .env (load_dotenv) — قبل أي قراءة لـ os.getenv
    2. تحليل الوسائط — ‎--version يخرج فوراً دون لمس أي شيء آخر
    3. رسالة الترحيب
    4. ‎--balance (إن وُجد) يُطبَّق على PAPER_INITIAL_BALANCE قبل أي بناء
    5. تأكيد live (إن كان TRADING_MODE=live وبدون ‎--yes) — *قبل* بناء
       TradingBot نفسه، حتى لا يحدث أي اتصال حقيقي ببينانس قبل الموافقة
       الصريحة
    6. بناء TradingBot() (مُغلَّف بمعالجة أخطاء صديقة، لا traceback خام)
    7. ‎--status يطبع الحالة ويخرج، وإلا bot.run(max_iterations=...)
"""

import argparse
import os
import sys
from pathlib import Path

# إضافة المسار — نفس نمط test_bot.py بالضبط
sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv


# ─────────────────────────────────────────────────────────
# معالج وسائط سطر الأوامر
# ─────────────────────────────────────────────────────────

def build_arg_parser() -> argparse.ArgumentParser:
    """بناء معالج وسائط سطر الأوامر."""
    parser = argparse.ArgumentParser(
        prog='main.py',
        description=(
            'Trading Bot — بوت تداول آلي على Binance Futures '
            '(Trend Following + Momentum)'
        ),
        epilog='مثال: python main.py --iterations 5   (تشغيل 5 دورات فقط للاختبار)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        '--iterations', type=int, default=None, metavar='N',
        help='إيقاف تلقائي بعد N دورة بدلاً من التشغيل بلا نهاية (للاختبار الآلي فقط).',
    )
    parser.add_argument(
        '--status', action='store_true',
        help='اطبع حالة البوت الحالية (الرصيد، الصفقات المفتوحة) ثم اخرج دون بدء الحلقة.',
    )
    parser.add_argument(
        '--balance', type=float, default=None, metavar='USDT',
        help=(
            'تجاوز PAPER_INITIAL_BALANCE لهذا التشغيل فقط '
            '(Paper Trading فقط — لا يُعدِّل .env، ويُتجاهَل في وضع live).'
        ),
    )
    parser.add_argument(
        '-y', '--yes', action='store_true',
        help="تخطّي تأكيد 'live' التفاعلي — للتشغيل غير التفاعلي (مثل systemd).",
    )
    parser.add_argument(
        '--version', action='store_true',
        help='اطبع معلومات الإصدار والاستراتيجية الحالية ثم اخرج.',
    )
    return parser


# ─────────────────────────────────────────────────────────
# رسائل الترحيب والإصدار
# ─────────────────────────────────────────────────────────

def print_welcome_banner():
    """
    طباعة لافتة الترحيب — أول شيء يراه المستخدم.

    ملاحظة: هذه منفصلة عمداً عن TradingBot._print_startup_banner()
    (التي تظهر لاحقاً عند bot.run() بتفاصيل تشغيلية: الرموز، الأطر
    الزمنية، الرصيد) — هذه فقط لافتة هوية المشروع + وضع التشغيل.
    """
    print("\n" + "╔" + "═" * 68 + "╗")
    print("║" + " " * 68 + "║")
    print("║" + "🤖  Trading Bot — Binance Futures".center(68) + "║")
    print("║" + "Trend Following + Momentum (EMA · RSI · MACD · ADX · ATR)".center(68) + "║")
    print("║" + " " * 68 + "║")
    print("╚" + "═" * 68 + "╝")


def print_version():
    """طباعة معلومات الإصدار — ‎--version. لا تلمس exchange أو الشبكة إطلاقاً."""
    print("\nTrading Bot — Binance Futures")
    print("─" * 42)
    try:
        from core.strategy import TradingStrategy
        s = TradingStrategy()
        print(f"  الاستراتيجية:   {s.name} (v{s.version})")
    except Exception:
        print("  الاستراتيجية:   Trend Following + Momentum")
    print(f"  وضع التداول:    {os.getenv('TRADING_MODE', 'paper')}")
    print(f"  المرجع الرسمي:  BOT_STATUS_REPORT.md")
    print("─" * 42)


# ─────────────────────────────────────────────────────────
# تأكيد أمان إضافي لوضع live
# ─────────────────────────────────────────────────────────

def confirm_live_mode() -> bool:
    """
    طلب تأكيد صريح قبل أي اتصال بوضع live (تداول حقيقي).

    طبقة أمان إضافية فوق تحذير create_exchange() نفسه، تُطبَّق هنا في
    main.py تحديداً لأنه أعلى نقطة تفاعل مع المستخدم في المشروع، قبل
    وصول التنفيذ حتى إلى core/bot.py أو core/exchange.py.

    Returns:
        True فقط إذا كتب المستخدم 'live' بالضبط.
    """
    print("\n" + "🔴" * 34)
    print("⚠️   TRADING_MODE=live — البوت سيتصل بحساب Binance الحقيقي!")
    print("🔴" * 34)
    print("\n  قبل المتابعة، تأكد أن:")
    print("  • أكملت أسبوعاً كاملاً من Paper Trading 24/7 بنجاح (Phase 7)")
    print("  • راجعت إعدادات المخاطر في core/risk_manager.py و config.yaml")
    print("  • ستبدأ برأس مال صغير جداً حسب توصية README.md (20-30$)")
    print("  • فعّلت 2FA على حساب Binance ومفتاح API بدون صلاحية Withdraw")
    print()

    try:
        answer = input("  اكتب 'live' بالضبط للمتابعة، أو اضغط Enter للإلغاء: ").strip()
    except EOFError:
        # لا يوجد stdin تفاعلي (مثل تشغيل غير تفاعلي بدون --yes) — الأمان أولاً
        return False

    return answer == 'live'


# ─────────────────────────────────────────────────────────
# نقطة الدخول
# ─────────────────────────────────────────────────────────

def main():
    load_dotenv()   # قبل أي قراءة لـ os.getenv في هذا الملف

    args = build_arg_parser().parse_args()

    if args.version:
        print_version()
        sys.exit(0)

    print_welcome_banner()

    trading_mode = os.getenv('TRADING_MODE', 'paper').lower().strip()
    print(f"\n  الوضع المكتشف من .env (TRADING_MODE): {trading_mode}")

    # ── تجاوز الرصيد الوهمي لهذا التشغيل فقط (Paper Trading فقط) ──
    if args.balance is not None:
        if trading_mode == 'live':
            print(
                f"  ⚠️  ‎--balance={args.balance} يُتجاهَل — "
                f"غير مؤثر في وضع live (الرصيد الحقيقي من حسابك في Binance)"
            )
        else:
            os.environ['PAPER_INITIAL_BALANCE'] = str(args.balance)
            print(f"  💰 رصيد ورقي مخصص لهذا التشغيل: ${args.balance:,.2f}")

    # ── تأكيد إضافي قبل أي اتصال حقيقي — قبل بناء TradingBot نفسه ──
    if trading_mode == 'live' and not args.yes:
        if not confirm_live_mode():
            print("\n👋 تم الإلغاء — لم يبدأ أي اتصال أو تداول.")
            sys.exit(0)
        print("\n✅ تم التأكيد — المتابعة بوضع LIVE ⚠️")

    # ── بناء TradingBot (مُغلَّف بمعالجة أخطاء صديقة) ──────────
    try:
        from core.bot import TradingBot
        bot = TradingBot()
    except ValueError as e:
        print(f"\n❌ خطأ في الإعدادات: {e}")
        print("   راجع config.yaml و.env قبل إعادة المحاولة.")
        sys.exit(1)
    except EnvironmentError as e:
        # مثال: TRADING_MODE=testnet غير مدعوم (راجع core/__init__.py)
        print(f"\n❌ خطأ في وضع التشغيل: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ فشل تهيئة البوت: {e}")
        print("   💡 تأكد من: .env محرر بشكل صحيح، المكتبات مثبتة "
              "(pip install -r requirements.txt)، والاتصال بالإنترنت متاح.")
        sys.exit(1)

    # ── ‎--status: اطبع واخرج دون بدء الحلقة ─────────────────────
    if args.status:
        bot.print_status()
        sys.exit(0)

    # ── التشغيل الفعلي — التفويض الكامل لـ TradingBot.run() ─────
    bot.run(max_iterations=args.iterations)
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n👋 تم الخروج بواسطة المستخدم")
        sys.exit(0)
    except SystemExit:
        raise   # اسمح لـ sys.exit() الصادرة من main() بالمرور كما هي
    except Exception as e:
        print(f"\n\n❌ خطأ غير متوقع: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
