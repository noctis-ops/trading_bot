"""
═══════════════════════════════════════════════════════════
مدير الأوامر — OrderManager
═══════════════════════════════════════════════════════════
الجسر الآمن بين قرار الاستراتيجية وتنفيذ الصفقة الفعلي.

المسؤوليات:
    ① تنفيذ الأوامر بأمان مع Retry تلقائي
    ② فتح صفقة كاملة (Market + SL + TP1 + TP2) بخطوة واحدة
    ③ إدارة SL/TP (تعديل، إلغاء، نقل إلى Breakeven)
    ④ مراقبة الصفقات المفتوحة وإغلاقها تلقائياً
    ⑤ معالجة الأخطاء والانقطاعات بشكل احترافي
    ⑥ إغلاق طارئ لجميع الصفقات

══════════════════════════════════════════
الموقع في معمارية البوت:
══════════════════════════════════════════

    TradingStrategy          ← يقرر "هل ندخل؟"
         ↓
    RiskManager              ← يقرر "كم الحجم والـ SL/TP؟"
         ↓
    OrderManager             ← ينفذ "الأوامر الفعلية"
         ↓
    Exchange                 ← PaperTradingExchange أو BinanceExchange

══════════════════════════════════════════
التوافق:
══════════════════════════════════════════
    يعمل مع كلا النوعين بدون تعديل:
    - PaperTradingExchange (TRADING_MODE=paper)
    - BinanceExchange      (TRADING_MODE=live)

    الاكتشاف تلقائي: إذا كان Exchange يدعم check_and_trigger_orders()
    فهو Paper Trading وتُعامَل SL/TP كـ registered values.
    إذا لم يدعمها فهو Live وتُرسَل أوامر حقيقية للبورصة.
"""

import time
import yaml
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from utils.logger import logger

# ─────────────────────────────────────────────────────────
# قراءة الإعدادات
# ─────────────────────────────────────────────────────────

CONFIG_PATH = Path(__file__).parent.parent / 'config.yaml'

with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
    _config = yaml.safe_load(f)

_risk_cfg = _config.get('risk_management', {})
_adv_cfg  = _config.get('advanced',        {})


# ═══════════════════════════════════════════════════════════
# فئة OrderManager
# ═══════════════════════════════════════════════════════════

class OrderManager:
    """
    مدير الأوامر المركزي.

    يضمن:
    ─────
    • أن كل صفقة تُفتح مع SL وTP دائماً (لا صفقة بدون حماية)
    • إعادة المحاولة عند أي فشل في الاتصال
    • تسجيل دقيق لكل أمر وكل تغيير في الحالة
    • إغلاق آمن يُعيد الهامش حتى عند الفشل الجزئي
    """

    # ── إعدادات الـ Retry ───────────────────────────────
    MAX_RETRY_ATTEMPTS = int(_config.get('exchange', {}).get('retry_attempts', 3))
    RETRY_BASE_DELAY   = 1.0    # ثانية — يتضاعف مع كل محاولة فاشلة
    RETRY_MAX_DELAY    = 10.0   # أقصى تأخير بين المحاولات (ثانية)

    # ── إعدادات إدارة الرافعة ──────────────────────────
    MAX_LEVERAGE       = int(_risk_cfg.get('max_leverage', 10))
    MARGIN_TYPE        = 'isolated'   # isolated أكثر أماناً من crossed

    # ── إعدادات خاصة بالـ Paper Trading ────────────────
    PAPER_MONITOR_INTERVAL = 0   # ثوانٍ (0 = كل دورة بدون انتظار)

    def __init__(self, exchange, risk_manager):
        """
        تهيئة OrderManager.

        Args:
            exchange:     كائن PaperTradingExchange أو BinanceExchange
            risk_manager: كائن RiskManager للتحقق والتسجيل
        """
        self.exchange     = exchange
        self.risk_manager = risk_manager

        # ── حالة الصفقات المفتوحة ───────────────────────
        # {symbol: PositionState}
        self.open_positions: Dict[str, Dict] = {}

        # ── سجل الصفقات المغلقة ─────────────────────────
        self.closed_positions: List[Dict] = []

        # ── معرّفات الأوامر المعلقة ──────────────────────
        # {symbol: {sl_order_id, tp1_order_id, tp2_order_id}}
        self.pending_orders: Dict[str, Dict] = {}

        # ── كشف نوع الـ Exchange ─────────────────────────
        self._is_paper = hasattr(exchange, 'check_and_trigger_orders')

        mode = "📝 Paper Trading" if self._is_paper else "⚠️ Live Trading"
        logger.success(
            f"✅ OrderManager جاهز | {mode} | "
            f"MaxRetry={self.MAX_RETRY_ATTEMPTS} | "
            f"MaxLev={self.MAX_LEVERAGE}x | "
            f"Margin={self.MARGIN_TYPE}"
        )

    # ═══════════════════════════════════════════════════
    # الواجهة الرئيسية — فتح صفقة كاملة
    # ═══════════════════════════════════════════════════

    def open_position(
        self,
        symbol:        str,
        signal_data:   Dict,
        position_data: Dict,
        exits_data:    Dict,
    ) -> Dict:
        """
        فتح صفقة كاملة بخطوة واحدة آمنة.

        الخطوات الداخلية:
        ─────────────────
        1. التحقق من عدم وجود صفقة مفتوحة مسبقاً
        2. إعداد الرافعة والهامش
        3. تنفيذ أمر الشراء (Market Order)
        4. وضع Stop Loss
        5. وضع Take Profit 1 (50% من الحجم)
        6. وضع Take Profit 2 (50% المتبقي)
        7. تسجيل الصفقة في الحالة الداخلية
        8. عند أي فشل بعد الخطوة 3 → إغلاق تلقائي فوري

        Args:
            symbol:        الزوج (مثال: 'ETH/USDT')
            signal_data:   ناتج strategy.check_buy_signal()
            position_data: ناتج risk_manager.calculate_position_size()
            exits_data:    ناتج risk_manager.calculate_stops()

        Returns:
            dict يحتوي على:
                success:     True/False
                position:    تفاصيل الصفقة المفتوحة
                orders:      معرّفات الأوامر المنفذة
                error:       وصف الخطأ عند الفشل
        """
        # ── التحقق المسبق ──────────────────────────────
        if symbol in self.open_positions:
            msg = f"⚠️ صفقة مفتوحة مسبقاً على {symbol} — يُرفض الفتح"
            logger.warning(msg)
            return {'success': False, 'error': msg}

        if not exits_data.get('valid', False):
            msg = f"❌ exits_data غير صالحة (R:R={exits_data.get('risk_reward_ratio',0):.2f})"
            logger.error(msg)
            return {'success': False, 'error': msg}

        entry_price  = float(signal_data.get('entry_price', 0))
        contract_size = float(position_data.get('contract_size', 0))
        leverage      = int(float(position_data.get('leverage', 1)))
        sl_price      = float(exits_data['stop_loss'])
        tp1_price     = float(exits_data['take_profit_1'])
        tp2_price     = float(exits_data['take_profit_2'])

        if entry_price <= 0 or contract_size <= 0:
            msg = f"❌ بيانات الصفقة غير صالحة: entry={entry_price} | size={contract_size}"
            logger.error(msg)
            return {'success': False, 'error': msg}

        orders_placed: Dict[str, Dict] = {}
        market_order = None

        try:
            # ── الخطوة 1: إعداد الرافعة والهامش ─────────
            self._setup_leverage_and_margin(symbol, leverage)

            # ── الخطوة 2: أمر الشراء بالسوق ─────────────
            logger.info(
                f"📤 فتح LONG | {symbol} | "
                f"${entry_price:,.2f} × {contract_size:.6f} | "
                f"Lev={leverage}x"
            )
            market_order = self._execute_with_retry(
                fn          = lambda: self.exchange.create_market_order(
                    symbol, 'buy', contract_size
                ),
                description = f"Market BUY {symbol}",
                critical    = True,
            )
            if not market_order:
                return {'success': False, 'error': 'فشل أمر الشراء الرئيسي'}

            orders_placed['market'] = market_order
            fill_price = float(market_order.get('price', entry_price))

            # ── الخطوة 3: Stop Loss ───────────────────────
            sl_order = self._place_stop_loss(symbol, sl_price, contract_size)
            if sl_order:
                orders_placed['stop_loss'] = sl_order

            # ── الخطوة 4: Take Profit 1 (50%) ───────────
            size_tp1   = round(contract_size * 0.5, 6)
            tp1_order  = self._place_take_profit(symbol, tp1_price, size_tp1, label='TP1')
            if tp1_order:
                orders_placed['take_profit_1'] = tp1_order

            # ── الخطوة 5: Take Profit 2 (50% المتبقي) ───
            size_tp2   = contract_size - size_tp1
            tp2_order  = self._place_take_profit(symbol, tp2_price, size_tp2, label='TP2')
            if tp2_order:
                orders_placed['take_profit_2'] = tp2_order

            # ── الخطوة 6: تسجيل الصفقة ───────────────────
            position_state = {
                'symbol':        symbol,
                'side':          'long',
                'entry_price':   fill_price,
                'contract_size': contract_size,
                'leverage':      leverage,
                'stop_loss':     sl_price,
                'take_profit_1': tp1_price,
                'take_profit_2': tp2_price,
                'sl_moved_to_be': False,   # هل نُقل SL إلى Breakeven؟
                'tp1_hit':       False,    # هل TP1 تحقق؟
                'signal_score':  signal_data.get('score', 0),
                'rr_ratio':      exits_data.get('risk_reward_ratio', 0),
                'opened_at':     datetime.utcnow().isoformat(),
                'order_ids':     {k: v.get('id', '') for k, v in orders_placed.items()},
            }

            self.open_positions[symbol]  = position_state
            self.pending_orders[symbol]  = {
                'sl_order_id':  sl_order.get('id',  '') if sl_order  else '',
                'tp1_order_id': tp1_order.get('id', '') if tp1_order else '',
                'tp2_order_id': tp2_order.get('id', '') if tp2_order else '',
            }

            logger.trade_entry(
                symbol    = symbol,
                direction = 'long',
                price     = fill_price,
                qty       = contract_size,
                leverage  = leverage,
                tp1       = tp1_price,
                tp2       = tp2_price,
                sl        = sl_price,
            )

            return {
                'success':  True,
                'position': position_state,
                'orders':   orders_placed,
            }

        except Exception as e:
            logger.error(f"❌ خطأ في open_position ({symbol}): {e}")

            # ── الإغلاق الطارئ إذا نُفِّذ أمر الشراء ────
            if market_order:
                logger.warning(
                    f"⚠️ تنفيذ إغلاق طارئ لـ {symbol} "
                    f"بسبب فشل وضع SL/TP"
                )
                self._emergency_close_single(symbol, contract_size)

            return {'success': False, 'error': str(e)}

    # ═══════════════════════════════════════════════════
    # إغلاق الصفقات
    # ═══════════════════════════════════════════════════

    def close_position(
        self,
        symbol: str,
        reason: str,
        partial_size: float = None,
    ) -> Dict:
        """
        إغلاق صفقة بأمر سوق.

        يُستدعى من:
        ─────────────
        • check_and_update_positions() عند تحقق SL/TP
        • risk_manager عند تجاوز الحد اليومي
        • الأوامر اليدوية
        • الإغلاق الطارئ

        Args:
            symbol:       الزوج
            reason:       سبب الإغلاق (للسجلات والـ Telegram)
            partial_size: حجم جزئي للإغلاق (None = كامل الصفقة)

        Returns:
            dict يحتوي على نتيجة الإغلاق
        """
        position = self.open_positions.get(symbol)
        if not position:
            logger.warning(f"⚠️ close_position: لا توجد صفقة مفتوحة لـ {symbol}")
            return {'success': False, 'error': 'لا توجد صفقة مفتوحة'}

        size_to_close = partial_size or position['contract_size']

        try:
            # ── إلغاء الأوامر المعلقة أولاً ──────────────
            self.cancel_all_orders(symbol)

            # ── أمر الإغلاق ─────────────────────────────
            close_order = self._execute_with_retry(
                fn          = lambda: self.exchange.create_market_order(
                    symbol, 'sell', size_to_close
                ),
                description = f"Market SELL {symbol} ({reason})",
                critical    = True,
            )

            if not close_order:
                return {'success': False, 'error': f'فشل أمر الإغلاق ({reason})'}

            exit_price = float(close_order.get('price', 0))
            if exit_price <= 0:
                # استخدم السعر الحالي إذا لم يُعطَ
                try:
                    ticker     = self.exchange.fetch_ticker(symbol)
                    exit_price = float(ticker.get('close', position['entry_price']))
                except Exception:
                    exit_price = float(position['entry_price'])

            # ── حساب الـ PnL ──────────────────────────────
            pnl, pnl_pct = self._calculate_pnl(
                entry_price  = position['entry_price'],
                exit_price   = exit_price,
                contract_size = size_to_close,
                side         = position['side'],
            )

            # ── تسجيل النتيجة في RiskManager ─────────────
            self.risk_manager.register_trade_result(
                symbol    = symbol,
                pnl       = pnl,
                pnl_pct   = pnl_pct,
                exit_type = reason,
                extra     = {
                    'entry_price':   position['entry_price'],
                    'exit_price':    exit_price,
                    'contract_size': size_to_close,
                    'leverage':      position['leverage'],
                    'rr_ratio':      position.get('rr_ratio', 0),
                },
            )

            # ── نقل إلى سجل المغلقة وإزالة من المفتوحة ──
            closed_record = {
                **position,
                'exit_price':  exit_price,
                'exit_reason': reason,
                'pnl':         pnl,
                'pnl_pct':     pnl_pct,
                'closed_at':   datetime.utcnow().isoformat(),
            }
            self.closed_positions.append(closed_record)

            if partial_size is None:
                # إغلاق كامل
                self.open_positions.pop(symbol, None)
                self.pending_orders.pop(symbol, None)
            else:
                # إغلاق جزئي — تحديث الحجم المتبقي
                remaining = position['contract_size'] - size_to_close
                if remaining <= 0:
                    self.open_positions.pop(symbol, None)
                    self.pending_orders.pop(symbol, None)
                else:
                    self.open_positions[symbol]['contract_size'] = remaining

            logger.trade_exit(
                symbol     = symbol,
                direction  = position['side'],
                entry      = position['entry_price'],
                exit_price = exit_price,
                pnl        = pnl,
                pnl_pct    = pnl_pct,
                reason     = reason,
            )

            return {
                'success':    True,
                'pnl':        pnl,
                'pnl_pct':    pnl_pct,
                'exit_price': exit_price,
                'reason':     reason,
            }

        except Exception as e:
            logger.error(f"❌ خطأ في close_position ({symbol}): {e}")
            return {'success': False, 'error': str(e)}

    # ═══════════════════════════════════════════════════
    # مراقبة الصفقات وتحديثها
    # ═══════════════════════════════════════════════════

    def check_and_update_positions(self) -> List[Dict]:
        """
        الدالة الرئيسية لمراقبة الصفقات — تُستدعى في كل دورة من bot.py.

        تعمل بطريقتين حسب نوع الـ Exchange:

        Paper Trading (is_paper = True):
        ──────────────────────────────────
            → استدعاء exchange.check_and_trigger_orders()
            → معالجة الصفقات التي أُغلقت تلقائياً بـ SL/TP
            → مزامنة الحالة الداخلية

        Live Trading (is_paper = False):
        ─────────────────────────────────
            → جلب السعر الحالي لكل زوج
            → مقارنة السعر مع SL/TP المسجَّل
            → إغلاق عند التحقق (يضمن التنفيذ حتى لو فشل أمر البورصة)
            → التحقق من نقل SL إلى Breakeven

        Returns:
            قائمة الصفقات التي أُغلقت في هذه الدورة
        """
        events: List[Dict] = []

        if not self.open_positions:
            return events

        try:
            if self._is_paper:
                events = self._monitor_paper_positions()
            else:
                events = self._monitor_live_positions()

        except Exception as e:
            logger.error(f"❌ خطأ في check_and_update_positions: {e}")

        return events

    def _monitor_paper_positions(self) -> List[Dict]:
        """مراقبة مخصصة لـ Paper Trading — تُفعِّل SL/TP الداخلية."""
        events: List[Dict] = []
        triggered = self.exchange.check_and_trigger_orders()

        for event in triggered:
            sym    = event.get('symbol')
            reason = event.get('reason', 'UNKNOWN')
            price  = float(event.get('price', 0))

            if sym not in self.open_positions:
                continue

            pos = self.open_positions[sym]
            pnl, pnl_pct = self._calculate_pnl(
                entry_price   = pos['entry_price'],
                exit_price    = price,
                contract_size = pos['contract_size'],
                side          = pos['side'],
            )

            self.risk_manager.register_trade_result(
                symbol    = sym,
                pnl       = pnl,
                pnl_pct   = pnl_pct,
                exit_type = reason,
                extra     = {
                    'entry_price':   pos['entry_price'],
                    'exit_price':    price,
                    'contract_size': pos['contract_size'],
                },
            )

            closed_record = {
                **pos,
                'exit_price':  price,
                'exit_reason': reason,
                'pnl':         pnl,
                'pnl_pct':     pnl_pct,
                'closed_at':   datetime.utcnow().isoformat(),
            }
            self.closed_positions.append(closed_record)
            self.open_positions.pop(sym, None)
            self.pending_orders.pop(sym, None)

            events.append({'symbol': sym, 'reason': reason, 'pnl': pnl})
            logger.trade_exit(sym, pos['side'], pos['entry_price'], price, pnl, pnl_pct, reason)

        # ── تحقق من Breakeven لصفقات لم تُغلق بعد ─────
        for sym in list(self.open_positions.keys()):
            self._check_and_apply_breakeven(sym)

        return events

    def _monitor_live_positions(self) -> List[Dict]:
        """مراقبة مخصصة للـ Live Trading — فحص SL/TP يدوياً."""
        events: List[Dict] = []

        for symbol in list(self.open_positions.keys()):
            try:
                pos = self.open_positions.get(symbol)
                if not pos:
                    continue

                ticker        = self.exchange.fetch_ticker(symbol)
                current_price = float(ticker.get('close', 0))

                if current_price <= 0:
                    continue

                sl  = pos.get('stop_loss',     0)
                tp1 = pos.get('take_profit_1', 0)
                tp2 = pos.get('take_profit_2', 0)

                reason = None

                if sl  and current_price <= sl:
                    reason = 'STOP_LOSS'
                elif tp2 and current_price >= tp2:
                    reason = 'TAKE_PROFIT_2'
                elif tp1 and current_price >= tp1 and not pos.get('tp1_hit', False):
                    reason = 'TAKE_PROFIT_1'

                if reason == 'TAKE_PROFIT_1':
                    # إغلاق جزئي (50%) عند TP1
                    result = self.close_position(
                        symbol       = symbol,
                        reason       = reason,
                        partial_size = round(pos['contract_size'] * 0.5, 6),
                    )
                    if result.get('success'):
                        # نقل SL إلى Breakeven
                        if symbol in self.open_positions:
                            self.open_positions[symbol]['tp1_hit']       = True
                            self.open_positions[symbol]['stop_loss']     = pos['entry_price']
                            self.open_positions[symbol]['sl_moved_to_be'] = True
                            self._update_stop_loss_order(symbol, pos['entry_price'])

                        events.append({
                            'symbol': symbol,
                            'reason': reason,
                            'pnl':    result.get('pnl', 0),
                        })

                elif reason in ('STOP_LOSS', 'TAKE_PROFIT_2'):
                    result = self.close_position(symbol, reason)
                    if result.get('success'):
                        events.append({
                            'symbol': symbol,
                            'reason': reason,
                            'pnl':    result.get('pnl', 0),
                        })

                else:
                    # لم يُغلَق — تحقق من Breakeven
                    self._check_and_apply_breakeven(symbol)

            except Exception as e:
                logger.error(f"❌ خطأ في مراقبة {symbol}: {e}")

        return events

    def _check_and_apply_breakeven(self, symbol: str):
        """فحص وتطبيق نقل SL إلى Breakeven للصفقة."""
        pos = self.open_positions.get(symbol)
        if not pos or pos.get('sl_moved_to_be', False):
            return

        try:
            ticker        = self.exchange.fetch_ticker(symbol)
            current_price = float(ticker.get('close', 0))

            should_move, new_sl = self.risk_manager.should_move_sl_to_breakeven(
                current_price = current_price,
                entry_price   = pos['entry_price'],
                take_profit_1 = pos['take_profit_1'],
                current_sl    = pos['stop_loss'],
            )

            if should_move and new_sl:
                self.open_positions[symbol]['stop_loss']     = new_sl
                self.open_positions[symbol]['sl_moved_to_be'] = True
                self._update_stop_loss_order(symbol, new_sl)

        except Exception as e:
            logger.error(f"❌ خطأ في _check_and_apply_breakeven ({symbol}): {e}")

    # ═══════════════════════════════════════════════════
    # وضع/تحديث/إلغاء الأوامر
    # ═══════════════════════════════════════════════════

    def _place_stop_loss(
        self,
        symbol:    str,
        sl_price:  float,
        amount:    float,
    ) -> Optional[Dict]:
        """وضع أمر Stop Loss مع retry."""
        return self._execute_with_retry(
            fn = lambda: self.exchange.create_stop_loss_order(
                symbol, 'sell', amount, sl_price
            ),
            description = f"SL @ ${sl_price:,.2f}",
            critical    = False,   # غير حرج — الصفقة مفتوحة بالفعل
        )

    def _place_take_profit(
        self,
        symbol:   str,
        tp_price: float,
        amount:   float,
        label:    str = 'TP',
    ) -> Optional[Dict]:
        """وضع أمر Take Profit مع retry."""
        return self._execute_with_retry(
            fn = lambda: self.exchange.create_take_profit_order(
                symbol, 'sell', amount, tp_price
            ),
            description = f"{label} @ ${tp_price:,.2f}",
            critical    = False,
        )

    def _update_stop_loss_order(self, symbol: str, new_sl_price: float):
        """
        تحديث أمر SL بأمر جديد (إلغاء القديم + وضع الجديد).
        يُستخدم عند نقل SL إلى Breakeven.
        """
        try:
            pending = self.pending_orders.get(symbol, {})
            old_sl_id = pending.get('sl_order_id', '')

            # إلغاء الأمر القديم
            if old_sl_id:
                self._execute_with_retry(
                    fn          = lambda: self.exchange.cancel_order(symbol, old_sl_id),
                    description = f"إلغاء SL القديم {old_sl_id[:8]}",
                    critical    = False,
                )

            # وضع الأمر الجديد
            pos = self.open_positions.get(symbol, {})
            remaining_size = pos.get('contract_size', 0)

            new_sl_order = self._place_stop_loss(symbol, new_sl_price, remaining_size)
            if new_sl_order and symbol in self.pending_orders:
                self.pending_orders[symbol]['sl_order_id'] = new_sl_order.get('id', '')
                logger.info(
                    f"✅ SL مُحدَّث إلى Breakeven | {symbol} | "
                    f"${new_sl_price:,.2f}"
                )

        except Exception as e:
            logger.error(f"❌ خطأ في _update_stop_loss_order ({symbol}): {e}")

    def cancel_all_orders(self, symbol: str) -> bool:
        """
        إلغاء جميع الأوامر المعلقة للزوج.

        يُستدعى قبل إغلاق الصفقة لتجنب تضارب الأوامر.

        Args:
            symbol: الزوج

        Returns:
            True إذا نجح الإلغاء أو لم تكن هناك أوامر
        """
        pending = self.pending_orders.get(symbol, {})
        if not pending:
            return True

        success = True
        for order_key, order_id in pending.items():
            if not order_id:
                continue
            result = self._execute_with_retry(
                fn          = lambda oid=order_id: self.exchange.cancel_order(symbol, oid),
                description = f"إلغاء {order_key} {order_id[:8]}",
                critical    = False,
            )
            if result is None:
                success = False

        if symbol in self.pending_orders:
            self.pending_orders[symbol] = {
                'sl_order_id':  '',
                'tp1_order_id': '',
                'tp2_order_id': '',
            }

        return success

    # ═══════════════════════════════════════════════════
    # الإغلاق الطارئ
    # ═══════════════════════════════════════════════════

    def emergency_close_all(self, reason: str = 'EMERGENCY') -> List[Dict]:
        """
        إغلاق جميع الصفقات المفتوحة فوراً بأوامر سوق.

        يُستدعى عند:
        ─────────────
        • تجاوز حد الخسارة اليومية
        • خطأ حرج في البوت
        • أمر يدوي من Telegram
        • إيقاف البوت

        Returns:
            قائمة نتائج إغلاق كل صفقة
        """
        if not self.open_positions:
            logger.info("ℹ️ emergency_close_all: لا توجد صفقات مفتوحة")
            return []

        results = []
        symbols = list(self.open_positions.keys())

        logger.warning(
            f"🚨 EMERGENCY CLOSE ALL | "
            f"{len(symbols)} صفقات | السبب: {reason}"
        )

        for symbol in symbols:
            result = self.close_position(symbol, reason=reason)
            results.append({'symbol': symbol, **result})
            time.sleep(0.2)   # تجنب rate limiting

        return results

    def _emergency_close_single(self, symbol: str, contract_size: float):
        """إغلاق طارئ فوري لصفقة واحدة (يُستخدم عند فشل وضع SL/TP)."""
        try:
            self.exchange.create_market_order(symbol, 'sell', contract_size)
            logger.warning(f"🚨 إغلاق طارئ نجح | {symbol}")
        except Exception as e:
            logger.critical(
                f"🚨 إغلاق طارئ فشل! | {symbol} | {e}\n"
                f"   ⚠️ يجب إغلاق الصفقة يدوياً!"
            )

    # ═══════════════════════════════════════════════════
    # الاستعلام عن الحالة
    # ═══════════════════════════════════════════════════

    def get_position_state(self, symbol: str) -> Optional[Dict]:
        """الحصول على حالة صفقة محددة."""
        return self.open_positions.get(symbol)

    def get_all_open_positions(self) -> Dict[str, Dict]:
        """جميع الصفقات المفتوحة."""
        return dict(self.open_positions)

    def has_open_position(self, symbol: str) -> bool:
        """هل هناك صفقة مفتوحة على الزوج؟"""
        return symbol in self.open_positions

    def get_open_positions_count(self) -> int:
        """عدد الصفقات المفتوحة."""
        return len(self.open_positions)

    def get_session_summary(self) -> Dict:
        """
        ملخص جلسة التداول الكاملة.

        يجمع بيانات من:
        ─────────────────
        • الصفقات المغلقة (closed_positions)
        • الصفقات المفتوحة (open_positions)
        • RiskManager stats
        """
        total_pnl   = sum(p.get('pnl', 0) for p in self.closed_positions)
        wins        = [p for p in self.closed_positions if p.get('pnl', 0) > 0]
        losses      = [p for p in self.closed_positions if p.get('pnl', 0) <= 0]

        profit_factor = (
            abs(sum(p['pnl'] for p in wins))   /
            abs(sum(p['pnl'] for p in losses))
            if losses and sum(p['pnl'] for p in losses) != 0
            else float('inf')
        )

        return {
            'total_closed':     len(self.closed_positions),
            'total_open':       len(self.open_positions),
            'winning_trades':   len(wins),
            'losing_trades':    len(losses),
            'win_rate':         len(wins) / len(self.closed_positions) * 100
                                if self.closed_positions else 0,
            'total_pnl':        round(total_pnl, 4),
            'profit_factor':    round(profit_factor, 3),
            'avg_win':          round(sum(p['pnl'] for p in wins)   / len(wins),   4) if wins   else 0,
            'avg_loss':         round(sum(p['pnl'] for p in losses) / len(losses), 4) if losses else 0,
            'is_paper':         self._is_paper,
        }

    def print_session_summary(self):
        """طباعة ملخص الجلسة بشكل منسق."""
        s = self.get_session_summary()
        print("\n" + "═" * 65)
        print("📋 ملخص جلسة OrderManager")
        print("═" * 65)
        mode = "📝 Paper" if s['is_paper'] else "⚠️ Live"
        print(f"  الوضع:         {mode}")
        print(f"  صفقات مغلقة:  {s['total_closed']}")
        print(f"  صفقات مفتوحة: {s['total_open']}")
        print(f"  رابحة:         {s['winning_trades']}  ({s['win_rate']:.1f}%)")
        print(f"  خاسرة:         {s['losing_trades']}")
        pnl_sign = '+' if s['total_pnl'] >= 0 else ''
        print(f"  PnL الكلي:    {pnl_sign}${s['total_pnl']:,.2f}")
        print(f"  Profit Factor: {s['profit_factor']:.2f}")
        print("═" * 65)

    # ═══════════════════════════════════════════════════
    # الـ Retry الذكي
    # ═══════════════════════════════════════════════════

    def _execute_with_retry(
        self,
        fn,
        description: str,
        critical:    bool  = False,
        max_attempts: int  = None,
    ) -> Optional[Dict]:
        """
        تنفيذ دالة API مع إعادة المحاولة التلقائية.

        استراتيجية الـ Retry:
        ──────────────────────
        • Exponential backoff: 1s → 2s → 4s (بحد أقصى 10s)
        • يُميّز بين أخطاء الشبكة (قابلة للإعادة) وأخطاء المنطق (غير قابلة)
        • إذا critical=True والمحاولات كلها فشلت → يرفع استثناء

        Args:
            fn:           الدالة المراد تنفيذها
            description:  وصف للسجلات
            critical:     إذا True وفشل → رفع استثناء
            max_attempts: عدد المحاولات (None = MAX_RETRY_ATTEMPTS)

        Returns:
            نتيجة الدالة عند النجاح، أو None عند الفشل (إذا critical=False)
        """
        attempts = max_attempts or self.MAX_RETRY_ATTEMPTS
        delay    = self.RETRY_BASE_DELAY

        for attempt in range(1, attempts + 1):
            try:
                result = fn()
                if attempt > 1:
                    logger.success(
                        f"✅ {description} نجح بعد {attempt} محاولات"
                    )
                return result

            except Exception as e:
                error_str = str(e).lower()

                # ── أخطاء غير قابلة للإعادة ─────────────
                non_retryable = [
                    'insufficient', 'invalid symbol', 'bad symbol',
                    'minimum', 'precision', 'authorization',
                ]
                if any(kw in error_str for kw in non_retryable):
                    logger.error(
                        f"❌ {description} فشل (غير قابل للإعادة): {e}"
                    )
                    if critical:
                        raise
                    return None

                # ── أخطاء قابلة للإعادة ───────────────────
                if attempt < attempts:
                    actual_delay = min(delay, self.RETRY_MAX_DELAY)
                    logger.warning(
                        f"⚠️ {description} | محاولة {attempt}/{attempts} | "
                        f"خطأ: {type(e).__name__} | إعادة بعد {actual_delay:.1f}s"
                    )
                    time.sleep(actual_delay)
                    delay *= 2   # exponential backoff
                else:
                    logger.error(
                        f"❌ {description} فشل بعد {attempts} محاولات: {e}"
                    )
                    if critical:
                        raise

        return None

    # ═══════════════════════════════════════════════════
    # دوال مساعدة داخلية
    # ═══════════════════════════════════════════════════

    def _setup_leverage_and_margin(self, symbol: str, leverage: int):
        """إعداد الرافعة ونوع الهامش بدون رفع استثناء عند الفشل."""
        try:
            self.exchange.set_leverage(symbol, leverage)
        except Exception as e:
            logger.warning(f"⚠️ set_leverage فشل ({symbol}): {e}")

        try:
            self.exchange.set_margin_type(symbol, self.MARGIN_TYPE)
        except Exception as e:
            logger.warning(f"⚠️ set_margin_type فشل ({symbol}): {e}")

    @staticmethod
    def _calculate_pnl(
        entry_price:   float,
        exit_price:    float,
        contract_size: float,
        side:          str = 'long',
    ) -> Tuple[float, float]:
        """
        حساب الربح/الخسارة لصفقة.

        Args:
            entry_price:   سعر الدخول
            exit_price:    سعر الخروج
            contract_size: حجم العقد
            side:          'long' أو 'short'

        Returns:
            (pnl_usd, pnl_pct)
        """
        if side == 'long':
            pnl = (exit_price - entry_price) * contract_size
        else:
            pnl = (entry_price - exit_price) * contract_size

        cost    = entry_price * contract_size
        pnl_pct = (pnl / cost * 100) if cost > 0 else 0

        return round(pnl, 4), round(pnl_pct, 4)


# ─────────────────────────────────────────────────────────
# اختبار سريع عند التشغيل المباشر
# ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("order_manager.py جاهز للاستخدام")
    print("مثال الاستخدام:")
    print("  from core import create_exchange")
    print("  from core.risk_manager import RiskManager")
    print("  from core.order_manager import OrderManager")
    print()
    print("  exchange = create_exchange()  # paper بالافتراضي")
    print("  rm = RiskManager(initial_balance=10000)")
    print("  om = OrderManager(exchange, rm)")
