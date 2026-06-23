# 🔧 تعليمات الإصلاح السريع

## المشكلة:
```
❌ خطأ في الاستيراد: No module named 'utils'
```

## السبب:
الملفات موجودة لكن البوت لا يجد مجلدات `core/`, `data/`, `utils/`

---

## ✅ الحل بـ 3 خطوات:

### **خطوة 1: تأكد من البنية**
```
trading_bot/
├── core/
│   ├── __init__.py
│   ├── exchange.py        ← 24KB
│   └── ...
├── data/
│   ├── __init__.py
│   ├── market_data.py     ← 28KB
│   └── ...
├── utils/
│   ├── __init__.py
│   ├── logger.py          ← 11KB
│   └── ...
├── config.yaml
├── requirements.txt
├── .env                    ← أنشئه من .env.example
└── test_bot.py
```

### **خطوة 2: تثبيت المكتبات**
```bash
cd trading_bot
pip install -r requirements.txt
```

### **خطوة 3: إنشاء .env**
```bash
# انسخ:
copy .env.example .env

# ثم عدّل .env بإضافة:
BINANCE_API_KEY=your_key
BINANCE_SECRET_KEY=your_secret
TELEGRAM_BOT_TOKEN=your_token
TELEGRAM_CHAT_ID=your_chat_id
TRADING_ENVIRONMENT=testnet
```

### **خطوة 4: الاختبار**
```bash
python test_bot.py
```

---

## 🆘 إذا فشل الاختبار:

### الخطأ: `No module named 'utils'`
```
✅ الحل: تأكد من أن مجلد utils موجود بجانب test_bot.py
```

### الخطأ: `Invalid API Key`
```
✅ الحل: عدّل .env بالمفاتيح الصحيحة من بينانس
```

### الخطأ: `No module named 'ccxt'`
```bash
pip install ccxt --upgrade
```

### الخطأ: `Connection refused`
```
✅ الحل: تأكد من الإنترنت + استخدم TRADING_ENVIRONMENT=testnet
```

---

## 📋 قائمة التحقق:

- [ ] مجلد `trading_bot/` موجود
- [ ] مجلدات `core/`, `data/`, `utils/` موجودة
- [ ] ملف `.env` محرر بالمفاتيح الصحيحة
- [ ] `pip install -r requirements.txt` شُغّل بنجاح
- [ ] `python test_bot.py` يعمل بدون أخطاء

---

## 🎯 إذا نجح الاختبار:

ستحصل على:
```
✅ جميع المكتبات تم استيرادها
✅ اتصال ناجح بـ Testnet
✅ تم جلب 50 شمعة
✅ المؤشرات محسوبة
✅ جميع الاختبارات نجحت!
```

---

**أسئلة شائعة:**

**س: أين أحصل على API Key؟**
ج: https://www.binance.com/en/account/api-management

**س: أين أحصل على Telegram Token؟**
ج: ابحث عن @BotFather على تليجرام

**س: ماذا لو لم أملك API Key بعد؟**
ج: استخدم TRADING_ENVIRONMENT=testnet (لا يحتاج مال حقيقي)

---

**تم! الآن يجب أن يعمل البوت بدون مشاكل** ✅
