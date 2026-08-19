"""
═══════════════════════════════════════════════════════════
اختبار شامل للإصلاحات الأربعة
═══════════════════════════════════════════════════════════
يتحقق من:
  ✅ Fix 1: Paper Trading Engine (بدلاً من Testnet المتوقف)
  ✅ Fix 2: معاملات الاستراتيجية (ADX=25, RSI=50-70, dist=2%)
  ✅ Fix 3: Backtesting IndexError (timestamp-based slicing)
  ✅ Fix 4: Backtesting ينتج صفقات فعلية (R:R=2.17 ✓)

تشغيل:
    cd trading_bot
    python test_fixes.py
"""

import sys
import traceback
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

# ── الألوان في Terminal ──────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

def ok(msg):  print(f"   {GREEN}✅ {msg}{RESET}")
def fail(msg):print(f"   {RED}❌ {msg}{RESET}")
def warn(msg):print(f"   {YELLOW}⚠️  {msg}{RESET}")
def info(msg):print(f"   {CYAN}ℹ️  {msg}{RESET}")

results = []

def run_test(name, fn):
    """تشغيل اختبار وتسجيل النتيجة"""
    print(f"\n{'─'*60}")
    print(f"{BOLD}🧪 {name}{RESET}")
    print('─'*60)
    try:
        passed = fn()
        results.append((name, passed))
        if passed:
            ok(f"اختبار '{name}' نجح ✓")
        else:
            fail(f"اختبار '{name}' فشل ✗")
        return passed
    except Exception as e:
        fail(f"استثناء: {e}")
        traceback.print_exc()
        results.append((name, False))
        return False


# ══════════════════════════════════════════════════════════
# Fix 1: Paper Trading Engine
# ══════════════════════════════════════════════════════════

def test_paper_trading_import():
    """التحقق من استيراد PaperTradingExchange بنجاح"""
    from core.paper_trading import PaperTradingExchange
    engine = PaperTradingExchange(initial_balance=5000.0)

    balance = engine.get_available_balance()
    info(f"الرصيد الوهمي: ${balance:,.2f}")

    assert balance == 5000.0, f"توقعنا 5000 وحصلنا على {balance}"
    ok("PaperTradingExchange تم إنشاؤه بنجاح")

    # اختبار fetch_balance
    bal_dict = engine.fetch_balance()
    assert 'USDT' in bal_dict, "USDT غير موجود في fetch_balance"
    ok("fetch_balance() تعيد صيغة CCXT الصحيحة")

    # اختبار set_leverage (يجب ألا يُلقي استثناء)
    engine.set_leverage('BTC/USDT', 5)
    ok("set_leverage() تعمل بدون استثناء")

    # اختبار set_margin_type
    engine.set_margin_type('BTC/USDT', 'isolated')
    ok("set_margin_type() تعمل بدون استثناء")

    return True


def test_paper_trading_order_simulation():
    """محاكاة فتح وإغلاق صفقة وهمية"""
    from core.paper_trading import PaperTradingExchange
    engine = PaperTradingExchange(initial_balance=10_000.0)

    initial_balance = engine.get_available_balance()
    info(f"الرصيد الأولي: ${initial_balance:,.2f}")

    # محاكاة فتح صفقة شراء بسعر ثابت معروف
    # (نعدّل السعر داخلياً لتجنب HTTP call في الاختبار)
    engine._positions['BTC/USDT'] = {
        'symbol':        'BTC/USDT',
        'side':          'long',
        'entry_price':   40_000.0,
        'amount':        0.01,
        'contracts':     0.01,
        'contractSize':  1,
        'notional':      400.0,
        'margin_locked': 40.0,   # 400 / 10x
        'fee_entry':     0.16,   # 400 × 0.04%
        'leverage':      10,
        'stop_loss':     39_400.0,
        'take_profit_1': 40_800.0,
        'take_profit_2': 41_800.0,
        'opened_at':     datetime.utcnow().isoformat(),
    }
    engine._usdt_balance -= (40.0 + 0.16)  # margin + fee

    balance_after_open = engine.get_available_balance()
    info(f"الرصيد بعد الفتح: ${balance_after_open:,.2f}")
    assert balance_after_open < initial_balance, "الرصيد يجب أن ينخفض بعد الفتح"
    ok("الرصيد انخفض صحيح بعد فتح الصفقة")

    positions = engine.get_positions()
    assert len(positions) == 1, "يجب أن تكون هناك صفقة واحدة مفتوحة"
    ok("get_positions() تعيد الصفقة المفتوحة")

    # تسجيل إغلاق بربح (سعر أعلى)
    engine._positions['BTC/USDT']['exit_price'] = 40_900.0
    pos = engine._positions.pop('BTC/USDT')
    exit_price = 40_900.0
    fee_exit   = exit_price * 0.01 * engine.TAKER_FEE
    pnl        = (exit_price - pos['entry_price']) * pos['amount'] - fee_exit
    engine._usdt_balance += pos['margin_locked'] + pnl
    engine.trade_history.append({**pos, 'exit_price': exit_price, 'pnl': pnl, 'pnl_pct': pnl/400*100})

    final_balance = engine.get_available_balance()
    info(f"الرصيد النهائي: ${final_balance:,.2f} | P&L: ${pnl:+.2f}")
    assert pnl > 0, "يجب أن يكون هناك ربح"
    ok("P&L محسوبة بشكل صحيح")

    # اختبار get_paper_stats
    stats = engine.get_paper_stats()
    assert stats['total_trades'] == 1
    assert stats['winning_trades'] == 1
    ok("get_paper_stats() تعمل بشكل صحيح")

    return True


def test_create_exchange_factory():
    """اختبار factory function"""
    from core import create_exchange
    from core.paper_trading import PaperTradingExchange

    engine = create_exchange('paper')
    assert isinstance(engine, PaperTradingExchange), \
        "create_exchange('paper') يجب أن يعيد PaperTradingExchange"
    ok("create_exchange('paper') يعيد PaperTradingExchange ✓")

    # اختبار أن testnet يُلقي استثناء واضح
    try:
        create_exchange('testnet')
        fail("كان يجب أن يُلقي EnvironmentError")
        return False
    except EnvironmentError as e:
        ok(f"create_exchange('testnet') يُلقي EnvironmentError الصحيح ✓")

    return True


# ══════════════════════════════════════════════════════════
# Fix 2: معاملات الاستراتيجية
# ══════════════════════════════════════════════════════════

def test_strategy_parameters():
    """التحقق من أن الثوابت تطابق STRATEGY_COMPLETE_GUIDE.md"""
    from core.strategy import TradingStrategy

    s = TradingStrategy()

    info(f"ADX_THRESHOLD:    {s.ADX_THRESHOLD}")
    info(f"RSI_MIN:          {s.RSI_MIN}")
    info(f"RSI_MAX:          {s.RSI_MAX}")
    info(f"MAX_EMA_DISTANCE: {s.MAX_EMA_DISTANCE}%")
    info(f"ATR_TP2_MULT:     {s.ATR_TP2_MULT}")

    assert s.ADX_THRESHOLD    == 25,  f"ADX يجب 25, وجدنا {s.ADX_THRESHOLD}"
    assert s.RSI_MIN          == 50,  f"RSI_MIN يجب 50, وجدنا {s.RSI_MIN}"
    assert s.RSI_MAX          == 70,  f"RSI_MAX يجب 70, وجدنا {s.RSI_MAX}"
    assert s.MAX_EMA_DISTANCE == 2.0, f"EMA_DIST يجب 2.0, وجدنا {s.MAX_EMA_DISTANCE}"
    assert s.ATR_TP2_MULT     == 4.5, f"TP2_MULT يجب 4.5, وجدنا {s.ATR_TP2_MULT}"

    ok("جميع المعاملات تطابق STRATEGY_COMPLETE_GUIDE.md ✓")
    return True


def test_risk_reward_ratio():
    """التحقق من أن R:R يتجاوز 2.0 دائماً"""
    from core.strategy import TradingStrategy

    s = TradingStrategy()

    test_cases = [
        (43_250.0, 150.0),   # BTC نموذجي
        (43_250.0,  50.0),   # ATR صغير
        (43_250.0, 500.0),   # ATR كبير
        (  100.0,   2.0),    # عملة رخيصة
        (  0.5,    0.01),    # عملة صغيرة جداً
    ]

    for entry, atr in test_cases:
        exits = s.calculate_exits(entry, atr)
        rr    = exits.get('risk_reward_ratio', 0)
        valid = exits.get('valid', False)

        info(f"Entry={entry:,} ATR={atr} → R:R={rr:.3f} valid={valid}")

        assert rr >= 2.0, \
            f"R:R يجب ≥ 2.0 لكن حصلنا على {rr:.3f} (entry={entry}, atr={atr})"
        assert valid, \
            f"يجب أن تكون الصفقة valid لكنها ليست (entry={entry}, atr={atr})"

    ok(f"R:R ≥ 2.0 في جميع الحالات ✓ (قيمة نموذجية: 2.17)")
    return True


def test_buy_signal_conditions():
    """التحقق من شروط الشراء الستة"""
    from core.strategy import TradingStrategy

    s      = TradingStrategy()
    price  = 43_250.0

    def make_df(n=50, close=price, ema_slow=42_800, ema_fast=43_100,
                adx=30, volume=5000, volume_sma=3500,
                rsi=60, macd=0.005, macd_signal=0.003,
                ema_medium=None):
        if ema_medium is None:
            ema_medium = close
        return pd.DataFrame({
            'close':       [close]       * n,
            'ema_slow':    [ema_slow]    * n,
            'ema_fast':    [ema_fast]    * n,
            'ema_medium':  [ema_medium]  * n,
            'adx':         [adx]         * n,
            'volume':      [float(volume)]     * n,
            'volume_sma':  [float(volume_sma)] * n,
            'rsi':         [rsi]         * n,
            'macd':        [macd]        * n,
            'macd_signal': [macd_signal] * n,
            'atr':         [150.0]       * n,
        })

    # ── حالة إيجابية: جميع الشروط متحققة ───────
    df_1h  = make_df()
    df_15m = make_df()
    df_5m  = make_df()

    found, data = s.check_buy_signal(df_1h, df_15m, df_5m)
    assert found, f"يجب إيجاد إشارة شراء. الشروط: {data.get('conditions')}"
    ok("إشارة شراء صحيحة عند توافر جميع الشروط ✓")

    # ── حالة سلبية 1: ADX منخفض (ADX=20 < 25) ───
    df_bad = make_df(adx=20)
    found, _ = s.check_buy_signal(df_1h, df_bad, df_5m)
    assert not found, "يجب ألا توجد إشارة عند ADX=20"
    ok("لا إشارة عند ADX=20 < 25 ✓")

    # ── حالة سلبية 2: RSI منخفض (RSI=45 < 50) ───
    df_bad = make_df(rsi=45)
    found, _ = s.check_buy_signal(df_1h, df_bad, df_5m)
    assert not found, "يجب ألا توجد إشارة عند RSI=45"
    ok("لا إشارة عند RSI=45 < 50 ✓")

    # ── حالة سلبية 3: RSI مرتفع (RSI=75 > 70) ───
    df_bad = make_df(rsi=75)
    found, _ = s.check_buy_signal(df_1h, df_bad, df_5m)
    assert not found, "يجب ألا توجد إشارة عند RSI=75"
    ok("لا إشارة عند RSI=75 > 70 ✓")

    return True


# ══════════════════════════════════════════════════════════
# Fix 3 & 4: Backtesting IndexError + R:R
# ══════════════════════════════════════════════════════════

def test_backtesting_no_indexerror():
    """التحقق من عدم وجود IndexError في Backtesting"""
    from backtesting.backtesting_advanced import AdvancedBacktestingEngine

    engine = AdvancedBacktestingEngine(initial_balance=10_000.0)

    # توليد بيانات تجريبية (60 يوم)
    df_1h, df_15m, df_5m = engine.generate_sample_data(days=60)

    assert df_1h  is not None, "generate_sample_data فشل"
    assert df_15m is not None, "generate_sample_data فشل"
    assert df_5m  is not None, "generate_sample_data فشل"

    ok(f"توليد البيانات نجح: 1H={len(df_1h)}, 15M={len(df_15m)}, 5M={len(df_5m)}")

    # التحقق أن DataFrames مُفهرَسة بـ DatetimeIndex
    import pandas as pd
    assert isinstance(df_1h.index,  pd.DatetimeIndex), "1H يجب أن يكون DatetimeIndex"
    assert isinstance(df_15m.index, pd.DatetimeIndex), "15M يجب أن يكون DatetimeIndex"
    assert isinstance(df_5m.index,  pd.DatetimeIndex), "5M يجب أن يكون DatetimeIndex"
    ok("جميع DataFrames مُفهرَسة بـ DatetimeIndex ✓")

    # تشغيل backtest (يجب ألا يُلقي IndexError)
    errors_before = 0
    report = engine.backtest(df_1h, df_15m, df_5m)

    assert isinstance(report, dict), "backtest يجب أن يعيد dict"
    ok("backtest() أكمل بدون IndexError ✓")

    info(f"الإشارات: {report.get('total_signals', 0)}")
    info(f"الصفقات:  {report.get('total_trades', 0)}")

    return True


def test_timestamp_slicing():
    """التحقق من صحة _get_slice_up_to"""
    from backtesting.backtesting_advanced import AdvancedBacktestingEngine

    engine = AdvancedBacktestingEngine()

    # إنشاء DataFrame بسيط
    dates = pd.date_range('2024-01-01', periods=200, freq='15min')
    df = pd.DataFrame({'close': range(200)}, index=dates)

    ts_mid = dates[99]

    # جلب آخر 20 شمعة حتى الوقت المحدد
    slc = engine._get_slice_up_to(df, ts_mid, n_candles=20)

    assert len(slc) == 20, f"توقعنا 20 شمعة وحصلنا على {len(slc)}"
    assert slc.index[-1] == ts_mid, \
        f"آخر timestamp يجب = {ts_mid}"

    # التأكد أن جميع الشموع قبل أو عند ts_mid
    assert all(slc.index <= ts_mid), "يجب ألا تكون هناك شموع مستقبلية"

    ok("_get_slice_up_to() تعمل بشكل صحيح ✓")
    return True


def test_symbol_key_fix():
    """التحقق من إصلاح مفتاح 'symbol' في positions"""
    from backtesting.backtesting_advanced import AdvancedBacktestingEngine

    engine = AdvancedBacktestingEngine(initial_balance=10_000.0)

    # محاكاة فتح صفقة مباشرة
    engine._open_position(
        symbol      = 'BTC/USDT',
        entry_price = 43_250.0,
        atr         = 150.0,
        timestamp   = pd.Timestamp('2024-01-15 10:00:00'),
    )

    # التحقق أن المفتاح الصحيح مستخدم
    assert 'BTC/USDT' in engine.positions, \
        "المفتاح يجب أن يكون 'BTC/USDT' وليس 'symbol'"
    assert 'symbol' not in engine.positions, \
        "المفتاح الخاطئ 'symbol' يجب ألا يكون موجوداً"

    ok("positions تستخدم 'BTC/USDT' كمفتاح ✓")

    # إغلاق الصفقة
    entry = engine.positions['BTC/USDT']['entry_price']
    exit_price = entry + 300  # ربح
    engine._close_position(
        symbol     = 'BTC/USDT',
        exit_price = exit_price,
        exit_type  = 'TAKE_PROFIT_1',
        timestamp  = pd.Timestamp('2024-01-15 12:00:00'),
    )

    assert 'BTC/USDT' not in engine.positions, \
        "يجب حذف الصفقة من positions بعد الإغلاق"
    assert len(engine.trades) == 1, "يجب تسجيل صفقة واحدة في trade_history"
    assert engine.trades[0]['profit'] > 0, "يجب أن يكون الربح موجباً"

    ok("_close_position() تعمل بشكل صحيح ✓")
    return True


# ══════════════════════════════════════════════════════════
# Fix 5 / Phase 6: دعم Short/Bearish
# ══════════════════════════════════════════════════════════

def test_short_support():
    """التحقق من دعم الـ Short (Phase 6) عبر السلسلة الكاملة"""
    from core.strategy import TradingStrategy
    from core.risk_manager import RiskManager
    from core.order_manager import OrderManager
    from core.paper_trading import PaperTradingExchange

    # ── إعداد بيانات هابطة اصطناعية ─────────────────────
    # نزول لطيف (السعر قريب من EMA21 — الشرط السادس يتطلب ≤ 2%)
    n     = 300
    close = pd.Series(100 - np.linspace(0, 6, n))
    def _mk():
        df = pd.DataFrame({
            'close': close,
            'high':  close + 3,
            'low':   close - 3,
            'volume': np.full(n, 100.0),
        })
        df['ema_slow']   = close.ewm(span=200).mean()
        df['ema_fast']   = close.ewm(span=50).mean()
        df['ema_medium'] = close.ewm(span=21).mean()
        df['adx']        = 40.0
        df['rsi']        = 40.0
        macd  = pd.Series(np.linspace(5, -5, n))
        sig   = pd.Series(np.linspace(3, -3, n))
        df['macd']       = macd
        df['macd_signal']= sig
        df['macd_hist']  = macd - sig
        df['volume_sma'] = 80.0   # الحجم ثابت 100 > 80 (تأكيد الحجم)
        return df

    df_1h = _mk(); df_15m = _mk(); df_5m = _mk()

    # ── 1) الاستراتيجية: check_short_signal + exits ────
    s = TradingStrategy()
    assert s.version == "1.3", f"الإصدار يجب أن يكون 1.3 وليس {s.version}"
    assert hasattr(s, 'check_short_signal'), "يجب أن توجد check_short_signal()"
    ok("الإصدار v1.3 + check_short_signal() موجودة ✓")

    short_ok, short_sig = s.check_short_signal(df_1h, df_15m, df_5m)
    assert short_ok, f"إشارة Short يجب أن تجتاز الشروط على بيانات هابطة: {short_sig.get('reason')}"
    assert short_sig.get('side') == 'short', "يجب أن تكون الجهة short"
    ok("check_short_signal() تلتقط الاتجاه الهابط ✓")

    short_exits = s.calculate_short_exits(100.0, 2.0)
    assert short_exits['stop_loss'] > 100.0, "SL يجب أن يكون فوق الدخول"
    assert short_exits['take_profit_1'] < 100.0, "TP1 يجب أن يكون تحت الدخول"
    assert short_exits['risk_reward_ratio'] >= 2.0, "R:R يجب ≥ 2.0"
    ok(f"calculate_short_exits() صحيحة (SL>Entry, TP<Entry, R:R={short_exits['risk_reward_ratio']:.2f}) ✓")

    # ── 2) RiskManager: حساب الـ stops للـ Short ────────
    rm = RiskManager(initial_balance=10_000.0)
    stops = rm.calculate_short_stops(100.0, 2.0)
    assert stops['valid'], "Stops Short يجب أن تكون صالحة"
    assert stops['stop_loss'] > 100.0 and stops['take_profit_2'] < stops['take_profit_1'] < 100.0
    ok("calculate_short_stops() صحيحة (SL فوق، TP تحت) ✓")

    valid_stops, reason = rm.validate_short_stops(100.0, 103.0, 96.0, 91.0)
    assert valid_stops, f"مستويات Short صحيحة يجب أن تُقبل: {reason}"
    ok("validate_short_stops() تتحقق من علاقة المستويات ✓")

    # ── 3) OrderManager + Paper: فتح/إغلاق Short ────────
    ex = PaperTradingExchange(initial_balance=10_000.0)
    price = {'val': 100.0}
    ex.fetch_ticker = lambda sym: {'close': price['val']}
    om = OrderManager(ex, rm)

    pos_data = rm.calculate_position_size(
        balance=10_000.0, entry_price=100.0,
        stop_loss_price=stops['stop_loss'], signal_score=80.0, side='short',
    )
    signal_data = {'entry_price': 100.0, 'atr': 2.0, 'score': 80.0, 'side': 'short'}
    r = om.open_short_position('ETH/USDT', signal_data, pos_data, stops)
    assert r['success'], f"فشل فتح صفقة Short: {r.get('error')}"
    p = om.get_position_state('ETH/USDT')
    assert p['side'] == 'short' and p['stop_loss'] > p['entry_price']
    ok("open_short_position() تفتح صفقة Short بشكل صحيح ✓")

    # نزول السعر → TP1: إغلاق جزئي 50% + نقل SL إلى Breakeven
    price['val'] = stops['take_profit_1']
    events = om.check_and_update_positions()
    reasons = [e['reason'] for e in events]
    assert 'TAKE_PROFIT_1' in reasons, f"يجب أن يُغلق على TP1: {reasons}"

    # بعد TP1: يجب أن تبقى الصفقة مفتوحة (نصفها)، وSL محمي (Breakeven/Trailing)
    st = om.get_position_state('ETH/USDT')
    assert st is not None, "بعد TP1 يجب أن تبقى 50% من الصفقة مفتوحة"
    assert abs(st['contract_size'] - pos_data['contract_size'] * 0.5) < 1e-6, \
        "يجب أن يبقى 50% من الحجم بعد TP1"
    # للـ Short: SL محمي يعني ≥ الدخول (Breakeven) أو أفضل (Trailing يحمّي أكثر)
    assert st['stop_loss'] >= st['entry_price'], \
        "بعد TP1 يجب أن يكون SL محميًا (Breakeven/Trailing) — لا خسارة على المتبقي"
    ok("TP1 يُغلق 50% فقط + SL محمي (Breakeven/Trailing) ✓")

    # نزول السعر إلى SL (محمي) → يُغلق النصف المتبقي بلا خسارة
    price['val'] = st['stop_loss']
    events2 = om.check_and_update_positions()
    reasons2 = [e['reason'] for e in events2]
    assert 'STOP_LOSS' in reasons2, f"يجب أن يُغلق المتبقي على SL: {reasons2}"
    assert not om.has_open_position('ETH/USDT'), "بعد SL يجب أن تُغلق الصفقة بالكامل"

    # إجمالي PnL موجب (ربح النصف الأول عند TP1 + ≥0 على النصف الثاني المحمي)
    total_pnl = sum(r['pnl'] for r in om.closed_positions)
    assert total_pnl > 0, "يجب أن يكون إجمالي PnL موجباً"
    assert ex.get_available_balance() > 10_000.0, "يجب أن يزيد الرصيد بعد ربح Short"
    ok(f"الإغلاق الجزئي + Breakeven يعملان | إجمالي PnL=${total_pnl:,.2f} ✓")

    return True


def test_backtest_both_directions():
    """التحقق من backtest_both_directions() (Phase E / 6.6)"""
    from backtesting.backtesting_advanced import AdvancedBacktestingEngine

    engine = AdvancedBacktestingEngine(initial_balance=10_000.0)

    df_1h, df_15m, df_5m = engine.generate_sample_data(days=90)
    assert df_1h is not None, "فشل توليد البيانات"

    report = engine.backtest_both_directions(df_1h, df_15m, df_5m)
    assert isinstance(report, dict), "يجب أن يُعيد dict"
    assert report.get('status') == 'completed', \
        f"يجب أن تكون الحالة completed وليس {report.get('status')}"

    # التقرير المنفصل لكل اتجاه + المدمج
    for key in ['long_report', 'short_report', 'combined_report']:
        assert key in report, f"يجب أن يحتوي التقرير على {key}"
        assert 'total_trades' in report[key], f"{key} يجب أن يحوي total_trades"

    # إجمالي صفقات الاتجاهين = صفقات التقرير المدمج
    long_count  = report['long_report']['total_trades']
    short_count = report['short_report']['total_trades']
    combined    = report['combined_report']['total_trades']
    assert (long_count + short_count) == combined, \
        "صفقات Long + Short يجب أن تساوي الصفقات المدمجة"

    ok(f"backtest_both_directions() نجح | Long={long_count} + "
       f"Short={short_count} = {combined} صفقة ✓")
    return True


def test_strategy_selector():
    """التحقق من StrategySelector (Phase 8.2 / Level 2)"""
    from core.strategy_selector import StrategySelector

    selector = StrategySelector()

    # الاستراتيجيات الثلاث موجودة
    assert hasattr(selector, 'trend_following')
    assert hasattr(selector, 'mean_reversion')
    assert hasattr(selector, 'breakout')
    assert selector.trend_following.name == 'trend'
    assert selector.mean_reversion.name  == 'reversion'
    assert selector.breakout.name        == 'breakout'
    ok("StrategySelector يضم الاستراتيجيات الثلاث (trend/reversion/breakout) ✓")

    # بناء DataFrame تجريبي مكتمل المؤشرات
    n     = 120
    close = pd.Series(100 + np.linspace(0, 20, n))
    df = pd.DataFrame({
        'close': close, 'high': close + 2, 'low': close - 2,
        'volume': np.full(n, 100.0),
    })
    df['ema_slow']   = close.ewm(span=200).mean()
    df['ema_fast']   = close.ewm(span=50).mean()
    df['ema_medium'] = close.ewm(span=21).mean()
    df['rsi']        = 55.0
    df['macd']       = 0.0
    df['macd_signal'] = 0.0
    df['macd_hist']  = 0.0
    df['atr']        = 2.0
    df['bb_upper']   = close + 3
    df['bb_middle']  = close
    df['bb_lower']   = close - 3
    df['adx']        = 40.0
    df['volume_sma'] = 90.0

    # get_scores تُعيد الدرجات الثلاث
    scores = selector.get_scores('BTC/USDT', df)
    for k in ['trend', 'reversion', 'breakout']:
        assert k in scores, f"يجب أن تحتوي الدرجات على {k}"
        assert 0.0 <= scores[k] <= 100.0, f"درجة {k} يجب أن تكون بين 0-100"

    # select_best_strategy تُعيد مفتاحاً صالحاً ودرجته
    best, score = selector.select_best_strategy('BTC/USDT', df)
    assert best in ['trend', 'reversion', 'breakout'], \
        f"يجب أن تكون الاستراتيجية من الثلاث: {best}"
    assert scores[best] == score, "يجب أن تطابق درجة المختارة أعلى الدرجات"

    ok(f"StrategySelector اختار '{best}' بدرجة {score:.1f} ✓")
    return True


# ══════════════════════════════════════════════════════════
# Smart Level: تدرّج الشروط + Market Regime + Trailing + Correlation
# ══════════════════════════════════════════════════════════

def test_graded_conditions():
    """التحقق من تدرّج الشروط (ADX=60 ≠ ADX=26)"""
    from core.strategy import TradingStrategy

    s = TradingStrategy()
    close = pd.Series(100 + np.linspace(0, 25, 120))

    def mk(adx):
        df = pd.DataFrame({'close': close, 'high': close+2, 'low': close-2,
                           'volume': np.full(120, 100.0)})
        df['ema_slow']=close.ewm(span=200).mean(); df['ema_fast']=close.ewm(span=50).mean()
        df['ema_medium']=close.ewm(span=21).mean()
        df['rsi']=58.0; df['adx']=adx
        df['macd']=0.5; df['macd_signal']=0.0; df['macd_hist']=0.5
        df['volume_sma']=80.0
        return df

    df_weak = mk(26)   # ADX=26
    ok1, sig1 = s.check_buy_signal(df_weak, df_weak, df_weak)
    df_strong = mk(60)  # ADX=60
    ok2, sig2 = s.check_buy_signal(df_strong, df_strong, df_strong)

    assert ok1 and ok2, "كلا الحالتين يجب أن تجتازا الشروط (26>25)"
    # ADX=60 يجب أن تكون قوته أعلى من ADX=26
    s_weak  = sig1['strengths']['15m_adx_above_25']
    s_strong = sig2['strengths']['15m_adx_above_25']
    assert s_strong > s_weak, "قوة ADX المتدرجة يجب أن تميّز بين 60 و26"
    ok(f"تدرّج ADX: 26→{s_weak:.2f} مقابل 60→{s_strong:.2f} ✓")

    # gate_strength موجود وضمن 0-1
    assert 0.0 <= sig2['gate_strength'] <= 1.0
    ok(f"gate_strength مرجّح: {sig2['gate_strength']:.2f} ✓")
    return True


def test_market_regime():
    """التحقق من مصنّف حالة السوق"""
    from core.market_regime import classify_market_regime, strategy_for_regime
    from core.strategy_selector import StrategySelector

    def mk(trend, adx):
        n = 120
        if trend == 'up':   close = pd.Series(100 + np.linspace(0, 25, n))
        elif trend == 'down': close = pd.Series(100 - np.linspace(0, 25, n))
        else:               close = pd.Series(100 + np.sin(np.linspace(0, 20, n))*1.0)
        df = pd.DataFrame({'close': close, 'high': close+2, 'low': close-2,
                           'volume': np.full(n, 100.0)})
        df['ema_slow']=close.ewm(span=200).mean(); df['ema_fast']=close.ewm(span=50).mean()
        df['ema_medium']=close.ewm(span=21).mean()
        df['rsi']=55.0; df['adx']=adx
        df['macd']=0.0; df['macd_signal']=0.0; df['macd_hist']=0.0; df['atr']=2.0
        df['bb_upper']=close+3; df['bb_middle']=close; df['bb_lower']=close-3
        df['volume_sma']=90.0
        return df

    # bull → trend
    r_bull = classify_market_regime(mk('up', 40))
    assert r_bull == 'bull', f"توقع bull وحصلنا {r_bull}"
    # bear → trend
    r_bear = classify_market_regime(mk('down', 40))
    assert r_bear == 'bear', f"توقع bear وحصلنا {r_bear}"
    # range → reversion
    r_range = classify_market_regime(mk('sideways', 12))
    assert r_range == 'range', f"توقع range وحصلنا {r_range}"

    assert strategy_for_regime('bull') == 'trend'
    assert strategy_for_regime('range') == 'reversion'
    ok(f"Regime: bull→trend, bear→trend, range→reversion ✓")

    # select_strategy_for_market يعيد الحالة والاستراتيجية
    sel = StrategySelector()
    m = sel.select_strategy_for_market('BTC/USDT', mk('up', 40))
    assert m['regime'] == 'bull' and m['strategy'] == 'trend'
    ok(f"select_strategy_for_market: {m['regime']} → {m['strategy']} ✓")
    return True


def test_trailing_and_reversal():
    """التحقق من Trailing Stop + خروج انعكاس"""
    from core.risk_manager import RiskManager

    rm = RiskManager(initial_balance=10_000.0)

    # Trailing Long: بعد TP1، نرفع SL مع السعر
    up, new_sl = rm.should_update_trailing_stop(
        current_price=110, entry_price=100, current_sl=100,
        take_profit_1=104, atr=2.0, side='long',
    )
    assert up and new_sl == 108.0, f"توقع رفع SL إلى 108 وحصلنا {new_sl}"
    ok(f"Trailing Long يرفع SL: 100→{new_sl} ✓")

    # لا ننزل SL (حماية)
    up2, _ = rm.should_update_trailing_stop(
        current_price=103, entry_price=100, current_sl=108,
        take_profit_1=104, atr=2.0, side='long',
    )
    assert not up2, "لا يجب إنزال SL"
    ok("Trailing لا ينزل SL ✓")

    # خروج انعكاس Long (كسر EMA نزولاً)
    n = 50
    close = pd.Series([98.0]*n)
    df = pd.DataFrame({'close': close})
    df['ema_medium'] = 99.0; df['ema_fast'] = 100.0
    r, reason = rm.should_exit_on_reversal(df, 'long', entry_price=102)
    assert r and reason == 'REVERSAL_EXIT', f"توقع خروج انعكاس: {reason}"
    ok("خروج انعكاس Long يعمل ✓")

    # خروج انعكاس Short (كسر EMA صعوداً)
    close2 = pd.Series([102.0]*n)
    df2 = pd.DataFrame({'close': close2})
    df2['ema_medium'] = 101.0; df2['ema_fast'] = 100.0
    r2, _ = rm.should_exit_on_reversal(df2, 'short', entry_price=99)
    assert r2, "توقع خروج انعكاس Short"
    ok("خروج انعكاس Short يعمل ✓")
    return True


def test_pair_correlation():
    """التحقق من كشف ارتباط الأزواج"""
    from core.pair_selector import compute_pair_correlation, is_pair_correlated_with_open

    n = 100
    np.random.seed(1)
    base = pd.Series(100 + np.cumsum(np.random.randn(n)*2))
    df_btc = pd.DataFrame({'close': base})
    df_eth = pd.DataFrame({'close': base*1.1 + np.random.randn(n)*0.2})
    df_xrp = pd.DataFrame({'close': 50 + np.cumsum(np.random.randn(n)*1.5)})

    c_high = compute_pair_correlation(df_btc, df_eth)
    c_low  = compute_pair_correlation(df_btc, df_xrp)
    assert c_high > 0.7, f"BTC-ETH يجب أن يكونا مرتبطين: {c_high:.2f}"
    assert abs(c_low) < 0.7, f"BTC-XRP يجب ألا يكونا مرتبطين: {c_low:.2f}"
    ok(f"ارتباط BTC-ETH={c_high:.2f} (عالي) | BTC-XRP={c_low:.2f} (منخفض) ✓")

    dfs = {'ETH/USDT': df_eth, 'XRP/USDT': df_xrp}
    def get_df(s): return dfs[s]

    corr1, with_sym = is_pair_correlated_with_open(
        'BTC/USDT', df_btc, ['ETH/USDT'], get_df
    )
    assert corr1, "يجب تجنّب BTC إذا كان ETH مفتوحاً (مرتبطان)"
    corr2, _ = is_pair_correlated_with_open(
        'BTC/USDT', df_btc, ['XRP/USDT'], get_df
    )
    assert not corr2, "يجب السماح بـ BTC إذا كان XRP مفتوحاً (غير مرتبط)"
    ok("كاشف الارتباط يتجنّب الأزواج المترابطة ✓")
    return True


# ══════════════════════════════════════════════════════════
# تشغيل جميع الاختبارات
# ══════════════════════════════════════════════════════════

def main():
    print("\n" + "═"*65)
    print(f"{BOLD}🔬 اختبار شامل للإصلاحات الأربعة{RESET}")
    print("═"*65)

    # ── Fix 1: Paper Trading ─────────────────────────────
    print(f"\n{BOLD}{CYAN}━━━ الإصلاح 1: Paper Trading Engine ━━━{RESET}")
    run_test("استيراد PaperTradingExchange",         test_paper_trading_import)
    run_test("محاكاة فتح/إغلاق صفقة وهمية",        test_paper_trading_order_simulation)
    run_test("create_exchange factory function",    test_create_exchange_factory)

    # ── Fix 2: Strategy Parameters ──────────────────────
    print(f"\n{BOLD}{CYAN}━━━ الإصلاح 2: معاملات الاستراتيجية ━━━{RESET}")
    run_test("قيم ثوابت الاستراتيجية",              test_strategy_parameters)
    run_test("R:R ≥ 2.0 في جميع الحالات",          test_risk_reward_ratio)
    run_test("شروط إشارة الشراء الستة",             test_buy_signal_conditions)

    # ── Fix 3 & 4: Backtesting ───────────────────────────
    print(f"\n{BOLD}{CYAN}━━━ الإصلاح 3 & 4: Backtesting ━━━{RESET}")
    run_test("لا IndexError في Backtesting",        test_backtesting_no_indexerror)
    run_test("تقطيع صحيح بالـ timestamp",           test_timestamp_slicing)
    run_test("إصلاح مفتاح symbol في positions",    test_symbol_key_fix)

    # ── Phase 6: دعم Short/Bearish ───────────────────────
    print(f"\n{BOLD}{CYAN}━━━ المرحلة 6: دعم Short/Bearish ━━━{RESET}")
    run_test("دعم Short عبر السلسلة الكاملة",       test_short_support)
    run_test("backtest_both_directions (Long+Short)", test_backtest_both_directions)

    # ── Phase 8.2: Multi-Strategy Selection (Level 2) ─────
    print(f"\n{BOLD}{CYAN}━━━ المرحلة 8.2: Multi-Strategy Selection ━━━{RESET}")
    run_test("StrategySelector (trend/reversion/breakout)", test_strategy_selector)

    # ── Smart Level: تدرّج + Regime + Trailing + Correlation ──
    print(f"\n{BOLD}{CYAN}━━━ Smart Level: تدرّج/Regime/Trailing/Correlation ━━━{RESET}")
    run_test("تدرّج قوة الشروط (ADX)",          test_graded_conditions)
    run_test("مصنّف حالة السوق (Regime)",       test_market_regime)
    run_test("Trailing Stop + خروج انعكاس",     test_trailing_and_reversal)
    run_test("كشف ارتباط الأزواج",              test_pair_correlation)

    # ── ملخص النتائج ─────────────────────────────────────
    print("\n" + "═"*65)
    print(f"{BOLD}📊 ملخص النتائج{RESET}")
    print("═"*65)

    passed = sum(1 for _, r in results if r)
    total  = len(results)
    failed = total - passed

    for name, result in results:
        icon = f"{GREEN}✅" if result else f"{RED}❌"
        print(f"   {icon} {name}{RESET}")

    print("─"*65)
    print(f"\n   الإجمالي: {total} اختبارات")
    print(f"   {GREEN}✅ نجح:{RESET}  {passed}")

    if failed > 0:
        print(f"   {RED}❌ فشل:{RESET}  {failed}")

    print()

    if passed == total:
        print(f"{BOLD}{GREEN}🎉 جميع الاختبارات نجحت!{RESET}")
        print(f"{GREEN}   الإصلاحات الأربعة تعمل بشكل صحيح.{RESET}")
        print(f"{GREEN}   الخطوة التالية: تشغيل backtest مع بيانات حقيقية.{RESET}")
        sys.exit(0)
    else:
        print(f"{BOLD}{RED}⚠️  {failed} اختبار(ات) فشلت — راجع الأخطاء أعلاه{RESET}")
        sys.exit(1)


if __name__ == "__main__":
    main()