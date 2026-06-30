"""
═══════════════════════════════════════════════════════════
استراتيجية التداول الأساسية
═══════════════════════════════════════════════════════════
Multi-Timeframe Trend Following + Momentum
(نفس الاستراتيجية المستخدمة في البوت والـ Backtesting)

═══════════════════════════════════════════════════════════
سجل التغييرات:
═══════════════════════════════════════════════════════════
v1.1 (إصلاح الأخطاء):
  - ADX threshold:    20  → 25   (توافق مع STRATEGY_COMPLETE_GUIDE.md)
  - RSI range:       45-70→ 50-70 (توافق مع STRATEGY_COMPLETE_GUIDE.md)
  - EMA distance:    3.0%→ 2.0%  (توافق مع STRATEGY_COMPLETE_GUIDE.md)
  - TP2 multiplier:  3.5 → 4.5   (إصلاح: R:R كان 1.83 < 2.0 دائماً!)
    محاسبة جديدة:
      Risk   = ATR × 1.5
      Reward = (ATR×2.0×0.5) + (ATR×4.5×0.5) = 3.25×ATR
      R:R    = 3.25/1.5 = 2.17 ✓ (فوق الحد الأدنى 2.0)
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Tuple, Optional
from datetime import datetime

from utils.logger import logger


class TradingStrategy:
    """
    استراتيجية Trend Following + Momentum

    الأطر الزمنية:
    - 1H:  الاتجاه الرئيسي
    - 15M: الإشارة والتأكيد
    - 5M:  تفاصيل إضافية

    المؤشرات:
    - EMA(200, 50, 21) — اتجاه
    - ADX(14) > 25     — قوة الاتجاه
    - RSI(14): 50-70   — زخم (ليس ذروة شراء)
    - MACD(12,26,9)    — تأكيد الزخم
    - ATR(14)          — حساب SL/TP
    - Volume > SMA20   — تأكيد الحجم
    """

    # ── ثوابت الاستراتيجية ──────────────────────────────
    # معاملات SL/TP (بوحدات ATR)
    ATR_SL_MULT  = 1.5   # Stop Loss  = Entry − ATR×1.5
    ATR_TP1_MULT = 2.0   # Take Profit 1 = Entry + ATR×2.0  (50% من الصفقة)
    ATR_TP2_MULT = 4.5   # Take Profit 2 = Entry + ATR×4.5  (50% من الصفقة)
    #                       ↑ تم تصحيحه من 3.5 إلى 4.5 لضمان R:R ≥ 2.0
    #                       R:R = (2.0×0.5 + 4.5×0.5) / 1.5 = 3.25/1.5 = 2.17 ✓

    MIN_RISK_REWARD = 2.0      # الحد الأدنى المقبول لنسبة R:R
    RISK_PER_TRADE  = 0.02     # 2% من الرصيد كحد أقصى للمخاطرة
    MAX_LEVERAGE    = 10       # أقصى رافعة مسموحة

    # معاملات الفلترة
    ADX_THRESHOLD       = 25   # ADX يجب أن يكون > 25 (اتجاه قوي)
    RSI_MIN             = 50   # RSI لا يقل عن 50 (في منطقة الصعود)
    RSI_MAX             = 70   # RSI لا يتجاوز 70 (تجنب ذروة الشراء)
    MAX_EMA_DISTANCE    = 2.0  # السعر لا يبعد أكثر من 2% عن EMA21

    def __init__(self):
        """تهيئة الاستراتيجية"""
        self.name    = "Trend Following + Momentum"
        self.version = "1.1"
        logger.success(f"✅ استراتيجية جاهزة: {self.name} v{self.version}")

    # ═══════════════════════════════════════════════════
    # التحقق من إشارة الشراء
    # ═══════════════════════════════════════════════════

    def check_buy_signal(
        self,
        df_1h:  pd.DataFrame,
        df_15m: pd.DataFrame,
        df_5m:  pd.DataFrame
    ) -> Tuple[bool, Dict]:
        """
        التحقق من 6 شروط الشراء (جميعها مطلوبة)

        Args:
            df_1h:  DataFrame بيانات الإطار الساعي
            df_15m: DataFrame بيانات 15 دقيقة
            df_5m:  DataFrame بيانات 5 دقائق

        Returns:
            (True, signal_data) إذا تحققت جميع الشروط
            (False, {})         إذا لم تتحقق
        """
        try:
            # ── الحد الأدنى: 21 شمعة للمؤشرات ──────────
            if (len(df_1h) < 21 or
                    len(df_15m) < 21 or
                    len(df_5m) < 21):
                return False, {'reason': 'بيانات غير كافية (< 21 شمعة)'}

            # ── نحتاج على الأقل شمعتين للـ iloc[-2] ─────
            if (len(df_1h) < 2 or
                    len(df_15m) < 2 or
                    len(df_5m) < 2):
                return False, {'reason': 'بيانات ناقصة (< 2 شمعة)'}

            # ── نأخذ آخر شمعة مكتملة (مغلقة) ───────────
            # iloc[-1] = الشمعة الحالية المفتوحة (غير مكتملة)
            # iloc[-2] = آخر شمعة مكتملة ← هذه التي نحللها
            candle_1h  = df_1h.iloc[-2]
            candle_15m = df_15m.iloc[-2]
            candle_5m  = df_5m.iloc[-2]

            conditions = {}

            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # 1️⃣  الاتجاه الرئيسي على 1H
            #     السعر يجب أن يكون فوق EMA200
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            conditions['1h_price_above_ema200'] = (
                candle_1h['close'] > candle_1h['ema_slow']
            )

            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # 2️⃣  EMA50 فوق EMA200 على 1H (Golden Cross)
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            conditions['1h_ema50_above_ema200'] = (
                candle_1h['ema_fast'] > candle_1h['ema_slow']
            )

            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # 3️⃣  قوة الاتجاه على 15M
            #     ADX > 25 = اتجاه قوي (وليس Sideways)
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            adx_value = candle_15m.get('adx', 0)
            conditions['adx_above_25'] = (adx_value > self.ADX_THRESHOLD)

            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # 4️⃣  تأكيد الحجم على 15M
            #     الحجم الحالي > متوسط 20 شمعة
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            conditions['volume_above_sma20'] = (
                candle_15m['volume'] > candle_15m.get('volume_sma', 0)
            )

            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # 5️⃣  مؤشر RSI على 15M في النطاق 50-70
            #     50+  = في منطقة الصعود (زخم إيجابي)
            #     ≤70  = لم يصل ذروة الشراء بعد
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            rsi_value = candle_15m.get('rsi', 50)
            conditions['rsi_in_range_50_70'] = (
                self.RSI_MIN <= rsi_value <= self.RSI_MAX
            )

            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # 6️⃣  تقاطع MACD على 15M
            #     MACD فوق خط الإشارة = زخم صاعد
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            macd_val   = candle_15m.get('macd',        0)
            signal_val = candle_15m.get('macd_signal', 0)
            conditions['macd_above_signal'] = (macd_val > signal_val)

            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # [إضافي]  المسافة من EMA21 ≤ 2%
            #     تجنب الدخول عندما يكون السعر بعيداً جداً
            #     (مبالغة في الحركة = خطر تصحيح وشيك)
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            ema21_val = candle_15m.get('ema_medium',
                                       candle_15m.get('ema_fast',
                                                      candle_15m['close']))
            if ema21_val and ema21_val > 0:
                distance_pct = (
                    abs(candle_15m['close'] - ema21_val) / ema21_val * 100
                )
                conditions['price_near_ema21'] = (
                    distance_pct <= self.MAX_EMA_DISTANCE
                )
            else:
                conditions['price_near_ema21'] = True  # تجاهل إذا لا يوجد EMA

            # ── التحقق من جميع الشروط ────────────────────
            all_conditions_met = all(conditions.values())

            if all_conditions_met:
                signal_data = {
                    'signal':      'BUY',
                    'entry_price': candle_15m['close'],
                    'atr':         candle_15m.get('atr', 0),
                    'rsi':         rsi_value,
                    'adx':         adx_value,
                    'conditions':  conditions,
                }
                return True, signal_data

            return False, {'conditions': conditions}

        except Exception as e:
            logger.error(f"❌ خطأ في فحص شرط الشراء: {e}")
            return False, {}

    # ═══════════════════════════════════════════════════
    # حساب حجم الصفقة
    # ═══════════════════════════════════════════════════

    def calculate_position_size(
        self,
        balance:          float,
        entry_price:      float,
        stop_loss_price:  float,
        max_leverage:     int = None
    ) -> Dict:
        """
        حساب حجم الصفقة الديناميكي

        الصيغة:
            risk_amount       = balance × 2%
            stop_distance_pct = (entry - SL) / entry
            position_notional = risk_amount / stop_distance_pct
            leverage          = min(position_notional / balance, max_leverage)
            contract_size     = position_notional / entry_price

        Args:
            balance:         الرصيد المتاح (USDT)
            entry_price:     سعر الدخول
            stop_loss_price: سعر وقف الخسارة
            max_leverage:    الرافعة القصوى (افتراضي: 10)

        Returns:
            dict بجميع معاملات حجم الصفقة
        """
        try:
            if max_leverage is None:
                max_leverage = self.MAX_LEVERAGE

            # التحقق من المدخلات
            if balance <= 0 or entry_price <= 0 or stop_loss_price <= 0:
                logger.warning("⚠️ مدخلات غير صالحة في calculate_position_size")
                return {}

            if stop_loss_price >= entry_price:
                logger.warning(
                    "⚠️ stop_loss_price يجب أن يكون أقل من entry_price"
                )
                return {}

            # 1. مبلغ المخاطرة (2% من الرصيد)
            risk_amount = balance * self.RISK_PER_TRADE

            # 2. المسافة للـ Stop Loss كنسبة مئوية
            stop_distance     = entry_price - stop_loss_price
            stop_distance_pct = stop_distance / entry_price

            if stop_distance_pct <= 0:
                return {}

            # 3. حجم الصفقة بالـ USDT
            position_notional = risk_amount / stop_distance_pct

            # 4. الرافعة المطلوبة (محدودة بـ max_leverage)
            leverage = min(position_notional / balance, max_leverage)
            leverage = max(1.0, leverage)  # حد أدنى: 1x

            # 5. عدد العقود
            contract_size = position_notional / entry_price

            return {
                'risk_amount':       risk_amount,
                'stop_distance':     stop_distance,
                'stop_distance_pct': stop_distance_pct * 100,
                'position_notional': position_notional,
                'leverage':          leverage,
                'contract_size':     contract_size,
            }

        except Exception as e:
            logger.error(f"❌ خطأ في حساب حجم الصفقة: {e}")
            return {}

    # ═══════════════════════════════════════════════════
    # حساب نقاط الخروج
    # ═══════════════════════════════════════════════════

    def calculate_exits(self, entry_price: float, atr: float) -> Dict:
        """
        حساب نقاط SL و TP باستخدام ATR

        الصيغة:
            SL  = Entry − ATR × 1.5
            TP1 = Entry + ATR × 2.0  ← 50% من الصفقة
            TP2 = Entry + ATR × 4.5  ← 50% من الصفقة
                  ↑ تم تصحيحه من 3.5 إلى 4.5

        R:R = [(TP1-Entry)×0.5 + (TP2-Entry)×0.5] / (Entry-SL)
            = [ATR×1.0 + ATR×2.25] / ATR×1.5
            = 3.25 / 1.5 = 2.17 ✓

        Args:
            entry_price: سعر الدخول
            atr:         قيمة ATR الحالية

        Returns:
            dict بـ SL و TP1 و TP2 ونسبة R:R
        """
        try:
            if entry_price <= 0 or atr <= 0:
                return {'valid': False, 'reason': 'سعر أو ATR صفري'}

            stop_loss    = entry_price - (atr * self.ATR_SL_MULT)
            take_profit1 = entry_price + (atr * self.ATR_TP1_MULT)
            take_profit2 = entry_price + (atr * self.ATR_TP2_MULT)

            # حساب R:R
            risk   = entry_price - stop_loss  # = atr × 1.5
            reward = (
                (take_profit1 - entry_price) * 0.5 +
                (take_profit2 - entry_price) * 0.5
            )
            # = (atr×2.0×0.5) + (atr×4.5×0.5)
            # = atr×1.0 + atr×2.25 = atr×3.25

            risk_reward = reward / risk if risk > 0 else 0
            # = 3.25 × atr / 1.5 × atr = 2.17 ✓

            return {
                'stop_loss':         stop_loss,
                'take_profit_1':     take_profit1,
                'take_profit_2':     take_profit2,
                'risk':              risk,
                'reward':            reward,
                'risk_reward_ratio': risk_reward,
                'valid':             risk_reward >= self.MIN_RISK_REWARD,
            }

        except Exception as e:
            logger.error(f"❌ خطأ في حساب نقاط الخروج: {e}")
            return {'valid': False}

    # ═══════════════════════════════════════════════════
    # التحقق من شروط الخروج
    # ═══════════════════════════════════════════════════

    def should_exit(
        self,
        current_price:  float,
        stop_loss:      float,
        take_profit_1:  float,
        take_profit_2:  float
    ) -> Tuple[bool, Optional[str], Optional[float]]:
        """
        التحقق من شروط الخروج

        Returns:
            (should_exit, reason, exit_price)
            reason: 'STOP_LOSS' | 'TAKE_PROFIT_1' | 'TAKE_PROFIT_2'
        """
        if stop_loss and current_price <= stop_loss:
            return True, 'STOP_LOSS', stop_loss

        if take_profit_2 and current_price >= take_profit_2:
            return True, 'TAKE_PROFIT_2', take_profit_2

        if take_profit_1 and current_price >= take_profit_1:
            return True, 'TAKE_PROFIT_1', take_profit_1

        return False, None, None

    # ═══════════════════════════════════════════════════
    # التحقق من صحة الصفقة قبل التنفيذ
    # ═══════════════════════════════════════════════════

    def validate_trade(self, trade: Dict) -> bool:
        """
        التحقق من صحة جميع معاملات الصفقة

        Args:
            trade: dict يحتوي على معاملات الصفقة

        Returns:
            True إذا كانت الصفقة صالحة، False إذا لم تكن
        """
        try:
            required = ['entry_price', 'stop_loss', 'take_profit_1', 'contract_size']

            for field in required:
                if field not in trade or trade[field] is None:
                    logger.warning(f"⚠️ حقل مفقود في الصفقة: {field}")
                    return False

            if trade['entry_price'] <= 0:
                logger.warning("⚠️ سعر الدخول غير منطقي")
                return False

            if trade['contract_size'] <= 0:
                logger.warning("⚠️ حجم العقد غير منطقي")
                return False

            if trade['stop_loss'] >= trade['entry_price']:
                logger.warning(
                    "⚠️ Stop Loss يجب أن يكون أقل من Entry Price "
                    f"(SL={trade['stop_loss']:.2f} >= Entry={trade['entry_price']:.2f})"
                )
                return False

            return True

        except Exception as e:
            logger.error(f"❌ خطأ في التحقق من الصفقة: {e}")
            return False

    # ═══════════════════════════════════════════════════
    # طباعة تفاصيل الإشارة
    # ═══════════════════════════════════════════════════

    def print_signal(self, signal_data: Dict):
        """طباعة تفاصيل إشارة الشراء بشكل منسق"""
        print("\n" + "═" * 60)
        print("📊 إشارة شراء تم اكتشافها!")
        print("═" * 60)

        print("\n💡 الشروط:")
        for condition, passed in signal_data.get('conditions', {}).items():
            icon = "✅" if passed else "❌"
            print(f"   {icon} {condition}")

        print(f"\n💰 بيانات الإشارة:")
        print(f"   • سعر الدخول: ${signal_data.get('entry_price', 0):,.2f}")
        print(f"   • ATR:         {signal_data.get('atr', 0):.2f}")
        print(f"   • RSI:         {signal_data.get('rsi', 0):.1f}")
        print(f"   • ADX:         {signal_data.get('adx', 0):.1f}")

        # حساب SL/TP إذا كان ATR متاحاً
        atr = signal_data.get('atr', 0)
        if atr > 0:
            entry  = signal_data.get('entry_price', 0)
            exits  = self.calculate_exits(entry, atr)
            print(f"\n🎯 نقاط الخروج:")
            print(f"   • SL:  ${exits.get('stop_loss', 0):,.2f}")
            print(f"   • TP1: ${exits.get('take_profit_1', 0):,.2f}")
            print(f"   • TP2: ${exits.get('take_profit_2', 0):,.2f}")
            print(f"   • R:R: {exits.get('risk_reward_ratio', 0):.2f}:1")

        print("═" * 60)


# ─────────────────────────────────────────────────────────
# اختبار سريع عند التشغيل المباشر
# ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    strategy = TradingStrategy()

    # التحقق من حساب R:R
    exits = strategy.calculate_exits(entry_price=43250, atr=150)
    print(f"\n🧮 التحقق من R:R:")
    print(f"   SL:  ${exits['stop_loss']:,.2f}")
    print(f"   TP1: ${exits['take_profit_1']:,.2f}")
    print(f"   TP2: ${exits['take_profit_2']:,.2f}")
    print(f"   R:R: {exits['risk_reward_ratio']:.2f}:1  "
          f"({'✅ فوق الحد الأدنى 2.0' if exits['valid'] else '❌ تحت الحد الأدنى'})")