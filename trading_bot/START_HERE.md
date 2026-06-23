# 🚀 دليل البدء السريع (5 دقائق)

## ✅ **الخطوة 1: التحضير (1 دقيقة)**

```bash
# 1. انتقل لمجلد trading_bot
cd trading_bot

# 2. أنشئ البيئة الافتراضية (إذا لم تكن موجودة)
python -m venv venv

# 3. فعّلها
venv\Scripts\activate  # Windows
# أو
source venv/bin/activate  # Mac/Linux
```

## ✅ **الخطوة 2: التثبيت (2 دقيقة)**

```bash
# قم بالترقية
pip install --upgrade pip

# ثبّت المكتبات
pip install -r requirements.txt
```

## ✅ **الخطوة 3: الإعداد (1 دقيقة)**

```bash
# انسخ ملف المثال
copy .env.example .env  # Windows
# أو
cp .env.example .env    # Mac/Linux

# افتح .env وأضفْ مفاتيحك
# ستحتاج:
# - BINANCE_API_KEY
# - BINANCE_SECRET_KEY
# - TELEGRAM_BOT_TOKEN
# - TELEGRAM_CHAT_ID
```

## ✅ **الخطوة 4: الاختبار (1 دقيقة)**

```bash
python test_bot.py
```

إذا رأيت:
```
✅ جميع الاختبارات نجحت!
🎉 البوت يعمل بشكل صحيح!
```

**تهانينا! البوت جاهز!** 🎉

---

## 📚 الملفات المهمة للقراءة:

| الملف | الوصف | الوقت |
|------|-------|-------|
| **QUICK_FIX.md** | إصلاح الأخطاء | 2 دقيقة |
| **README.md** | الدليل الكامل | 10 دقائق |
| **EXECUTIVE_SUMMARY.md** | ملخص البوت | 5 دقائق |
| **BOT_ASSESSMENT.md** | التقييم والخطط | 10 دقائق |

---

## 🎯 بعد النجاح:

1. **اقرأ EXECUTIVE_SUMMARY.md** - لتفهم ما لديك
2. **اقرأ BOT_ASSESSMENT.md** - لتعرف الخطة التالية
3. **اختبر على Testnet** - لا تحتاج مال حقيقي!
4. **ابدأ التطوير** - الأسبوع المقبل

---

## ❌ إذا حدث خطأ:

```
خطأ: No module named 'utils'
↓
تأكد: هل مجلد utils موجود بجانب test_bot.py؟

خطأ: Invalid API Key
↓
تأكد: هل .env محرر بالمفاتيح الصحيحة؟

خطأ: Connection refused
↓
تأكد: هل الإنترنت متصل؟ TRADING_ENVIRONMENT=testnet؟
```

اقرأ **QUICK_FIX.md** للمزيد.

---

## 💡 نصيحة ذهبية:

**ابدأ بـ Testnet دائماً!**

لا حاجة للمال الحقيقي للاختبار.
بينانس توفر نقود تجريبية مجانية.

```bash
# في .env
TRADING_ENVIRONMENT=testnet  # ✅ آمن وخالي من المخاطر
TRADING_ENVIRONMENT=live     # ❌ استخدم فقط بعد الاختبار الكامل
```

---

## 🚀 أنت الآن جاهز!

**التالي: اقرأ EXECUTIVE_SUMMARY.md**

```bash
cat EXECUTIVE_SUMMARY.md
```

أو افتحه بمحرر النصوص.

---

**كل التوفيق!** ✨🚀
