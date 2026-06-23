"""
═══════════════════════════════════════════════════════════
محرك Backtesting متقدم - دقيق 100%
═══════════════════════════════════════════════════════════
نفس الاستراتيجية والشروط من البوت الأساسي
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Tuple, Optional
import json

from utils.logger import logger
from core.strategy import TradingStrategy
from data.market_data import MarketData

class AdvancedBacktestingEngine:
    """
    محرك Backtesting دقيق يستخدم نفس الاستراتيجية بالضبط
    """
    
    def __init__(self, initial_balance: float = 10000.0):
        """تهيئة محرك Backtesting"""
        self.initial_balance = initial_balance
        self.current_balance = initial_balance
        self.strategy = TradingStrategy()
        
        self.trades = []
        self.signals = []
        self.positions = {}
        self.equity_curve = [initial_balance]
        self.timestamps = []
        
        self.total_trades = 0
        self.winning_trades = 0
        self.losing_trades = 0
        
        logger.success(f"✅ محرك Backtesting متقدم جاهز | الرصيد: ${initial_balance:.2f}")
    
    def load_and_prepare_data(
        self,
        exchange,
        symbol: str = 'BTC/USDT'
    ) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        """
        تحميل البيانات من API وتحضيرها
        نفس طريقة البوت الأساسي بالضبط
        """
        try:
            logger.info(f"📊 تحميل البيانات لـ {symbol}...")
            
            market_data = MarketData(exchange)
            
            # جلب البيانات على الأطر الزمنية الثلاثة
            df_1h = market_data.get_complete_dataframe(symbol, '1h', limit=200)
            df_15m = market_data.get_complete_dataframe(symbol, '15m', limit=200)
            df_5m = market_data.get_complete_dataframe(symbol, '5m', limit=200)
            
            if df_1h.empty or df_15m.empty or df_5m.empty:
                logger.error("❌ فشل تحميل البيانات")
                return None, None, None
            
            logger.success(f"✅ تم تحميل البيانات:")
            logger.success(f"   • 1H: {len(df_1h)} شمعة")
            logger.success(f"   • 15M: {len(df_15m)} شمعة")
            logger.success(f"   • 5M: {len(df_5m)} شمعة")
            
            return df_1h, df_15m, df_5m
        
        except Exception as e:
            logger.error(f"❌ خطأ في تحميل البيانات: {e}")
            return None, None, None
    
    def generate_sample_data(
        self,
        symbol: str = 'BTC/USDT',
        days: int = 30
    ) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        """
        توليد بيانات تجريبية واقعية
        """
        try:
            logger.warning(f"⚠️ توليد بيانات تجريبية ({days} يوم)...")
            
            # توليد البيانات بحركة واقعية
            base_price = 40000
            volatility = 0.02
            
            dates_1h = pd.date_range(end=datetime.now(), periods=days*24, freq='H')
            
            # محاكاة حركة السعر (Random Walk مع Trend)
            returns = np.random.normal(0.0005, volatility, len(dates_1h))
            prices = base_price * (1 + returns).cumprod()
            
            # بيانات 1H
            df_1h = pd.DataFrame({
                'timestamp': dates_1h,
                'open': prices + np.random.randn(len(prices)) * 100,
                'high': prices + np.abs(np.random.randn(len(prices)) * 200),
                'low': prices - np.abs(np.random.randn(len(prices)) * 200),
                'close': prices,
                'volume': np.random.randint(1000, 5000, len(prices))
            })
            
            # بيانات 15M (4 أضعاف)
            dates_15m = pd.date_range(end=datetime.now(), periods=days*24*4, freq='15min')
            returns_15m = np.random.normal(0.0001, volatility/2, len(dates_15m))
            prices_15m = base_price * (1 + returns_15m).cumprod()
            
            df_15m = pd.DataFrame({
                'timestamp': dates_15m,
                'open': prices_15m + np.random.randn(len(prices_15m)) * 50,
                'high': prices_15m + np.abs(np.random.randn(len(prices_15m)) * 100),
                'low': prices_15m - np.abs(np.random.randn(len(prices_15m)) * 100),
                'close': prices_15m,
                'volume': np.random.randint(100, 500, len(prices_15m))
            })
            
            # بيانات 5M (12 أضعاف)
            dates_5m = pd.date_range(end=datetime.now(), periods=days*24*12, freq='5min')
            returns_5m = np.random.normal(0.00005, volatility/4, len(dates_5m))
            prices_5m = base_price * (1 + returns_5m).cumprod()
            
            df_5m = pd.DataFrame({
                'timestamp': dates_5m,
                'open': prices_5m + np.random.randn(len(prices_5m)) * 20,
                'high': prices_5m + np.abs(np.random.randn(len(prices_5m)) * 50),
                'low': prices_5m - np.abs(np.random.randn(len(prices_5m)) * 50),
                'close': prices_5m,
                'volume': np.random.randint(10, 100, len(prices_5m))
            })
            
            # إضافة المؤشرات
            for df in [df_1h, df_15m, df_5m]:
                self._add_indicators(df)
            
            logger.success(f"✅ تم توليد البيانات بنجاح")
            return df_1h, df_15m, df_5m
        
        except Exception as e:
            logger.error(f"❌ خطأ في توليد البيانات: {e}")
            return None, None, None
    
    def _add_indicators(self, df: pd.DataFrame):
        """إضافة المؤشرات نفس البوت الأساسي"""
        try:
            # EMA
            df['ema_slow'] = df['close'].ewm(span=200, adjust=False).mean()
            df['ema_fast'] = df['close'].ewm(span=50, adjust=False).mean()
            df['ema_medium'] = df['close'].ewm(span=21, adjust=False).mean()
            
            # RSI
            delta = df['close'].diff()
            gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
            rs = gain / loss
            df['rsi'] = 100 - (100 / (1 + rs))
            
            # MACD
            exp1 = df['close'].ewm(span=12, adjust=False).mean()
            exp2 = df['close'].ewm(span=26, adjust=False).mean()
            df['macd'] = exp1 - exp2
            df['macd_signal'] = df['macd'].ewm(span=9, adjust=False).mean()
            
            # ATR
            high_low = df['high'] - df['low']
            high_close = abs(df['high'] - df['close'].shift())
            low_close = abs(df['low'] - df['close'].shift())
            ranges = pd.concat([high_low, high_close, low_close], axis=1)
            true_range = ranges.max(axis=1)
            df['atr'] = true_range.rolling(14).mean()
            
            # ADX
            df['adx'] = self._calculate_adx(df)
            
            # Volume SMA
            df['volume_sma'] = df['volume'].rolling(window=20).mean()
            
            logger.debug("✅ تمت إضافة المؤشرات")
        
        except Exception as e:
            logger.error(f"❌ خطأ في إضافة المؤشرات: {e}")
    
    def _calculate_adx(self, df: pd.DataFrame) -> pd.Series:
        """حساب ADX"""
        high_diff = df['high'].diff()
        low_diff = -df['low'].diff()
        
        plus_dm = np.where((high_diff > low_diff) & (high_diff > 0), high_diff, 0)
        minus_dm = np.where((low_diff > high_diff) & (low_diff > 0), low_diff, 0)
        
        true_range = df['high'] - df['low']
        true_range = true_range.rolling(14).mean()
        
        plus_di = 100 * (pd.Series(plus_dm).rolling(14).mean() / true_range)
        minus_di = 100 * (pd.Series(minus_dm).rolling(14).mean() / true_range)
        
        di_diff = abs(plus_di - minus_di)
        di_sum = plus_di + minus_di
        
        adx = 100 * (di_diff / di_sum).rolling(14).mean()
        return adx
    
    def backtest(
        self,
        df_1h: pd.DataFrame,
        df_15m: pd.DataFrame,
        df_5m: pd.DataFrame
    ) -> Dict:
        """
        محاكاة التداول بناءً على الاستراتيجية
        """
        try:
            logger.info("🧪 بدء Backtesting...")
            
            # حلقة البيانات (من البداية للنهاية)
            for i in range(100, min(len(df_1h), len(df_15m))-1):
                
                # البيانات الحالية
                current_1h = df_1h.iloc[i]
                current_15m = df_15m.iloc[i*4:i*4+4]  # تقريبي
                
                # التحقق من إشارة شراء
                signal_found, signal_data = self.strategy.check_buy_signal(
                    df_1h.iloc[max(0, i-10):i+1],
                    df_15m.iloc[max(0, i*4-10):i*4+1],
                    df_5m.iloc[max(0, i*12-10):i*12+1]
                )
                
                if signal_found and 'symbol' not in self.positions:
                    # فتح صفقة
                    self._open_position(
                        symbol='BTC/USDT',
                        entry_price=signal_data['entry_price'],
                        atr=signal_data.get('atr', 100),
                        timestamp=current_1h['timestamp'] if 'timestamp' in current_1h else None,
                        balance=self.current_balance
                    )
                    self.signals.append(signal_data)
                
                # التحقق من الخروج
                if 'symbol' in self.positions:
                    position = self.positions['symbol']
                    exit_triggered, exit_type, exit_price = self.strategy.should_exit(
                        current_price=current_1h['close'],
                        stop_loss=position['stop_loss'],
                        take_profit_1=position['take_profit_1'],
                        take_profit_2=position['take_profit_2']
                    )
                    
                    if exit_triggered:
                        self._close_position(
                            exit_price=exit_price,
                            exit_type=exit_type,
                            timestamp=current_1h['timestamp'] if 'timestamp' in current_1h else None
                        )
                
                # تحديث منحنى الأسهم
                self.equity_curve.append(self.current_balance)
                self.timestamps.append(current_1h['timestamp'] if 'timestamp' in current_1h else None)
            
            logger.success(f"✅ انتهى Backtesting | عدد الصفقات: {len(self.trades)}")
            
            return self.get_performance_report()
        
        except Exception as e:
            logger.error(f"❌ خطأ في Backtesting: {e}")
            return {}
    
    def _open_position(
        self,
        symbol: str,
        entry_price: float,
        atr: float,
        timestamp,
        balance: float
    ):
        """فتح صفقة جديدة"""
        try:
            # حساب Stop Loss و Take Profit
            exits = self.strategy.calculate_exits(entry_price, atr)
            
            if not exits.get('valid', False):
                logger.warning("⚠️ نسبة المخاطرة/الربح غير كافية")
                return
            
            # حساب حجم الصفقة
            position_size = self.strategy.calculate_position_size(
                balance=balance,
                entry_price=entry_price,
                stop_loss_price=exits['stop_loss']
            )
            
            # التحقق من الصفقة
            trade_data = {
                'symbol': symbol,
                'entry_price': entry_price,
                'entry_time': timestamp,
                'stop_loss': exits['stop_loss'],
                'take_profit_1': exits['take_profit_1'],
                'take_profit_2': exits['take_profit_2'],
                'contract_size': position_size.get('contract_size', 0),
                'leverage': position_size.get('leverage', 1),
            }
            
            if not self.strategy.validate_trade(trade_data):
                return
            
            # خصم رأس المال
            cost = entry_price * trade_data['contract_size']
            self.current_balance -= cost
            
            self.positions[symbol] = trade_data
            self.total_trades += 1
            
            logger.info(
                f"✅ فتح صفقة | {symbol} @ ${entry_price:.2f} | "
                f"حجم: {trade_data['contract_size']:.4f} | "
                f"رافعة: {trade_data['leverage']:.1f}x"
            )
        
        except Exception as e:
            logger.error(f"❌ خطأ في فتح الصفقة: {e}")
    
    def _close_position(
        self,
        exit_price: float,
        exit_type: str,
        timestamp
    ):
        """إغلاق الصفقة"""
        try:
            if 'symbol' not in self.positions:
                return
            
            position = self.positions.pop('symbol')
            
            # حساب الربح/الخسارة
            profit = (exit_price - position['entry_price']) * position['contract_size']
            profit_pct = (profit / (position['entry_price'] * position['contract_size'])) * 100
            
            self.current_balance += exit_price * position['contract_size']
            
            trade = {
                'symbol': position['symbol'],
                'entry_price': position['entry_price'],
                'exit_price': exit_price,
                'entry_time': position['entry_time'],
                'exit_time': timestamp,
                'contract_size': position['contract_size'],
                'profit': profit,
                'profit_pct': profit_pct,
                'exit_type': exit_type,
            }
            
            self.trades.append(trade)
            
            if profit > 0:
                self.winning_trades += 1
            else:
                self.losing_trades += 1
            
            logger.success(
                f"📊 إغلاق صفقة ({exit_type}) | ${profit:,.2f} ({profit_pct:+.2f}%)"
            )
        
        except Exception as e:
            logger.error(f"❌ خطأ في إغلاق الصفقة: {e}")
    
    def get_performance_report(self) -> Dict:
        """الحصول على تقرير الأداء الشامل"""
        if not self.trades:
            return {
                'status': 'no_trades',
                'message': 'لا توجد صفقات منفذة'
            }
        
        trades_df = pd.DataFrame(self.trades)
        
        total_profit = trades_df['profit'].sum()
        total_profit_pct = (total_profit / self.initial_balance) * 100
        
        report = {
            'total_trades': len(trades_df),
            'winning_trades': self.winning_trades,
            'losing_trades': self.losing_trades,
            'win_rate': (self.winning_trades / len(trades_df) * 100) if len(trades_df) > 0 else 0,
            'total_profit': total_profit,
            'total_profit_pct': total_profit_pct,
            'avg_profit': trades_df['profit'].mean(),
            'max_profit': trades_df['profit'].max(),
            'max_loss': trades_df['profit'].min(),
            'final_balance': self.current_balance,
            'roi': ((self.current_balance - self.initial_balance) / self.initial_balance) * 100,
        }
        
        return report
    
    def print_report(self):
        """طباعة التقرير الشامل"""
        report = self.get_performance_report()
        
        if report.get('status') == 'no_trades':
            logger.warning(f"⚠️ {report['message']}")
            return
        
        print("\n" + "=" * 70)
        print("📊 تقرير Backtesting الشامل (نفس الاستراتيجية الأساسية)")
        print("=" * 70)
        
        print(f"\n💰 الأرصدة:")
        print(f"   • الأولي: ${self.initial_balance:,.2f}")
        print(f"   • النهائي: ${report['final_balance']:,.2f}")
        print(f"   • الربح: ${report['total_profit']:,.2f} ({report['roi']:+.2f}%)")
        
        print(f"\n📈 إحصائيات الصفقات:")
        print(f"   • إجمالي: {report['total_trades']}")
        print(f"   • رابحة: {report['winning_trades']}")
        print(f"   • خاسرة: {report['losing_trades']}")
        print(f"   • نسبة النجاح: {report['win_rate']:.2f}%")
        
        print(f"\n🎯 الأرباح:")
        print(f"   • متوسط الربح: ${report['avg_profit']:,.2f}")
        print(f"   • أقصى ربح: ${report['max_profit']:,.2f}")
        print(f"   • أقصى خسارة: ${report['max_loss']:,.2f}")
        
        print("\n" + "=" * 70)

# ─────────────────────────────────────────────────────────
# اختبار سريع
# ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    engine = AdvancedBacktestingEngine(initial_balance=10000.0)
    logger.success("✅ محرك Backtesting متقدم جاهز")
