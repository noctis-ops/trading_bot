"""
ملف تشخيص سريع لمشكلة المفاتيح
"""

import os
from pathlib import Path
from dotenv import load_dotenv

print("=" * 60)
print("🔍 تشخيص ملف .env والمفاتيح")
print("=" * 60)

# 1. تحقق من وجود ملف .env
env_path = Path('.env')
if env_path.exists():
    print(f"✅ ملف .env موجود: {env_path.absolute()}")
else:
    print(f"❌ ملف .env غير موجود في: {Path.cwd()}")
    print(f"المسار الحالي: {Path.cwd()}")
    exit(1)

# 2. قراءة الملف
print("\n" + "=" * 60)
print("📄 محتوى ملف .env:")
print("=" * 60)

with open('.env', 'r') as f:
    lines = f.readlines()
    for i, line in enumerate(lines, 1):
        if '=' in line:
            key, value = line.split('=', 1)
            # إخفاء القيم الحساسة
            value_display = value.strip()
            if len(value_display) > 5:
                value_display = value_display[:5] + "***" + value_display[-5:]
            print(f"{i}. {key}={value_display}")

# 3. حمّل المتغيرات
load_dotenv()

# 4. تحقق من المفاتيح
print("\n" + "=" * 60)
print("🔑 التحقق من المفاتيح:")
print("=" * 60)

# LIVE Keys
api_key_live = os.getenv('BINANCE_API_KEY')
secret_key_live = os.getenv('BINANCE_SECRET_KEY')

# TESTNET Keys
api_key_testnet = os.getenv('BINANCE_TESTNET_API_KEY')
secret_key_testnet = os.getenv('BINANCE_TESTNET_SECRET_KEY')

print(f"\n1️⃣  BINANCE_API_KEY: {'✅ موجود' if api_key_live else '❌ غير موجود'}")
if api_key_live:
    print(f"   الطول: {len(api_key_live)} حرف")
    print(f"   البداية: {api_key_live[:10]}...")
    print(f"   بدون مسافات: {api_key_live.strip() == api_key_live}")

print(f"\n2️⃣  BINANCE_SECRET_KEY: {'✅ موجود' if secret_key_live else '❌ غير موجود'}")
if secret_key_live:
    print(f"   الطول: {len(secret_key_live)} حرف")
    print(f"   البداية: {secret_key_live[:10]}...")
    print(f"   بدون مسافات: {secret_key_live.strip() == secret_key_live}")

print(f"\n3️⃣  BINANCE_TESTNET_API_KEY: {'✅ موجود' if api_key_testnet else '❌ غير موجود'}")
if api_key_testnet:
    print(f"   الطول: {len(api_key_testnet)} حرف")

print(f"\n4️⃣  BINANCE_TESTNET_SECRET_KEY: {'✅ موجود' if secret_key_testnet else '❌ غير موجود'}")
if secret_key_testnet:
    print(f"   الطول: {len(secret_key_testnet)} حرف")

# 5. اختبر CCXT
print("\n" + "=" * 60)
print("🧪 اختبار CCXT:")
print("=" * 60)

try:
    import ccxt
    
    # حدد أي المفاتيح سنستخدم
    use_key = api_key_testnet if api_key_testnet else api_key_live
    use_secret = secret_key_testnet if secret_key_testnet else secret_key_live
    
    if not use_key or not use_secret:
        print("❌ لا توجد مفاتيح على الإطلاق!")
        exit(1)
    
    print(f"📍 استخدام: {'TESTNET' if api_key_testnet else 'LIVE'} Keys")
    
    exchange = ccxt.binance({
        'apiKey': use_key.strip(),
        'secret': use_secret.strip(),
        'enableRateLimit': True,
        'options': {
            'defaultType': 'future',
            'recvWindow': 10000,
        }
    })
    
    # إذا كان Testnet
    if api_key_testnet:
        exchange.set_sandbox_mode(True)
        exchange.urls['api'] = {
            'public': 'https://testnet.binancefuture.com/fapi',
            'private': 'https://testnet.binancefuture.com/fapi',
        }
        print("✅ تعيين Testnet URLs")
    
    # اختبر الاتصال
    try:
        ticker = exchange.fetch_ticker('BTC/USDT')
        print(f"✅ جلب BTC/USDT: ${ticker['close']:.2f}")
    except Exception as e:
        print(f"⚠️ خطأ في جلب البيانات: {e}")
    
    # اختبر المصادقة
    try:
        balance = exchange.fetch_balance()
        print(f"✅ جلب الرصيد بنجاح")
        if 'USDT' in balance:
            usdt = balance['USDT'].get('free', 0)
            print(f"💰 الرصيد: ${usdt:.2f}")
    except ccxt.AuthenticationError as e:
        print(f"❌ خطأ مصادقة: {e}")
        print("   تأكد من أن المفاتيح صحيحة!")
    except Exception as e:
        print(f"⚠️ خطأ آخر: {e}")

except Exception as e:
    print(f"❌ خطأ عام: {e}")

print("\n" + "=" * 60)
print("✅ انتهى التشخيص")
print("=" * 60)
