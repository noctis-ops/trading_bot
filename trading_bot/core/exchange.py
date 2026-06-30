"""
═══════════════════════════════════════════════════════════
التعامل مع بينانس API — Exchange Module
═══════════════════════════════════════════════════════════
wrapper احترافي لـ CCXT لتداول العملات الرقمية على بينانس
(النسخة الكاملة — للإنتاج فقط)

⚠️  تحذير مهم:
    CCXT أوقف دعم Testnet لـ Binance Futures (يونيو 2025)
    استخدم PaperTradingExchange بدلاً منه خلال التطوير:
        from core import create_exchange
        exchange = create_exchange()  # يختار paper تلقائياً
"""

import ccxt
import os
import time
from datetime import datetime
from pathlib import Path
import backoff
import yaml
from typing import Dict, List, Optional

from utils.logger import logger

# ─────────────────────────────────────────────────────────
# قراءة الإعدادات
# ─────────────────────────────────────────────────────────

CONFIG_PATH = Path(__file__).parent.parent / 'config.yaml'

with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
    config = yaml.safe_load(f)

from dotenv import load_dotenv
load_dotenv()


class BinanceExchange:
    """
    فئة للتعامل الآمن مع بينانس API — للإنتاج فقط

    ⚠️  لا تستخدم هذه الفئة أثناء التطوير والاختبار.
       استخدم PaperTradingExchange أو create_exchange() بدلاً منها.

    المميزات:
    - إعادة محاولة تلقائية (Backoff)
    - معالجة الأخطاء الشاملة
    - Rate limiting ذكي
    - دعم Live Trading فقط (Testnet متوقف في CCXT)
    """

    def __init__(self, use_testnet: bool = True):
        """
        تهيئة الاتصال بـ بينانس

        Args:
            use_testnet: مُتجاهَل — Testnet لم يعد مدعوماً.
                         استخدم PaperTradingExchange للاختبار الآمن.
        """
        # ── تحذير Testnet ──────────────────────────────────
        if use_testnet:
            logger.warning(
                "⚠️  use_testnet=True — لكن Binance Futures Testnet\n"
                "    توقف في CCXT (يونيو 2025). سيتم الاتصال بالـ LIVE API.\n"
                "    ⛔ لا تُنفّذ أوامر حقيقية إذا كنت في مرحلة الاختبار!\n"
                "    ✅ الحل الأفضل: استخدم create_exchange() من core/__init__.py"
            )

        self.use_testnet      = False  # Testnet متوقف — نتصل بـ Live دائماً
        self.environment      = "LIVE ⚠️"
        self.last_api_call    = 0
        self.api_call_count   = 0
        self.request_timeout  = config['exchange']['timeout']

        self.exchange = None
        self._initialize_exchange()

    def _initialize_exchange(self):
        """تهيئة كائن بينانس مع الإعدادات الصحيحة"""
        try:
            logger.info(f"🔗 جاري الاتصال بـ {self.environment}...")

            api_key    = os.getenv('BINANCE_API_KEY',    '').strip()
            secret_key = os.getenv('BINANCE_SECRET_KEY', '').strip()

            if not api_key or not secret_key:
                logger.critical(
                    "❌ لم يتم العثور على BINANCE_API_KEY أو BINANCE_SECRET_KEY\n"
                    "   تأكد من ملف .env .\n"
                    "   أو استخدم TRADING_MODE=paper لتجنب الحاجة لمفاتيح."
                )
                raise ValueError("مفاتيح API مفقودة في .env")

            self.exchange = ccxt.binance({
                'apiKey':          api_key,
                'secret':          secret_key,
                'enableRateLimit': True,
                'options': {
                    'defaultType':                              'future',
                    'fetchMyTradesMethod':                      'private',
                    'warnOnFetchOpenOrdersWithoutSymbol':       False,
                    'recvWindow':                               10000,
                },
                'timeout': self.request_timeout,
            })

            logger.success("✅ كائن CCXT تم إنشاؤه بنجاح")
            self._test_connection()
            logger.success(
                f"✅ اتصال بينانس نجح | البيئة: {self.environment}"
            )

        except Exception as e:
            logger.critical(f"❌ خطأ في تهيئة البينانس: {e}")
            raise

    def _test_connection(self):
        """اختبار الاتصال بـ API"""
        try:
            logger.info("🧪 اختبار الاتصال...")

            # اختبار البيانات العامة أولاً
            try:
                self.exchange.fetch_ticker('BTC/USDT')
                logger.success("✅ Server متاح")
            except Exception as e:
                logger.warning(f"⚠️ خطأ في جلب ticker: {e}")

            # اختبار المصادقة
            try:
                balance = self.exchange.fetch_balance()
                usdt    = balance.get('USDT', {})
                free    = usdt.get('free', 0)
                total   = usdt.get('total', 0)
                logger.success(
                    f"✅ اختبار API نجح | الرصيد: ${free:.2f} (من ${total:.2f})"
                )
            except (KeyError, TypeError):
                logger.success("✅ اختبار API نجح (بدون بيانات رصيد)")

        except ccxt.AuthenticationError:
            logger.critical("❌ خطأ في المصادقة — تحقق من API Key/Secret")
            raise
        except ccxt.NetworkError:
            logger.critical("❌ خطأ في الاتصال بالشبكة — تحقق من الإنترنت")
            raise
        except Exception as e:
            logger.critical(f"❌ خطأ في اختبار الاتصال: {e}")
            raise

    # ═════════════════════════════════════════════════════
    # دوال جلب البيانات
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
        """جلب بيانات الشموع (OHLCV)"""
        try:
            self._rate_limit()
            ohlcv = self.exchange.fetch_ohlcv(
                symbol, timeframe, limit=min(limit, 1500)
            )
            logger.debug(f"✓ جلب {len(ohlcv)} شمعة لـ {symbol} {timeframe}")
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
        """جلب معلومات الزوج الحالية"""
        try:
            self._rate_limit()
            return self.exchange.fetch_ticker(symbol)
        except Exception as e:
            logger.error(f"❌ خطأ في جلب ticker: {e}")
            raise

    @backoff.on_exception(
        backoff.expo,
        (ccxt.NetworkError, ccxt.ExchangeError),
        max_tries=3
    )
    def fetch_balance(self) -> Dict:
        """جلب رصيد الحساب"""
        try:
            self._rate_limit()
            return self.exchange.fetch_balance()
        except ccxt.InsufficientBalance:
            logger.warning("⚠️ رصيد غير كافٍ")
            raise
        except Exception as e:
            logger.error(f"❌ خطأ في جلب الرصيد: {e}")
            raise

    def get_available_balance(self) -> float:
        """الحصول على الرصيد المتاح بالـ USDT"""
        try:
            balance = self.fetch_balance()
            usdt    = balance.get('USDT', {})
            return float(usdt.get('free', 0))
        except Exception as e:
            logger.error(f"❌ خطأ في الحصول على الرصيد: {e}")
            return 0.0

    # ═════════════════════════════════════════════════════
    # دوال تنفيذ الأوامر
    # ═════════════════════════════════════════════════════

    def set_leverage(self, symbol: str, leverage: int):
        """تحديد الرافعة المالية"""
        try:
            leverage = max(1, min(leverage, 20))
            self._rate_limit()
            self.exchange.set_leverage(leverage, symbol)
            logger.info(f"⚡ رافعة | {symbol} = {leverage}x")
        except Exception as e:
            logger.error(f"❌ خطأ في تحديد الرافعة: {e}")
            raise

    def set_margin_type(self, symbol: str, margin_type: str = 'isolated'):
        """تحديد نوع الهامش"""
        try:
            self._rate_limit()
            self.exchange.private_post_fapi_v1_margintype({
                'symbol':     symbol.replace('/', ''),
                'marginType': margin_type.upper()
            })
            logger.info(f"📊 هامش | {symbol} = {margin_type}")
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
        """تنفيذ أمر سوق"""
        try:
            self._rate_limit()
            if params is None:
                params = {}
            params['defaultType'] = 'future'

            order = self.exchange.create_market_order(
                symbol, side, amount, params=params
            )
            logger.info(
                f"✅ أمر سوق | {symbol} {side.upper()} "
                f"{amount} | ID: {order['id']}"
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
        """إنشاء أمر وقف الخسارة"""
        try:
            self._rate_limit()
            params = {'stopPrice': stop_price, 'closePosition': True}
            order  = self.exchange.create_order(
                symbol, 'STOP_MARKET', side, amount, params=params
            )
            logger.info(
                f"🛑 SL | {symbol} @ ${stop_price:.2f} | ID: {order['id']}"
            )
            return order
        except Exception as e:
            logger.error(f"❌ خطأ في إنشاء SL: {e}")
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
        """إنشاء أمر هدف الربح"""
        try:
            self._rate_limit()
            params = {'stopPrice': stop_price, 'closePosition': True}
            order  = self.exchange.create_order(
                symbol, 'TAKE_PROFIT_MARKET', side, amount, params=params
            )
            logger.info(
                f"🎯 TP | {symbol} @ ${stop_price:.2f} | ID: {order['id']}"
            )
            return order
        except Exception as e:
            logger.error(f"❌ خطأ في إنشاء TP: {e}")
            raise

    @backoff.on_exception(
        backoff.expo,
        (ccxt.NetworkError, ccxt.ExchangeError),
        max_tries=3
    )
    def cancel_order(self, symbol: str, order_id: str) -> Dict:
        """إلغاء أمر"""
        try:
            self._rate_limit()
            order = self.exchange.cancel_order(order_id, symbol)
            logger.info(f"❌ ألغي الأمر | ID: {order_id}")
            return order
        except Exception as e:
            logger.error(f"❌ خطأ في إلغاء الأمر: {e}")
            raise

    # ═════════════════════════════════════════════════════
    # دوال الصفقات المفتوحة
    # ═════════════════════════════════════════════════════

    @backoff.on_exception(
        backoff.expo,
        (ccxt.NetworkError, ccxt.ExchangeError),
        max_tries=3
    )
    def get_positions(self) -> List[Dict]:
        """جميع الصفقات المفتوحة"""
        try:
            self._rate_limit()
            positions = self.exchange.fetch_positions()
            return [
                p for p in positions
                if float(p.get('contracts', 0)) > 0
                and p.get('contractSize', 0) > 0
            ]
        except Exception as e:
            logger.error(f"❌ خطأ في جلب الصفقات: {e}")
            return []

    def get_position_by_symbol(self, symbol: str) -> Optional[Dict]:
        """صفقة محددة بالزوج"""
        for pos in self.get_positions():
            if pos['symbol'] == symbol:
                return pos
        return None

    @backoff.on_exception(
        backoff.expo,
        (ccxt.NetworkError, ccxt.ExchangeError),
        max_tries=3
    )
    def close_position(
        self,
        symbol: str,
        position_side: str = None
    ) -> Dict:
        """إغلاق صفقة بأمر سوق معاكس"""
        try:
            position = self.get_position_by_symbol(symbol)
            if not position:
                logger.warning(f"⚠️ لا توجد صفقة مفتوحة لـ {symbol}")
                return {}

            contracts  = float(position['contracts'])
            side_close = 'sell' if position['side'] == 'long' else 'buy'

            order = self.create_market_order(symbol, side_close, contracts)
            logger.success(f"✅ أغلقت الصفقة | {symbol} | {contracts}")
            return order
        except Exception as e:
            logger.error(f"❌ خطأ في إغلاق الصفقة: {e}")
            raise

    # ═════════════════════════════════════════════════════
    # دوال مساعدة
    # ═════════════════════════════════════════════════════

    def _rate_limit(self):
        """تطبيق rate limiting يدوياً"""
        min_interval = 0.1
        elapsed = time.time() - self.last_api_call
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed)
        self.last_api_call = time.time()
        self.api_call_count += 1

    def get_exchange_info(self, symbol: str) -> Dict:
        """معلومات الزوج من البورصة"""
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
        """تدوير الكمية حسب دقة الزوج"""
        market    = self.get_exchange_info(symbol)
        precision = market.get('precision', {}).get('amount', 8)
        return round(quantity, precision)

    def round_price(self, symbol: str, price: float) -> float:
        """تدوير السعر حسب دقة الزوج"""
        market    = self.get_exchange_info(symbol)
        precision = market.get('precision', {}).get('price', 8)
        return round(price, precision)

    def is_open_market(self) -> bool:
        try:
            self.fetch_ticker('BTC/USDT')
            return True
        except Exception:
            return False

    def get_server_time(self) -> datetime:
        try:
            ts = self.exchange.fetch_time()
            return datetime.fromtimestamp(ts / 1000)
        except Exception:
            return datetime.utcnow()

    def get_api_status(self) -> str:
        try:
            self._test_connection()
            return "✅ متصل بشكل جيد"
        except Exception:
            return "❌ خطأ في الاتصال"