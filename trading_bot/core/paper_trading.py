"""
═══════════════════════════════════════════════════════════
محرك التداول الورقي (Paper Trading Engine)
═══════════════════════════════════════════════════════════
بديل آمن 100% عن Testnet الذي أصبح غير مدعوم في CCXT

الميزات:
✅ يجلب بيانات حقيقية من API بينانس العام (بدون مفاتيح)
✅ يحاكي تنفيذ الأوامر محلياً (لا يلمس أموالاً حقيقية)
✅ يتتبع الرصيد الوهمي والصفقات والأرباح
✅ يحاكي الرسوم والانزلاق بشكل واقعي
✅ واجهة متطابقة مع BinanceExchange (drop-in replacement)
"""

import time
import uuid
import ccxt
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from pathlib import Path

import yaml
from utils.logger import logger

# ─────────────────────────────────────────────────────────
# قراءة الإعدادات
# ─────────────────────────────────────────────────────────

CONFIG_PATH = Path(__file__).parent.parent / 'config.yaml'

with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
    config = yaml.safe_load(f)


class PaperTradingExchange:
    """
    محاكي التداول الورقي — بديل آمن لـ BinanceExchange

    كيف يعمل:
    - البيانات: تُجلب من API بينانس العام بدون مفاتيح (حقيقية 100%)
    - الأوامر: تُنفَّذ محلياً في الذاكرة فقط
    - الرصيد: رصيد وهمي قابل للإعداد (افتراضي: 10,000 USDT)
    - الرسوم: محاكاة واقعية (0.04% taker fee)
    - الانزلاق: محاكاة واقعية (0.02%)

    الاستخدام:
        exchange = PaperTradingExchange(initial_balance=10000)
        # ← نفس الاستخدام تماماً مثل BinanceExchange
    """

    # ── ثوابت محاكاة السوق ──────────────────────────────
    TAKER_FEE  = 0.0004   # 0.04% رسوم بينانس فيوتشر المعتادة
    SLIPPAGE   = 0.0002   # 0.02% انزلاق تقريبي لأوامر السوق
    MAX_LEVERAGE = 10     # حد الرافعة

    def __init__(self, initial_balance: float = 10_000.0):
        """
        تهيئة محرك التداول الورقي

        Args:
            initial_balance: الرصيد الوهمي الأولي بالـ USDT
        """
        # ── تهيئة الحالة ──────────────────────────────
        self.initial_balance = initial_balance
        self.use_testnet     = False
        self.environment     = "PAPER TRADING 📝 (آمن — بدون أموال حقيقية)"

        # الرصيد المتاح (يتغير مع فتح/إغلاق الصفقات)
        self._usdt_balance: float = initial_balance

        # الصفقات المفتوحة حالياً: {symbol → position_dict}
        self._positions: Dict[str, dict] = {}

        # سجل جميع الصفقات المغلقة
        self.trade_history: List[dict] = []

        # عداد API calls للـ rate limiting
        self.last_api_call  = 0.0
        self.api_call_count = 0

        # ── تهيئة CCXT للبيانات العامة فقط (بدون مفاتيح) ──
        self._public_client = ccxt.binance({
            'enableRateLimit': True,
            'options': {
                'defaultType': 'future',
                'warnOnFetchOpenOrdersWithoutSymbol': False,
            },
            'timeout': config.get('exchange', {}).get('timeout', 30000),
        })
        # لا نضع apiKey ولا secret → وصول للبيانات العامة فقط

        logger.success(
            f"✅ Paper Trading Engine جاهز\n"
            f"   💰 الرصيد الوهمي: ${initial_balance:,.2f} USDT\n"
            f"   📊 البيانات: حقيقية من بينانس (API عام)\n"
            f"   🛡️  الأوامر: محاكاة محلية (لا أموال حقيقية)"
        )

    # ═══════════════════════════════════════════════════
    # دوال مساعدة داخلية
    # ═══════════════════════════════════════════════════

    def _rate_limit(self):
        """تطبيق rate limiting للـ API"""
        min_interval = 0.12  # 120ms بين الطلبات
        elapsed = time.time() - self.last_api_call
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed)
        self.last_api_call = time.time()
        self.api_call_count += 1

    def _apply_slippage(self, side: str, price: float) -> float:
        """
        محاكاة انزلاق السعر عند التنفيذ

        شراء  → سعر أعلى قليلاً (نشتري بسعر أغلى)
        بيع   → سعر أقل قليلاً  (نبيع بسعر أرخص)
        """
        if side == 'buy':
            return price * (1 + self.SLIPPAGE)
        return price * (1 - self.SLIPPAGE)

    def _calculate_fee(self, notional: float) -> float:
        """حساب الرسوم على المعاملة"""
        return notional * self.TAKER_FEE

    def _generate_order_id(self) -> str:
        """توليد معرّف فريد للأمر"""
        return f"PAPER-{str(uuid.uuid4())[:8].upper()}"

    # ═══════════════════════════════════════════════════
    # جلب البيانات الحقيقية (Public API — بدون مفاتيح)
    # ═══════════════════════════════════════════════════

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        limit: int = 200
    ) -> List[List]:
        """
        جلب بيانات الشموع الحقيقية من بينانس

        يستخدم API العام — لا يحتاج مفاتيح
        نفس الصيغة تماماً: [[timestamp, open, high, low, close, volume], ...]
        """
        try:
            self._rate_limit()
            ohlcv = self._public_client.fetch_ohlcv(
                symbol,
                timeframe,
                limit=min(limit, 1500)
            )
            logger.debug(
                f"✓ [Paper] جُلبت {len(ohlcv)} شمعة "
                f"لـ {symbol} {timeframe}"
            )
            return ohlcv

        except Exception as e:
            logger.error(f"❌ [Paper] خطأ في جلب OHLCV: {e}")
            return []

    def fetch_ticker(self, symbol: str) -> Dict:
        """جلب معلومات الزوج الحالية (السعر الحالي)"""
        try:
            self._rate_limit()
            return self._public_client.fetch_ticker(symbol)
        except Exception as e:
            logger.error(f"❌ [Paper] خطأ في جلب Ticker: {e}")
            return {'close': 0.0, 'percentage': 0.0}

    def get_exchange_info(self, symbol: str) -> Dict:
        """معلومات الزوج (دقة الأسعار والكميات)"""
        return {}  # Paper trading — لا نحتاج هذا

    # ═══════════════════════════════════════════════════
    # إدارة الرصيد الوهمي
    # ═══════════════════════════════════════════════════

    def fetch_balance(self) -> Dict:
        """
        إرجاع الرصيد الوهمي بنفس صيغة CCXT

        Returns:
            {'USDT': {'free': x, 'used': y, 'total': z}}
        """
        margin_in_use = sum(
            p.get('margin_locked', 0)
            for p in self._positions.values()
        )
        return {
            'USDT': {
                'free':  self._usdt_balance,
                'used':  margin_in_use,
                'total': self._usdt_balance + margin_in_use,
            }
        }

    def get_available_balance(self) -> float:
        """الحصول على الرصيد المتاح بالـ USDT"""
        return self._usdt_balance

    # ═══════════════════════════════════════════════════
    # إعدادات الصفقة (no-ops في وضع Paper Trading)
    # ═══════════════════════════════════════════════════

    def set_leverage(self, symbol: str, leverage: int):
        """تسجيل الرافعة (محلياً فقط)"""
        logger.debug(f"📌 [Paper] رافعة {symbol}: {leverage}x")

    def set_margin_type(self, symbol: str, margin_type: str = 'isolated'):
        """تسجيل نوع الهامش (محلياً فقط)"""
        logger.debug(f"📌 [Paper] هامش {symbol}: {margin_type}")

    # ═══════════════════════════════════════════════════
    # تنفيذ الأوامر (محاكاة محلية)
    # ═══════════════════════════════════════════════════

    def create_market_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        params: Dict = None
    ) -> Dict:
        """
        محاكاة تنفيذ أمر سوق

        فتح صفقة (buy):
            - يجلب السعر الحالي الحقيقي
            - يطبق الانزلاق والرسوم
            - يحجز الهامش من الرصيد
            - يسجل الصفقة في self._positions

        إغلاق صفقة (sell):
            - يحسب الربح/الخسارة
            - يُحرر الهامش + PnL إلى الرصيد
            - يُنقل الصفقة إلى self.trade_history
        """
        # جلب السعر الحالي الحقيقي
        ticker = self.fetch_ticker(symbol)
        raw_price = ticker.get('close', 0)

        if raw_price <= 0:
            logger.error(f"❌ [Paper] سعر صفري لـ {symbol} — تجاهل الأمر")
            return {}

        # تطبيق الانزلاق
        fill_price = self._apply_slippage(side, raw_price)
        notional   = fill_price * amount
        fee        = self._calculate_fee(notional)

        order_id = self._generate_order_id()
        timestamp = int(datetime.utcnow().timestamp() * 1000)

        # ── فتح صفقة (LONG) ─────────────────────────
        if side == 'buy':
            # الهامش = Notional / Leverage (نستخدم 10x كافتراضي)
            margin = notional / self.MAX_LEVERAGE

            if margin + fee > self._usdt_balance:
                logger.warning(
                    f"⚠️ [Paper] رصيد غير كافٍ | "
                    f"مطلوب: ${margin + fee:,.2f} | "
                    f"متاح: ${self._usdt_balance:,.2f}"
                )
                return {}

            # حجز الهامش والرسوم من الرصيد
            self._usdt_balance -= (margin + fee)

            # تسجيل الصفقة المفتوحة
            self._positions[symbol] = {
                'symbol':        symbol,
                'side':          'long',
                'entry_price':   fill_price,
                'amount':        amount,
                'contracts':     amount,
                'contractSize':  1,
                'notional':      notional,
                'margin_locked': margin,
                'fee_entry':     fee,
                'leverage':      self.MAX_LEVERAGE,
                'stop_loss':     None,
                'take_profit_1': None,
                'take_profit_2': None,
                'opened_at':     datetime.utcnow().isoformat(),
            }

            logger.info(
                f"🟢 [Paper] فتح LONG | {symbol} | "
                f"السعر: ${fill_price:,.2f} | "
                f"الكمية: {amount:.4f} | "
                f"الهامش: ${margin:,.2f} | "
                f"الرسوم: ${fee:.2f}"
            )

        # ── إغلاق صفقة (LONG) ───────────────────────
        elif side == 'sell':
            pos = self._positions.pop(symbol, None)

            if not pos:
                logger.warning(
                    f"⚠️ [Paper] لا توجد صفقة مفتوحة لـ {symbol}"
                )
                return {}

            # حساب الربح والخسارة
            exit_fee = self._calculate_fee(fill_price * amount)
            raw_pnl  = (fill_price - pos['entry_price']) * amount
            net_pnl  = raw_pnl - exit_fee

            # استرداد الهامش + PnL
            self._usdt_balance += pos['margin_locked'] + net_pnl

            pnl_pct = net_pnl / (pos['entry_price'] * amount) * 100

            # تسجيل في السجل التاريخي
            record = {
                **pos,
                'exit_price':  fill_price,
                'exit_fee':    exit_fee,
                'pnl':         net_pnl,
                'pnl_pct':     pnl_pct,
                'closed_at':   datetime.utcnow().isoformat(),
            }
            self.trade_history.append(record)

            logger.trade_exit(
                symbol, 'long',
                pos['entry_price'], fill_price,
                net_pnl, pnl_pct,
                'PAPER_MARKET'
            )

        return {
            'id':        order_id,
            'symbol':    symbol,
            'side':      side,
            'amount':    amount,
            'price':     fill_price,
            'fee':       fee,
            'timestamp': timestamp,
            'status':    'closed',
            'type':      'market',
        }

    def create_stop_loss_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        stop_price: float
    ) -> Dict:
        """
        تسجيل أمر وقف الخسارة محلياً

        لا يُرسل لأي بورصة — يُفعَّل عند استدعاء check_and_trigger_orders()
        """
        if symbol in self._positions:
            self._positions[symbol]['stop_loss'] = stop_price
            logger.info(
                f"🛑 [Paper] Stop Loss مسجَّل | {symbol} @ ${stop_price:,.2f}"
            )

        return {
            'id':        self._generate_order_id(),
            'type':      'STOP_MARKET',
            'symbol':    symbol,
            'stopPrice': stop_price,
            'status':    'registered',
        }

    def create_take_profit_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        stop_price: float
    ) -> Dict:
        """
        تسجيل أمر هدف الربح محلياً

        الأول يذهب لـ take_profit_1، الثاني لـ take_profit_2
        """
        if symbol in self._positions:
            pos = self._positions[symbol]
            if pos.get('take_profit_1') is None:
                pos['take_profit_1'] = stop_price
                label = 'TP1'
            else:
                pos['take_profit_2'] = stop_price
                label = 'TP2'

            logger.info(
                f"🎯 [Paper] {label} مسجَّل | "
                f"{symbol} @ ${stop_price:,.2f}"
            )

        return {
            'id':        self._generate_order_id(),
            'type':      'TAKE_PROFIT_MARKET',
            'symbol':    symbol,
            'stopPrice': stop_price,
            'status':    'registered',
        }

    def cancel_order(self, symbol: str, order_id: str) -> Dict:
        """إلغاء أمر مسجَّل"""
        logger.debug(f"[Paper] إلغاء أمر {order_id}")
        return {'id': order_id, 'status': 'cancelled'}

    # ═══════════════════════════════════════════════════
    # إدارة الصفقات المفتوحة
    # ═══════════════════════════════════════════════════

    def get_positions(self) -> List[Dict]:
        """
        الحصول على جميع الصفقات المفتوحة

        نفس صيغة CCXT fetch_positions()
        """
        return [
            {
                **pos,
                'contracts':    pos['amount'],
                'contractSize': 1,
                'unrealizedPnl': self._get_unrealized_pnl(pos),
            }
            for pos in self._positions.values()
        ]

    def get_position_by_symbol(self, symbol: str) -> Optional[Dict]:
        """الحصول على صفقة محددة بالزوج"""
        pos = self._positions.get(symbol)
        if pos:
            return {
                **pos,
                'contracts':    pos['amount'],
                'contractSize': 1,
            }
        return None

    def close_position(
        self,
        symbol: str,
        position_side: str = None
    ) -> Dict:
        """إغلاق صفقة بالكامل بأمر سوق"""
        pos = self._positions.get(symbol)
        if not pos:
            logger.warning(
                f"⚠️ [Paper] لا توجد صفقة مفتوحة لـ {symbol}"
            )
            return {}
        return self.create_market_order(symbol, 'sell', pos['amount'])

    def _get_unrealized_pnl(self, pos: dict) -> float:
        """حساب الربح/الخسارة غير المحقق للصفقة المفتوحة"""
        try:
            ticker = self.fetch_ticker(pos['symbol'])
            current_price = ticker.get('close', pos['entry_price'])
            return (current_price - pos['entry_price']) * pos['amount']
        except Exception:
            return 0.0

    # ═══════════════════════════════════════════════════
    # التحقق من SL/TP وتفعيلها (يُستدعى من حلقة البوت)
    # ═══════════════════════════════════════════════════

    def check_and_trigger_orders(self) -> List[Dict]:
        """
        يجب استدعاؤه في كل دورة من حلقة البوت الرئيسية.

        يتحقق من الأسعار الحالية ويُفعّل SL/TP إذا تحقق شرطها.
        يُعيد قائمة الصفقات التي أُغلقت في هذه الدورة.

        مثال الاستخدام في bot.py:
            triggered = exchange.check_and_trigger_orders()
            for event in triggered:
                logger.info(f"صفقة أُغلقت: {event}")
        """
        triggered = []

        for symbol in list(self._positions.keys()):
            pos = self._positions.get(symbol)
            if not pos:
                continue

            try:
                ticker        = self.fetch_ticker(symbol)
                current_price = ticker.get('close', 0)

                if current_price <= 0:
                    continue

                sl  = pos.get('stop_loss')
                tp1 = pos.get('take_profit_1')
                tp2 = pos.get('take_profit_2')

                reason = None

                # تحقق من شروط الخروج بالأولوية
                if sl and current_price <= sl:
                    reason = 'STOP_LOSS'
                elif tp2 and current_price >= tp2:
                    reason = 'TAKE_PROFIT_2'
                elif tp1 and current_price >= tp1:
                    reason = 'TAKE_PROFIT_1'

                if reason:
                    logger.info(
                        f"🔔 [Paper] {reason} تفعَّل! | "
                        f"{symbol} | السعر: ${current_price:,.2f}"
                    )
                    order = self.create_market_order(
                        symbol, 'sell', pos['amount']
                    )
                    triggered.append({
                        'symbol': symbol,
                        'reason': reason,
                        'price':  current_price,
                        'order':  order,
                    })

            except Exception as e:
                logger.error(
                    f"❌ [Paper] خطأ في check_and_trigger_orders: {e}"
                )

        return triggered

    # ═══════════════════════════════════════════════════
    # إحصائيات وتقارير أداء التداول الورقي
    # ═══════════════════════════════════════════════════

    def get_paper_stats(self) -> Dict:
        """
        ملخص شامل لأداء التداول الورقي

        Returns:
            dict بجميع مؤشرات الأداء
        """
        if not self.trade_history:
            return {
                'status':           'لا توجد صفقات مغلقة بعد',
                'initial_balance':  self.initial_balance,
                'current_balance':  self._usdt_balance,
                'open_positions':   len(self._positions),
            }

        pnls   = [t['pnl'] for t in self.trade_history]
        wins   = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]

        total_pnl    = sum(pnls)
        current_bal  = self._usdt_balance + sum(
            p.get('margin_locked', 0) for p in self._positions.values()
        )

        profit_factor = (
            abs(sum(wins)) / abs(sum(losses))
            if losses and sum(losses) != 0
            else float('inf')
        )

        return {
            'initial_balance':  self.initial_balance,
            'current_balance':  current_bal,
            'total_pnl':        total_pnl,
            'roi_pct':          total_pnl / self.initial_balance * 100,
            'total_trades':     len(self.trade_history),
            'winning_trades':   len(wins),
            'losing_trades':    len(losses),
            'win_rate_pct':     len(wins) / len(self.trade_history) * 100,
            'avg_win':          sum(wins)   / len(wins)   if wins   else 0,
            'avg_loss':         sum(losses) / len(losses) if losses else 0,
            'profit_factor':    profit_factor,
            'open_positions':   len(self._positions),
            'api_calls_made':   self.api_call_count,
        }

    def print_paper_stats(self):
        """طباعة تقرير الأداء بشكل منسق"""
        stats = self.get_paper_stats()

        print("\n" + "═" * 60)
        print("📊 تقرير التداول الورقي (Paper Trading)")
        print("═" * 60)

        if 'status' in stats and 'لا توجد' in stats['status']:
            print(f"\n⏳ {stats['status']}")
            print(f"   💰 الرصيد الحالي: ${stats['current_balance']:,.2f}")
            print(f"   📂 صفقات مفتوحة: {stats['open_positions']}")
            print("═" * 60)
            return

        print(f"\n💰 الأرصدة:")
        print(f"   الأولي:  ${stats['initial_balance']:>12,.2f}")
        print(f"   الحالي:  ${stats['current_balance']:>12,.2f}")
        pnl_sign = '+' if stats['total_pnl'] >= 0 else ''
        print(f"   إجمالي PnL: {pnl_sign}${stats['total_pnl']:>10,.2f}  "
              f"({pnl_sign}{stats['roi_pct']:.2f}%)")

        print(f"\n📈 الصفقات:")
        print(f"   الإجمالي:     {stats['total_trades']:>6}")
        print(f"   رابحة:        {stats['winning_trades']:>6}  "
              f"({stats['win_rate_pct']:.1f}%)")
        print(f"   خاسرة:        {stats['losing_trades']:>6}")

        print(f"\n🎯 الجودة:")
        print(f"   متوسط الربح:  ${stats['avg_win']:>10,.2f}")
        print(f"   متوسط الخسارة:${stats['avg_loss']:>10,.2f}")
        print(f"   Profit Factor: {stats['profit_factor']:>9.2f}")

        print("═" * 60)

    # ═══════════════════════════════════════════════════
    # دوال توافق إضافية (Compatibility Shims)
    # ═══════════════════════════════════════════════════

    def round_quantity(self, symbol: str, quantity: float) -> float:
        return round(quantity, 3)

    def round_price(self, symbol: str, price: float) -> float:
        return round(price, 2)

    def is_open_market(self) -> bool:
        """العملات الرقمية مفتوحة 24/7"""
        return True

    def get_server_time(self) -> datetime:
        return datetime.utcnow()

    def get_api_status(self) -> str:
        return "✅ Paper Trading — بدون أي مخاطر مالية"

    def __repr__(self) -> str:
        return (
            f"PaperTradingExchange("
            f"balance=${self._usdt_balance:,.2f}, "
            f"positions={len(self._positions)}, "
            f"trades={len(self.trade_history)})"
        )