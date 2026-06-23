# 🤖 Trading Bot - Binance Futures

بوت تداول متقدم وذكي لتداول العملات الرقمية على بينانس فيوتشر

## 🎯 المميزات

✅ **تداول فيوتشر USDT-M** على بينانس
✅ **تحليل متعدد الأطر الزمنية** (1H، 15M، 5M)
✅ **إدارة مخاطر احترافية** مع حماية رأس المال
✅ **تنبيهات تليجرام فورية** لكل صفقة
✅ **تشغيل 24/7** بدون تدخل بشري
✅ **اختبار على Testnet** آمن قبل التداول الحقيقي
✅ **قابل للتطوير** لمستويات متقدمة
✅ **تسجيل مفصل** لجميع الصفقات والعمليات

---

## 📋 المتطلبات

### 1. حساب بينانس (Binance Account)
- تسجيل على https://www.binance.com
- تفعيل التحقق الثنائي (2FA)
- إعدادات API محدودة لـ Futures فقط

### 2. Python 3.8+
```bash
python --version
```

### 3. مفتاح API من بينانس
- ادخل إلى Account → API Management
- أنشئ مفتاح جديد
- **تأكد من:**
  - ✅ Futures Trading مفعل
  - ✅ Withdraw **معطل** (أمان)
  - ❌ لا تعطِ صلاحيات زائدة

### 4. بوت تليجرام (Telegram Bot)
- ابحث عن `@BotFather` على تليجرام
- اتبع الخطوات لإنشاء بوت جديد
- احفظ الـ Token

---

## 🚀 الإعداد السريع

### الخطوة 1: استنساخ المشروع
```bash
git clone https://github.com/YOUR_USERNAME/trading_bot.git
cd trading_bot
```

### الخطوة 2: إنشاء بيئة افتراضية
```bash
# على macOS/Linux
python3 -m venv venv
source venv/bin/activate

# على Windows
python -m venv venv
venv\Scripts\activate
```

### الخطوة 3: تثبيت المكتبات
```bash
pip install -r requirements.txt
```

### الخطوة 4: إعداد الملفات السرية
```bash
# انسخ ملف المثال
cp .env.example .env

# عدّل الملف بمفاتيحك
nano .env
```

أو على Windows:
```bash
copy .env.example .env
notepad .env
```

### الخطوة 5: ملء بيانات API

في ملف `.env`، أضفْ:

```env
# Binance API
BINANCE_API_KEY=YOUR_ACTUAL_KEY
BINANCE_SECRET_KEY=YOUR_SECRET_KEY

# Binance Testnet (للاختبار)
BINANCE_TESTNET_API_KEY=YOUR_TESTNET_KEY
BINANCE_TESTNET_SECRET_KEY=YOUR_TESTNET_SECRET

# Telegram
TELEGRAM_BOT_TOKEN=YOUR_BOT_TOKEN
TELEGRAM_CHAT_ID=YOUR_CHAT_ID

# البيئة
TRADING_ENVIRONMENT=testnet  # تغيّر إلى 'live' بعد الاختبار
```

---

## 🧪 الاختبار الأول

### 1. اختبار الاتصال
```bash
python -c "
from core.exchange import BinanceExchange
from data.market_data import MarketData

exchange = BinanceExchange(use_testnet=True)
market_data = MarketData(exchange)

# جلب بيانات
df = market_data.get_complete_dataframe('ETH/USDT', '15m', limit=50)
print(f'✅ تم جلب {len(df)} شمعة بنجاح')
"
```

### 2. اختبار بيانات السوق
```bash
python -c "
from core.exchange import BinanceExchange
from data.market_data import MarketData

exchange = BinanceExchange(use_testnet=True)
market_data = MarketData(exchange)

# الحصول على آخر شمعة
candle = market_data.get_last_closed_candle('ETH/USDT', '15m')
print(f'السعر: {candle[\"close\"]}')
print(f'RSI: {candle.get(\"rsi\", \"N/A\")}')
print(f'ADX: {candle.get(\"adx\", \"N/A\")}')
"
```

### 3. اختبار التليجرام
```bash
python -c "
# سيتم إضافة هذا لاحقاً عند إنشاء ملف التليجرام
"
```

---

## 📁 بنية المشروع

```
trading_bot/
├── core/                    # المكونات الأساسية
│   └── exchange.py         # التواصل مع بينانس API
├── data/                   # معالجة البيانات
│   └── market_data.py      # جلب وتحليل البيانات
├── indicators/             # المؤشرات الفنية
│   ├── trend.py           # مؤشرات الاتجاه (EMA, ADX)
│   ├── momentum.py        # مؤشرات الزخم (RSI, MACD)
│   └── volatility.py      # مؤشرات التقلب (ATR, BB)
├── notifications/          # التنبيهات
│   └── telegram_bot.py    # بوت التليجرام
├── database/              # قاعدة البيانات
│   ├── models.py          # نماذج البيانات
│   └── trade_logger.py   # تسجيل الصفقات
├── utils/                 # أدوات مساعدة
│   ├── logger.py         # نظام السجلات
│   ├── calculator.py     # حسابات الحجم والربح
│   └── helpers.py        # دوال مساعدة
├── backtesting/           # اختبار الاستراتيجيات
│   ├── backtest.py       # محرك الاختبار
│   └── performance.py   # مقاييس الأداء
├── config.yaml            # الإعدادات الرئيسية
├── .env.example          # ملف المثال (انسخه إلى .env)
├── requirements.txt      # المكتبات المطلوبة
└── main.py              # نقطة البداية الرئيسية
```

---

## 📊 إعدادات الاستراتيجية

تعديل `config.yaml` لتخصيص الاستراتيجية:

### 1. المؤشرات
```yaml
strategy:
  indicators:
    ema_fast: 50      # متوسط حركة سريع
    ema_slow: 200     # متوسط حركة بطيء
    ema_medium: 21    # متوسط حركة متوسط
```

### 2. الزخم
```yaml
  momentum:
    rsi_period: 14
    rsi_long_entry_min: 50
    rsi_long_entry_max: 70
    macd_fast: 12
    macd_slow: 26
    macd_signal: 9
```

### 3. إدارة المخاطر
```yaml
risk_management:
  risk_percent_per_trade: 2.0   # 2% من الرصيد
  max_daily_loss_percent: 6.0   # إيقاف عند 6% خسارة يومية
  max_consecutive_losses: 3     # إيقاف مؤقت بعد 3 خسائر
  max_leverage: 10              # رافعة أقصى
```

---

## 🔧 استكشاف الأخطاء

### المشكلة: "Invalid API Key"
```
❌ الحل: تأكد من نسخ المفتاح بدقة (بدون مسافات إضافية)
```

### المشكلة: "Network Connection Error"
```
❌ الحل: تحقق من الإنترنت أو جدار الحماية (Firewall)
```

### المشكلة: "البوت لا يُرسل رسائل تليجرام"
```
❌ الحل: 
1. تحقق من TELEGRAM_BOT_TOKEN صحيح
2. أرسل رسالة للبوت على تليجرام أولاً
3. تحقق من TELEGRAM_CHAT_ID صحيح
```

### المشكلة: "No data fetched"
```
❌ الحل:
1. تحقق من أن الزوج موجود (مثل ETH/USDT)
2. تأكد من الإطار الزمني صحيح (15m، 1h، إلخ)
3. جرّب بوت مختلف (BTC/USDT بدلاً من عملة أخرى)
```

---

## 📈 المرحلة التالية

بعد الاختبار الناجح على Testnet:

### الأسبوع 1-2: الاختبار على Testnet
✅ تشغيل البوت 24/7
✅ مراقبة 100+ صفقة
✅ التحقق من دقة المؤشرات
✅ مراجعة التقارير اليومية

### الأسبوع 3: التداول الحقيقي (مبلغ صغير)
✅ تغيير `TRADING_ENVIRONMENT=live`
✅ البدء برصيد صغير (20-30$ فقط)
✅ مراقبة يومية لأسبوع
✅ التأكد من عدم الأخطاء التقنية

### الأسبوع 4+: النمو التدريجي
✅ زيادة الرصيد تدريجياً
✅ إضافة أزواج جديدة
✅ تحسين الاستراتيجية

---

## 📞 الدعم والمساعدة

### الموارد المفيدة
- 📖 [توثيق CCXT](https://docs.ccxt.com)
- 📖 [توثيق Binance API](https://binance-docs.github.io/apidocs)
- 💬 [مجتمع التطوير](https://github.com/ccxt/ccxt)

### المشاكل الشائعة
- تحقق من السجلات: `logs/bot.log`
- استخدم `DEBUG_MODE=true` في `.env` للرسائل المفصلة

---

## ⚠️ تحذيرات أمنية مهمة

🔴 **لا تفعل هذا أبداً:**
- ❌ لا تشارك ملف `.env` مع أحد
- ❌ لا تضعه على GitHub (حتى بدون قصد)
- ❌ لا تستخدم مفتاح مع صلاحيات Withdraw
- ❌ لا تشارك API Keys في قنوات عامة

🟢 **افعل هذا دائماً:**
- ✅ استخدم `.gitignore` لحماية `.env`
- ✅ غيّر المفاتيح شهرياً على الأقل
- ✅ استخدم كلمات مرور قوية لحساب بينانس
- ✅ فعّل 2FA على حسابك
- ✅ راجع السجلات يومياً

---

## 📝 الترخيص

هذا المشروع مرخص تحت MIT License

---

## 🎓 الخلاصة

هذا البوت جاهز للعمل! 🚀

الخطوات التالية:
1. ✅ أكمل الإعداد (config.yaml و .env)
2. ✅ شغّل اختبار الاتصال
3. ✅ اختبر على Testnet
4. ✅ ابدأ بمبلغ صغير عند التداول الحقيقي

**تذكر: التداول محفوف بالمخاطر. ابدأ بمبالغ صغيرة وزد تدريجياً.**

---

**آخر تحديث:** يونيو 2024
**الإصدار:** 1.0.0 (Alpha)
**الحالة:** جاهز للتجربة ✅
