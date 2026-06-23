"""
═══════════════════════════════════════════════════════════
التعامل مع بينانس API - Exchange Module
═══════════════════════════════════════════════════════════
wrapper احترافي لـ CCXT لتداول العملات الرقمية على بينانس
(النسخة الكاملة مع إصلاحات Testnet)
"""

import ccxt
import os
import time
import asyncio
from datetime import datetime, timedelta
from pathlib import Path
import backoff
import yaml
from typing import Dict, List, Optional, Tuple

# استيراد المكتبات الخاصة
from utils.logger import logger

# ─────────────────────────────────────────────────────────
# قراءة الإعدادات
# ─────────────────────────────────────────────────────────

CONFIG_PATH = Path(__file__).parent.parent / 'config.yaml'

with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
    config = yaml.safe_load(f)

from dotenv import load_dotenv
load_dotenv()

# ─────────────────────────────────────────────────────────
# فئة BinanceExchange الرئيسية
# ─────────────────────────────────────────────────────────

class BinanceExchange:
    """
    فئة للتعامل الآمن مع بينانس API
    تتضمن:
    - إعادة محاولة تلقائية (Backoff)
    - معالجة الأخطاء الشاملة
    - Rate limiting ذكي
    - دعم Testnet محسّن ✅
    """
    
    def __init__(self, use_testnet: bool = True):
        """
        تهيئة الاتصال بـ بينانس
        
        Args:
            use_testnet: استخدام Testnet أم حساب حقيقي؟
        """
        self.use_testnet = use_testnet
        self.exchange = None
        self.environment = "TESTNET ✅" if use_testnet else "LIVE ⚠️"
        self.last_api_call = 0
        self.api_call_count = 0
        self.request_timeout = config['exchange']['timeout']
        
        self._initialize_exchange()
    
    def _initialize_exchange(self):
        """
        تهيئة كائن بينانس مع الإعدادات الصحيحة
        
        ✅ الحل الصحيح:
        - استخدام Live API Keys (من binance.com)
        - استخدام set_sandbox_mode(True) لـ Testnet
        - عدم محاولة تعيين URLs يدويًا
        """
        try:
            logger.info(f"🔗 جاري الاتصال بـ {self.environment}...")
            
            # ✅ استخدم Live API Keys دائماً (حتى للـ Testnet)
            # السبب: مفاتيح Testnet من testnet.binancefuture.com غير متوافقة مع CCXT
            api_key = os.getenv('BINANCE_API_KEY')
            secret_key = os.getenv('BINANCE_SECRET_KEY')
            
            # إذا لم توجد Live Keys، جرّب Testnet Keys (قد تعمل)
            if not api_key or not secret_key:
                api_key = os.getenv('BINANCE_TESTNET_API_KEY')
                secret_key = os.getenv('BINANCE_TESTNET_SECRET_KEY')
                if api_key and secret_key:
                    logger.warning("⚠️ استخدام Testnet API Keys (قد تفشل)")
            
            if not api_key or not secret_key:
                logger.critical("❌ لم يتم العثور على API Keys في .env")
                logger.critical("تأكد من وجود:")
                logger.critical("  - BINANCE_API_KEY و BINANCE_SECRET_KEY (الأفضل)")
                logger.critical("  أو")
                logger.critical("  - BINANCE_TESTNET_API_KEY و BINANCE_TESTNET_SECRET_KEY")
                raise ValueError("Missing API credentials")
            
            # ✅ إعدادات CCXT
            exchange_params = {
                'apiKey': api_key.strip(),  # ✅ أزل المسافات الزائدة
                'secret': secret_key.strip(),  # ✅ أزل المسافات الزائدة
                'enableRateLimit': True,
                'options': {
                    'defaultType': 'future',  # USDT-M Futures
                    'fetchMyTradesMethod': 'private',
                    'warnOnFetchOpenOrdersWithoutSymbol': False,
                    'recvWindow': 10000,  # ✅ مهم لمشاكل التوقيت
                    'test': self.use_testnet,  # ✅ إخبار CCXT بأننا في Testnet
                },
                'timeout': self.request_timeout,
            }
            
            # ❌ لا تعيّن URLs يدويًا
            # ✅ دع CCXT تتعامل مع الـ routing تلقائياً عند استخدام set_sandbox_mode()
            
            # إنشاء كائن Exchange
            self.exchange = ccxt.binance(exchange_params)
            
            # ✅ تفعيل وضع Testnet بشكل صحيح
            if self.use_testnet:
                self.exchange.set_sandbox_mode(True)
                logger.info("✅ تم تفعيل وضع Testnet (Sandbox Mode)")
            
            logger.success(f"✅ كائن CCXT تم إنشاؤه بنجاح")
            
            # اختبار الاتصال
            self._test_connection()
            
            logger.success(
                f"✅ اتصال بينانس نجح | البيئة: {self.environment}"
            )
        
        except Exception as e:
            logger.critical(f"❌ خطأ في تهيئة البينانس: {e}")
            raise
    
    def _test_connection(self):
        """
        اختبار الاتصال بـ API
        """
        try:
            logger.info("🧪 اختبار الاتصال...")
            
            # محاولة جلب بيانات السوق (عام، بدون مفاتيح)
            try:
                self.exchange.fetch_ticker('BTC/USDT')
                logger.success("✅ Server/Testnet متاح")
            except Exception as e:
                logger.warning(f"⚠️ خطأ في جلب ticker: {e}")
            
            # محاولة جلب الرصيد (يتطلب مفاتيح صحيحة)
            try:
                balance = self.exchange.fetch_balance()
                
                # ✅ طريقة آمنة للتعامل مع الرصيد
                usdt_balance = balance.get('USDT', {})
                total = usdt_balance.get('total', 0)
                free = usdt_balance.get('free', 0)
                
                logger.success(
                    f"✅ اختبار API نجح | الرصيد: ${free:.2f} (من ${total:.2f})"
                )
            
            except (KeyError, TypeError):
                # إذا فشل التعامل مع البنية، اعتبر الاتصال ناجحاً على أي حال
                logger.success("✅ اختبار API نجح (بدون بيانات رصيد)")
        
        except ccxt.AuthenticationError as e:
            logger.critical("❌ خطأ في المصادقة - تحقق من API Key/Secret")
            raise
        
        except ccxt.NetworkError as e:
            logger.critical("❌ خطأ في الاتصال بالشبكة - تحقق من الإنترنت")
            raise
        
        except Exception as e:
            logger.critical(f"❌ خطأ في اختبار الاتصال: {e}")
            raise
    
    # ═════════════════════════════════════════════════════
    # دوال جلب البيانات (Data Fetching)
    # ═════════════════════════════════════════════════════
    
    @backoff.on_exception(
        backoff.expo,
        (ccxt.NetworkError, ccxt.ExchangeError),
        max_tries=3,
        max_time=30
    )
    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        limit: int = 200
    ) -> List[List]:
        """
        جلب بيانات الشموع (OHLCV)
        
        Args:
            symbol: الزوج (مثل 'ETH/USDT')
            timeframe: الإطار الزمني (مثل '15m', '1h')
            limit: عدد الشموع (الحد الأقصى 1500 لبينانس)
        
        Returns:
            قائمة الشموع: [[timestamp, open, high, low, close, volume], ...]
        """
        try:
            self._rate_limit()
            
            ohlcv = self.exchange.fetch_ohlcv(
                symbol,
                timeframe,
                limit=min(limit, 1500)  # حد أقصى بينانس
            )
            
            logger.debug(
                f"✓ جلب {len(ohlcv)} شمعة لـ {symbol} {timeframe}"
            )
            
            return ohlcv
        
        except ccxt.BadSymbol:
            logger.error(f"❌ الزوج '{symbol}' غير موجود")
            return []
        
        except Exception as e:
            logger.error(f"❌ خطأ في جلب OHLCV: {e}")
            raise
    
    @backoff.on_exception(
        backoff.expo,
        (ccxt.NetworkError, ccxt.ExchangeError),
        max_tries=3
    )
    def fetch_ticker(self, symbol: str) -> Dict:
        """
        جلب معلومات الزوج الحالية
        
        Args:
            symbol: الزوج (مثل 'ETH/USDT')
        
        Returns:
            قاموس بمعلومات الزوج
        """
        try:
            self._rate_limit()
            ticker = self.exchange.fetch_ticker(symbol)
            return ticker
        
        except Exception as e:
            logger.error(f"❌ خطأ في جلب ticker: {e}")
            raise
    
    @backoff.on_exception(
        backoff.expo,
        (ccxt.NetworkError, ccxt.ExchangeError),
        max_tries=3
    )
    def fetch_balance(self) -> Dict:
        """
        جلب رصيد الحساب
        
        Returns:
            قاموس بتفاصيل الرصيد
        """
        try:
            self._rate_limit()
            balance = self.exchange.fetch_balance()
            return balance
        
        except ccxt.InsufficientBalance:
            logger.warning("⚠️ رصيد غير كافٍ")
            raise
        
        except Exception as e:
            logger.error(f"❌ خطأ في جلب الرصيد: {e}")
            raise
    
    def get_available_balance(self) -> float:
        """
        الحصول على الرصيد المتاح بالـ USDT
        
        Returns:
            الرصيد المتاح (float)
        """
        try:
            balance = self.fetch_balance()
            
            # ✅ طريقة آمنة للتعامل مع البنية
            usdt_balance = balance.get('USDT', {})
            available = usdt_balance.get('free', 0)
            
            logger.debug(f"💰 الرصيد المتاح: ${available:.2f}")
            return float(available)
        
        except Exception as e:
            logger.error(f"❌ خطأ في الحصول على الرصيد: {e}")
            return 0.0
    
    # ═════════════════════════════════════════════════════
    # دوال تنفيذ الأوامر (Order Execution)
    # ═════════════════════════════════════════════════════
    
    def set_leverage(self, symbol: str, leverage: int):
        """
        تحديد الرافعة المالية للزوج
        
        Args:
            symbol: الزوج
            leverage: مستوى الرافعة (1-20)
        """
        try:
            leverage = max(1, min(leverage, 20))  # حد من 1 إلى 20
            
            self._rate_limit()
            
            self.exchange.set_leverage(leverage, symbol)
            
            logger.info(f"⚡ تحديد الرافعة | {symbol} = {leverage}x")
        
        except Exception as e:
            logger.error(f"❌ خطأ في تحديد الرافعة: {e}")
            raise
    
    def set_margin_type(self, symbol: str, margin_type: str = 'isolated'):
        """
        تحديد نوع الهامش
        
        Args:
            symbol: الزوج
            margin_type: 'isolated' أو 'crossed'
        """
        try:
            self._rate_limit()
            
            self.exchange.private_post_fapi_v1_margintype({
                'symbol': symbol.replace('/', ''),
                'marginType': margin_type.upper()
            })
            
            logger.info(f"📊 نوع الهامش | {symbol} = {margin_type}")
        
        except ccxt.BadRequest:
            logger.warning(f"⚠️ الزوج قد يكون بالفعل {margin_type}")
        
        except Exception as e:
            logger.warning(f"⚠️ خطأ في تعيين نوع الهامش: {e}")
    
    @backoff.on_exception(
        backoff.expo,
        (ccxt.NetworkError, ccxt.ExchangeError),
        max_tries=3
    )
    def create_market_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        params: Dict = None
    ) -> Dict:
        """
        تنفيذ أمر سوق (شراء/بيع فوراً بسعر السوق)
        
        Args:
            symbol: الزوج
            side: 'buy' أو 'sell'
            amount: الكمية
            params: معاملات إضافية (رافعة، إلخ)
        
        Returns:
            معلومات الأمر
        """
        try:
            self._rate_limit()
            
            if params is None:
                params = {}
            
            # إضافة معاملات بينانس
            params['defaultType'] = 'future'
            
            order = self.exchange.create_market_order(
                symbol,
                side,
                amount,
                params=params
            )
            
            logger.info(
                f"✅ أمر سوق | {symbol} {side.upper()} {amount} | "
                f"ID: {order['id']}"
            )
            
            return order
        
        except ccxt.InsufficientBalance:
            logger.error("❌ رصيد غير كافٍ")
            raise
        
        except Exception as e:
            logger.error(f"❌ خطأ في تنفيذ أمر السوق: {e}")
            raise
    
    @backoff.on_exception(
        backoff.expo,
        (ccxt.NetworkError, ccxt.ExchangeError),
        max_tries=3
    )
    def create_stop_loss_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        stop_price: float
    ) -> Dict:
        """
        إنشاء أمر وقف الخسارة
        
        Args:
            symbol: الزوج
            side: اتجاه الإغلاق ('sell' للـ long، 'buy' للـ short)
            amount: الكمية
            stop_price: سعر التفعيل
        
        Returns:
            معلومات الأمر
        """
        try:
            self._rate_limit()
            
            # معكوس: لـ long (شراء) نضع أمر بيع لوقف الخسارة
            # لـ short (بيع) نضع أمر شراء لوقف الخسارة
            
            params = {
                'stopPrice': stop_price,
                'closePosition': True,  # أغلق الصفقة بالكامل
            }
            
            order = self.exchange.create_order(
                symbol,
                'STOP_MARKET',
                side,
                amount,
                params=params
            )
            
            logger.info(
                f"🛑 أمر وقف خسارة | {symbol} @ ${stop_price:.2f} | "
                f"ID: {order['id']}"
            )
            
            return order
        
        except Exception as e:
            logger.error(f"❌ خطأ في إنشاء أمر وقف الخسارة: {e}")
            raise
    
    @backoff.on_exception(
        backoff.expo,
        (ccxt.NetworkError, ccxt.ExchangeError),
        max_tries=3
    )
    def create_take_profit_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        stop_price: float
    ) -> Dict:
        """
        إنشاء أمر هدف الربح
        
        Args:
            symbol: الزوج
            side: اتجاه الإغلاق ('sell' للـ long، 'buy' للـ short)
            amount: الكمية
            stop_price: سعر التفعيل (هدف الربح)
        
        Returns:
            معلومات الأمر
        """
        try:
            self._rate_limit()
            
            params = {
                'stopPrice': stop_price,
                'closePosition': True,
            }
            
            order = self.exchange.create_order(
                symbol,
                'TAKE_PROFIT_MARKET',
                side,
                amount,
                params=params
            )
            
            logger.info(
                f"🎯 أمر هدف ربح | {symbol} @ ${stop_price:.2f} | "
                f"ID: {order['id']}"
            )
            
            return order
        
        except Exception as e:
            logger.error(f"❌ خطأ في إنشاء أمر هدف الربح: {e}")
            raise
    
    @backoff.on_exception(
        backoff.expo,
        (ccxt.NetworkError, ccxt.ExchangeError),
        max_tries=3
    )
    def cancel_order(self, symbol: str, order_id: str) -> Dict:
        """
        إلغاء أمر
        
        Args:
            symbol: الزوج
            order_id: معرّف الأمر
        
        Returns:
            معلومات الأمر الملغى
        """
        try:
            self._rate_limit()
            
            order = self.exchange.cancel_order(order_id, symbol)
            
            logger.info(f"❌ ألغي الأمر | ID: {order_id}")
            
            return order
        
        except Exception as e:
            logger.error(f"❌ خطأ في إلغاء الأمر: {e}")
            raise
    
    # ═════════════════════════════════════════════════════
    # دوال معلومات الصفقات (Position Info)
    # ═════════════════════════════════════════════════════
    
    @backoff.on_exception(
        backoff.expo,
        (ccxt.NetworkError, ccxt.ExchangeError),
        max_tries=3
    )
    def get_positions(self) -> List[Dict]:
        """
        الحصول على جميع الصفقات المفتوحة
        
        Returns:
            قائمة الصفقات المفتوحة
        """
        try:
            self._rate_limit()
            
            positions = self.exchange.fetch_positions()
            
            # فلترة الصفقات المفتوحة فقط
            open_positions = [
                p for p in positions 
                if float(p['contracts']) > 0 and p['contractSize'] > 0
            ]
            
            return open_positions
        
        except Exception as e:
            logger.error(f"❌ خطأ في جلب الصفقات: {e}")
            return []
    
    def get_position_by_symbol(self, symbol: str) -> Optional[Dict]:
        """
        الحصول على صفقة محددة بـ symbol
        
        Args:
            symbol: الزوج
        
        Returns:
            معلومات الصفقة أو None
        """
        positions = self.get_positions()
        for pos in positions:
            if pos['symbol'] == symbol:
                return pos
        return None
    
    @backoff.on_exception(
        backoff.expo,
        (ccxt.NetworkError, ccxt.ExchangeError),
        max_tries=3
    )
    def close_position(self, symbol: str, position_side: str = None) -> Dict:
        """
        إغلاق صفقة بأكملها بأمر سوق معاكس
        
        Args:
            symbol: الزوج
            position_side: 'long' أو 'short' (اختياري)
        
        Returns:
            معلومات الأمر
        """
        try:
            position = self.get_position_by_symbol(symbol)
            
            if not position:
                logger.warning(f"⚠️ لا توجد صفقة مفتوحة لـ {symbol}")
                return {}
            
            # تحديد الكمية والاتجاه
            contracts = float(position['contracts'])
            side = position['side']  # 'long' أو 'short'
            
            # الاتجاه المعاكس لـ close
            close_side = 'sell' if side == 'long' else 'buy'
            
            # تنفيذ أمر الإغلاق
            order = self.create_market_order(
                symbol,
                close_side,
                contracts
            )
            
            logger.success(f"✅ أغلقت الصفقة | {symbol} | {contracts} {side}")
            
            return order
        
        except Exception as e:
            logger.error(f"❌ خطأ في إغلاق الصفقة: {e}")
            raise
    
    # ═════════════════════════════════════════════════════
    # دوال مساعدة (Helper Functions)
    # ═════════════════════════════════════════════════════
    
    def _rate_limit(self):
        """
        تطبيق rate limiting يدويًا
        (في الواقع، CCXT يفعل هذا تلقائياً مع enableRateLimit: true)
        """
        min_interval = 0.1  # 100ms بين الطلبات
        elapsed = time.time() - self.last_api_call
        
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed)
        
        self.last_api_call = time.time()
        self.api_call_count += 1
    
    def get_exchange_info(self, symbol: str) -> Dict:
        """
        الحصول على معلومات الزوج من البورصة
        (حد أدنى، حد أقصى للكمية، إلخ)
        
        Args:
            symbol: الزوج
        
        Returns:
            قاموس معلومات الزوج
        """
        try:
            markets = self.exchange.fetch_markets()
            for market in markets:
                if market['symbol'] == symbol:
                    return market
            return {}
        
        except Exception as e:
            logger.error(f"❌ خطأ في جلب معلومات البورصة: {e}")
            return {}
    
    def round_quantity(self, symbol: str, quantity: float) -> float:
        """
        تدوير الكمية حسب دقة الزوج
        
        Args:
            symbol: الزوج
            quantity: الكمية المراد تدويرها
        
        Returns:
            الكمية المدورة
        """
        market = self.get_exchange_info(symbol)
        
        if not market:
            return quantity
        
        precision = market.get('precision', {}).get('amount', 8)
        
        # تدوير إلى عدد الخانات المطلوبة
        return round(quantity, precision)
    
    def round_price(self, symbol: str, price: float) -> float:
        """
        تدوير السعر حسب دقة الزوج
        
        Args:
            symbol: الزوج
            price: السعر المراد تدويره
        
        Returns:
            السعر المدور
        """
        market = self.get_exchange_info(symbol)
        
        if not market:
            return price
        
        precision = market.get('precision', {}).get('price', 8)
        
        return round(price, precision)
    
    def is_open_market(self) -> bool:
        """
        التحقق من أن السوق مفتوح
        (العملات الرقمية مفتوحة 24/7، لكن قد تكون هناك صيانة)
        """
        try:
            self.fetch_ticker('BTC/USDT')
            return True
        except:
            return False
    
    def get_server_time(self) -> datetime:
        """
        الحصول على وقت سرفر البورصة
        """
        try:
            # جلب معلومات السرفر
            timestamp = self.exchange.fetch_time()
            return datetime.fromtimestamp(timestamp / 1000)
        except:
            return datetime.utcnow()
    
    def get_api_status(self) -> str:
        """
        الحصول على حالة API
        """
        try:
            self._test_connection()
            return "✅ متصل بشكل جيد"
        except:
            return "❌ خطأ في الاتصال"

# ─────────────────────────────────────────────────────────
# مثال على الاستخدام
# ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    # إنشاء instance
    exchange = BinanceExchange(use_testnet=True)
    
    # جلب البيانات
    ohlcv = exchange.fetch_ohlcv('ETH/USDT', '15m', limit=10)
    print(f"آخر 10 شموع: {ohlcv[-1]}")
    
    # جلب الرصيد
    balance = exchange.get_available_balance()
    print(f"الرصيد: ${balance:.2f}")
    
    # جلب الصفقات المفتوحة
    positions = exchange.get_positions()
    print(f"عدد الصفقات المفتوحة: {len(positions)}")