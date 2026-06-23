# 🚀 دليل التثبيت السريع

## خطوات التثبيت بـ 5 دقائق

### 1️⃣ استنساخ المشروع
```bash
cd /path/to/your/projects
git clone trading_bot
cd trading_bot
```

### 2️⃣ إنشاء بيئة افتراضية
```bash
# على macOS/Linux
python3 -m venv venv
source venv/bin/activate

# على Windows
python -m venv venv
venv\Scripts\activate
```

### 3️⃣ تثبيت المكتبات
```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### 4️⃣ إعداد البيانات المخفية
```bash
# انسخ ملف المثال
cp .env.example .env

# عدّل الملف بمحرر
nano .env
```

### 5️⃣ أدخل البيانات المطلوبة

في ملف `.env`:
```env
# من بينانس (https://www.binance.com/en/account/api-management)
BINANCE_API_KEY=your_api_key_here
BINANCE_SECRET_KEY=your_secret_key_here

# من Testnet (https://testnet.binancefuture.com)
BINANCE_TESTNET_API_KEY=your_testnet_key
BINANCE_TESTNET_SECRET_KEY=your_testnet_secret

# من Telegram (@BotFather)
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id

# البيئة (testnet أو live)
TRADING_ENVIRONMENT=testnet
```

### 6️⃣ اختبر الاتصال
```bash
python test_bot.py
```

يجب أن ترى:
```
✅ جميع المكتبات تم استيرادها
✅ اتصال ناجح بـ Testnet
✅ تم جلب 50 شمعة
✅ المؤشرات محسوبة
✅ أسعار حالية
```

---

## 📋 متطلبات مسبقة

### الحسابات المطلوبة:
- ✅ حساب Binance (مع API Key)
- ✅ حساب Telegram (بوت جديد من @BotFather)
- ✅ Python 3.8+ مثبت

### الملفات المطلوبة:
- ✅ `.env` - انسخه من `.env.example`
- ✅ `config.yaml` - موجود (لا تحتاج تعديل الآن)

---

## 🆘 استكشاف الأخطاء الشائعة

### خطأ: "No module named 'ccxt'"
```bash
# الحل:
pip install ccxt --upgrade
```

### خطأ: "Invalid API Key"
```
# تأكد:
1. API Key من بينانس صحيح (بدون مسافات)
2. نسخته في .env صحيح
3. الـ Secret Key نسخه صحيح أيضاً
```

### خطأ: "Connection refused"
```
# تأكد:
1. الإنترنت متصل
2. جدار الحماية (Firewall) لا يحجب
3. بينانس API نشطة
```

---

## ✅ ما بعد التثبيت

بعد النجاح في test_bot.py:

1. اقرأ `README.md` - الدليل الشامل
2. اقرأ `EXECUTIVE_SUMMARY.md` - ملخص المشروع
3. افهم `config.yaml` - الإعدادات
4. ابدأ من `test_bot.py` - كود بسيط

---

## 🎯 الخطوة التالية

```bash
# 1. اختبر البيانات
python -c "
from core.exchange import BinanceExchange
from data.market_data import MarketData

exchange = BinanceExchange(use_testnet=True)
market_data = MarketData(exchange)
df = market_data.get_complete_dataframe('ETH/USDT', '15m', limit=10)
print(f'✅ جُلبت {len(df)} شمعة')
"

# 2. اختبر الإشارات
# (سيتم إضافته الأسبوع المقبل)

# 3. اختبر التليجرام
# (سيتم إضافته الأسبوع المقبل)
```

---

## 🎉 تم!

الآن أنت جاهز لاستخدام البوت! 🚀

