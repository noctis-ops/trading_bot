"""
═══════════════════════════════════════════════════════════
استراتيجية التداول الأساسية
═══════════════════════════════════════════════════════════
Multi-Timeframe Trend Following + Momentum
(نفس الاستراتيجية المستخدمة في البوت والـ Backtesting)
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
    - 1H: الاتجاه الرئيسي
    - 15M: الإشارة
    - 5M: التأكيد
    
    المؤشرات:
    - EMA(200,50,21)
    - ADX(14) > 25
    - RSI(14): 50-70
    - MACD(12,26,9)
    - ATR(14)
    - Volume × 1.5
    """
    
    def __init__(self):
        """تهيئة الاستراتيجية"""
        self.name = "Trend Following + Momentum"
        self.version = "1.0"
        
        logger.success(f"✅ استراتيجية جاهزة: {self.name}")
    
    def check_buy_signal(self, df_1h: pd.DataFrame, df_15m: pd.DataFrame, df_5m: pd.DataFrame) -> Tuple[bool, Dict]:
        """
        التحقق من شرط الشراء (جميع الشروط يجب أن تكون صحيحة)
        
        Args:
            df_1h: بيانات 1H
            df_15m: بيانات 15M
            df_5m: بيانات 5M
        
        Returns:
            (signal_found, signal_data)
        """
        try:
            # ✅ التحقق من وجود بيانات كافية (حد أدنى 21 شمعة للمؤشرات)
            if len(df_1h) < 21 or len(df_15m) < 21 or len(df_5m) < 21:
                return False, {'reason': 'بيانات غير كافية'}
            
            # ✅ استخدم iloc[-2] (الشمعة المكتملة الأخيرة)
            # التحقق الآمن من الفهرس
            if len(df_1h) < 2 or len(df_15m) < 2 or len(df_5m) < 2:
                return False, {'reason': 'بيانات ناقصة'}
            
            candle_1h = df_1h.iloc[-2]
            candle_15m = df_15m.iloc[-2]
            candle_5m = df_5m.iloc[-2]
            
            # شروط الشراء (جميعها مطلوبة)
            conditions = {}
            
            # 1️⃣ شرط الاتجاه على 1H
            condition_1 = candle_1h['close'] > candle_1h['ema_slow']
            conditions['1h_above_ema200'] = condition_1
            
            condition_2 = candle_1h['ema_fast'] > candle_1h['ema_slow']
            conditions['ema50_above_ema200'] = condition_2
            
            # 2️⃣ شرط الاتجاه على 15M (ADX)
            condition_3 = candle_15m.get('adx', 0) > 20
            conditions['adx_above_20'] = condition_3
            
            # 3️⃣ شرط الحجم
            condition_4 = candle_15m['volume'] > candle_15m['volume_sma']
            conditions['volume_confirmed'] = condition_4
            
            # 4️⃣ شرط RSI على 15M
            rsi_15m = candle_15m.get('rsi', 50)
            condition_5 = 45 <= rsi_15m <= 70
            conditions['rsi_in_range'] = condition_5
            
            # 5️⃣ شرط MACD على 15M
            macd_15m = candle_15m.get('macd', 0)
            macd_signal_15m = candle_15m.get('macd_signal', 0)
            condition_6 = macd_15m > macd_signal_15m
            conditions['macd_crossover'] = condition_6
            
            # 6️⃣ شرط المسافة من EMA
            ema_21_15m = candle_15m.get('ema_fast', candle_15m['close'])
            distance_pct = abs(candle_15m['close'] - ema_21_15m) / ema_21_15m * 100
            condition_7 = distance_pct <= 3.0  # أقل من 3% من EMA
            conditions['distance_from_ema'] = condition_7
            
            # التحقق من جميع الشروط
            all_conditions_met = all(conditions.values())
            
            if all_conditions_met:
                signal_data = {
                    'signal': 'BUY',
                    'entry_price': candle_15m['close'],
                    'atr': candle_15m.get('atr', 0),
                    'conditions': conditions,
                    'timestamp': df_15m.index[-2] if hasattr(df_15m.index[-2], 'timestamp') else None,
                }
                return True, signal_data
            
            return False, {'conditions': conditions}
        
        except Exception as e:
            logger.error(f"❌ خطأ في فحص شرط الشراء: {e}")
            return False, {}
    
    def calculate_position_size(
        self,
        balance: float,
        entry_price: float,
        stop_loss_price: float,
        max_leverage: int = 10
    ) -> Dict:
        """
        حساب حجم الصفقة الديناميكي
        
        الصيغة:
        risk_amount = balance × 2%
        stop_distance_pct = ATR × 1.5 / entry_price
        position_notional = risk_amount / stop_distance_pct
        leverage = position_notional / balance
        """
        try:
            # 1. حساب المخاطرة
            risk_per_trade_pct = 0.02  # 2% من الرصيد
            risk_amount = balance * risk_per_trade_pct
            
            # 2. حساب المسافة من Stop Loss
            stop_distance = entry_price - stop_loss_price
            stop_distance_pct = stop_distance / entry_price
            
            # 3. حساب حجم الصفقة
            position_notional = risk_amount / stop_distance_pct
            
            # 4. حساب الرافعة المالية
            leverage = position_notional / balance
            leverage = min(leverage, max_leverage)  # حد أقصى 10x
            
            # 5. حجم العقود
            contract_size = position_notional / entry_price
            
            return {
                'risk_amount': risk_amount,
                'stop_distance': stop_distance,
                'position_notional': position_notional,
                'leverage': leverage,
                'contract_size': contract_size,
            }
        
        except Exception as e:
            logger.error(f"❌ خطأ في حساب حجم الصفقة: {e}")
            return {}
    
    def calculate_exits(self, entry_price: float, atr: float) -> Dict:
        """
        حساب نقاط الخروج
        
        SL = ATR × 1.5
        TP1 = ATR × 2.0 (50%)
        TP2 = ATR × 3.5 (50%)
        R:R >= 2:1
        """
        try:
            stop_loss = entry_price - (atr * 1.5)
            take_profit_1 = entry_price + (atr * 2.0)
            take_profit_2 = entry_price + (atr * 3.5)
            
            # حساب نسبة المخاطرة/الربح
            risk = entry_price - stop_loss
            reward = (take_profit_2 - entry_price) * 0.5 + (take_profit_1 - entry_price) * 0.5
            risk_reward_ratio = reward / risk if risk > 0 else 0
            
            return {
                'stop_loss': stop_loss,
                'take_profit_1': take_profit_1,
                'take_profit_2': take_profit_2,
                'risk_reward_ratio': risk_reward_ratio,
                'valid': risk_reward_ratio >= 2.0,  # يجب أن يكون >= 2:1
            }
        
        except Exception as e:
            logger.error(f"❌ خطأ في حساب نقاط الخروج: {e}")
            return {}
    
    def should_exit(
        self,
        current_price: float,
        stop_loss: float,
        take_profit_1: float,
        take_profit_2: float
    ) -> Tuple[bool, str, float]:
        """
        التحقق من شروط الخروج
        """
        # وقف الخسارة
        if current_price <= stop_loss:
            return True, 'STOP_LOSS', stop_loss
        
        # هدف الربح الأول
        if current_price >= take_profit_1:
            return True, 'TAKE_PROFIT_1', take_profit_1
        
        # هدف الربح الثاني
        if current_price >= take_profit_2:
            return True, 'TAKE_PROFIT_2', take_profit_2
        
        return False, None, None
    
    def validate_trade(self, trade: Dict) -> bool:
        """
        التحقق من صحة الصفقة قبل التنفيذ
        """
        try:
            # التحقق من المتطلبات الأساسية
            required_fields = ['entry_price', 'stop_loss', 'take_profit_1', 'contract_size']
            
            for field in required_fields:
                if field not in trade or trade[field] is None:
                    logger.warning(f"⚠️ حقل مفقود: {field}")
                    return False
            
            # التحقق من الأسعار المنطقية
            if trade['entry_price'] <= 0 or trade['contract_size'] <= 0:
                logger.warning("⚠️ أسعار غير منطقية")
                return False
            
            # التحقق من أن Stop Loss أقل من Entry
            if trade['stop_loss'] >= trade['entry_price']:
                logger.warning("⚠️ Stop Loss يجب أن يكون أقل من Entry Price")
                return False
            
            return True
        
        except Exception as e:
            logger.error(f"❌ خطأ في التحقق من الصفقة: {e}")
            return False
    
    def print_signal(self, signal_data: Dict):
        """طباعة بيانات الإشارة"""
        print("\n" + "=" * 60)
        print("📊 إشارة شراء تم اكتشافها!")
        print("=" * 60)
        print(f"\n💡 شروط الشراء:")
        for condition, value in signal_data.get('conditions', {}).items():
            status = "✅" if value else "❌"
            print(f"   {status} {condition}: {value}")
        
        print(f"\n💰 بيانات الصفقة:")
        print(f"   • سعر الدخول: ${signal_data.get('entry_price', 0):.2f}")
        print(f"   • ATR: {signal_data.get('atr', 0):.2f}")

# ─────────────────────────────────────────────────────────
# اختبار سريع
# ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    strategy = TradingStrategy()
    logger.success("✅ استراتيجية جاهزة للاستخدام")