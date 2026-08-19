"""
═══════════════════════════════════════════════════════════
سكربت تحليل الأداء — Performance Report
═══════════════════════════════════════════════════════════
يقرأ قاعدة البيانات (data/trading_bot.db) ويحسب كل مؤشرات الأداء
المطلوبة للحكم على نجاح البوت بموضوعية، ويقارنها بمعايير النجاح
الموثّقة في STRATEGY_DEEP_ANALYSIS.md.

المؤشرات المحسوبة:
    - Win Rate / Profit Factor / P&L الصافي / Max Drawdown
    - توزيع Long vs Short
    - توزيع الأزواج (symbols)
    - توزيع أسباب الخروج (SL / TP1 / TP2)
    - مقارنة تلقائية بمعايير النجاح (✅/❌)

الاستخدام:
    python performance_report.py                 ← كل الصفقات (paper+live)
    python performance_report.py --env paper     ← paper فقط
    python performance_report.py --limit 100     ← آخر 100 صفقة
    python performance_report.py --json          ← إخراج JSON (للأتمتة)

معايير النجاح (من STRATEGY_DEEP_ANALYSIS.md):
    Win Rate          > 55%
    Profit Factor     > 1.5
    Max Drawdown      < 15%
    P&L الصافي        > 0
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

# إضافة المسار
sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd

from database.models import Trade, Signal, DailyPerformance, init_db, close_db


# ─────────────────────────────────────────────────────────
# معايير النجاح (قابلة للتعديل هنا أو عبر معاملات)
# ─────────────────────────────────────────────────────────

SUCCESS_CRITERIA = {
    'win_rate_min':        55.0,   # %
    'profit_factor_min':   1.5,
    'max_drawdown_max':    15.0,   # %
    'pnl_min':             0.0,    # $
    'min_trades_for_judge': 100,   # عدد الصفقات للحكم النهائي
}


# ═══════════════════════════════════════════════════════════
# حساب Max Drawdown من قائمة الصفقات
# ═══════════════════════════════════════════════════════════

def compute_max_drawdown(trades_df: pd.DataFrame) -> float:
    """
    حساب أقصى تراجع (Max Drawdown) من منحنى الرصيد المتزايد.

    نفترض رصيداً ابتدائياً = 100 (نسبة مئوية نسبية) ونبني منحنى
    الأسهم بجمع P&L تراكمياً، ثم نحسب أقصى هبوط من القمة.

    Returns:
        float: Max Drawdown كنسبة مئوية (موجبة)
    """
    if trades_df.empty:
        return 0.0
    equity = trades_df['pnl'].cumsum()
    # نعتبر الرصيد الابتدائي 100 (نسبة نسبية)
    equity = equity + 100.0
    peak = equity.cummax()
    dd = (equity - peak) / peak * 100
    return float(abs(dd.min()))


# ═══════════════════════════════════════════════════════════
# بناء التقرير
# ═══════════════════════════════════════════════════════════

def build_report(trades_df: pd.DataFrame, signals_count: int, criteria: dict) -> dict:
    """
    بناء تقرير الأداء الكامل من DataFrame الصفقات.

    Args:
        trades_df: DataFrame بصفوف Trade (pnl, side, symbol, exit_reason, ...)
        signals_count: عدد الإشارات المسجلة (من جدول signals)
        criteria: معايير النجاح

    Returns:
        dict تقرير الأداء الكامل
    """
    total = len(trades_df)

    if total == 0:
        return {
            'status': 'no_trades',
            'total_trades': 0,
            'message': 'لا توجد صفقات بعد — أدر البوت على Paper وانتظر صفقات.',
        }

    pnls = trades_df['pnl']
    wins = pnls[pnls > 0]
    losses = pnls[pnls <= 0]

    total_pnl = float(pnls.sum())

    profit_factor = (
        float(abs(wins.sum()) / abs(losses.sum()))
        if len(losses) > 0 and losses.sum() != 0
        else float('inf')
    )

    win_rate = float(len(wins) / total * 100)

    max_dd = compute_max_drawdown(trades_df)

    # توزيع Long/Short
    if 'side' in trades_df.columns:
        side_dist = trades_df['side'].value_counts().to_dict()
    else:
        side_dist = {}

    # توزيع الأزواج
    if 'symbol' in trades_df.columns:
        symbol_dist = trades_df['symbol'].value_counts().to_dict()
    else:
        symbol_dist = {}

    # توزيع أسباب الخروج
    if 'exit_reason' in trades_df.columns:
        exit_dist = trades_df['exit_reason'].value_counts().to_dict()
    else:
        exit_dist = {}

    # ── مقارنة بمعايير النجاح ──────────────────────────
    verdict = {}
    verdict['win_rate'] = win_rate > criteria['win_rate_min']
    verdict['profit_factor'] = profit_factor > criteria['profit_factor_min']
    verdict['max_drawdown'] = max_dd < criteria['max_drawdown_max']
    verdict['pnl'] = total_pnl > criteria['pnl_min']

    enough_trades = total >= criteria['min_trades_for_judge']
    passed_all = all(verdict.values())
    final = 'PASS' if (enough_trades and passed_all) else 'PENDING/FAIL'

    return {
        'status': 'completed',
        'total_trades': total,
        'winning_trades': int(len(wins)),
        'losing_trades': int(len(losses)),
        'win_rate': round(win_rate, 2),
        'profit_factor': round(profit_factor, 3),
        'total_pnl': round(total_pnl, 4),
        'max_drawdown_pct': round(max_dd, 2),
        'avg_win': round(float(wins.mean()), 4) if len(wins) > 0 else 0,
        'avg_loss': round(float(losses.mean()), 4) if len(losses) > 0 else 0,
        'side_distribution': side_dist,
        'symbol_distribution': symbol_dist,
        'exit_reason_distribution': exit_dist,
        'signals_count': signals_count,
        'criteria': criteria,
        'verdict': verdict,
        'enough_trades': enough_trades,
        'final_verdict': final,
    }


# ═══════════════════════════════════════════════════════════
# الطباعة المنسقة
# ═══════════════════════════════════════════════════════════

def print_report(report: dict):
    """طباعة التقرير بشكل منسق وجميل."""
    print("\n" + "═" * 66)
    print("📊 تقرير أداء التداول — Performance Report")
    print("═" * 66)

    if report.get('status') == 'no_trades':
        print(f"\n⏳ {report['message']}")
        print("═" * 66)
        return

    # ── المؤشرات الرئيسية ────────────────────────────
    print(f"\n💰 المؤشرات الرئيسية:")
    print(f"   صفقات مغلقة:     {report['total_trades']}")
    print(f"   رابحة / خاسرة:   {report['winning_trades']} / {report['losing_trades']}")

    wr = report['win_rate']
    wr_ok = report['verdict']['win_rate']
    print(f"   Win Rate:         {wr:.1f}%  {'✅' if wr_ok else '❌'} "
          f"(الحد: >{report['criteria']['win_rate_min']}%)")

    pf = report['profit_factor']
    pf_str = '∞' if pf == float('inf') else f'{pf:.2f}'
    pf_ok = report['verdict']['profit_factor']
    print(f"   Profit Factor:    {pf_str}  {'✅' if pf_ok else '❌'} "
          f"(الحد: >{report['criteria']['profit_factor_min']})")

    dd = report['max_drawdown_pct']
    dd_ok = report['verdict']['max_drawdown']
    print(f"   Max Drawdown:     {dd:.2f}%  {'✅' if dd_ok else '❌'} "
          f"(الحد: <{report['criteria']['max_drawdown_max']}%)")

    pnl = report['total_pnl']
    pnl_sign = '+' if pnl >= 0 else ''
    pnl_ok = report['verdict']['pnl']
    print(f"   P&L الصافي:       {pnl_sign}${pnl:,.2f}  {'✅' if pnl_ok else '❌'}")

    # ── توزيعات ────────────────────────────────────────
    print(f"\n📈 توزيع الاتجاه (Long/Short):")
    for k, v in report['side_distribution'].items():
        print(f"   {k}: {v}")

    print(f"\n🔀 توزيع الأزواج:")
    for k, v in report['symbol_distribution'].items():
        print(f"   {k}: {v}")

    print(f"\n🎯 أسباب الخروج:")
    labels = {
        'STOP_LOSS': 'Stop Loss', 'TAKE_PROFIT_1': 'TP1',
        'TAKE_PROFIT_2': 'TP2', 'EMERGENCY': 'طوارئ',
    }
    for k, v in report['exit_reason_distribution'].items():
        print(f"   {labels.get(k, k)}: {v}")

    # ── الحكم النهائي ──────────────────────────────────
    print(f"\n{'═' * 66}")
    if report['enough_trades']:
        if report['final_verdict'] == 'PASS':
            print("🏆 الحكم: نجاح ✅ — اجتاز كل المعايير، جاهز للتجربة على Live بمبلغ صغير.")
        else:
            print("🔴 الحكم: لم ينجح بعد — افحص المعايير الفاشلة وعدّل المعاملات.")
    else:
        need = report['criteria']['min_trades_for_judge'] - report['total_trades']
        print(f"⏳ حكم مؤجل: يحتاج {need} صفقة إضافية "
              f"(الحد الأدنى {report['criteria']['min_trades_for_judge']} صفقة).")
    print("═" * 66 + "\n")


# ═══════════════════════════════════════════════════════════
# نقطة الدخول
# ═══════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        prog='performance_report.py',
        description='تحليل أداء البوت من قاعدة البيانات',
    )
    parser.add_argument('--env', default=None, choices=['paper', 'live'],
                        help='فلترة بالبيئة (افتراضي: الكل)')
    parser.add_argument('--limit', type=int, default=None,
                        help='عدد الصفقات الأخيرة فقط')
    parser.add_argument('--json', action='store_true',
                        help='إخراج JSON (للأتمتة)')
    parser.add_argument('--db', default=None,
                        help='مسار قاعدة بيانات مخصص')
    args = parser.parse_args()

    # تهيئة قاعدة البيانات
    if args.db:
        import os
        os.environ['DB_PATH_OVERRIDE'] = args.db
    init_db()

    # جلب الصفقات
    query = Trade.select().order_by(Trade.closed_at.desc())
    if args.env:
        query = query.where(Trade.environment == args.env)
    if args.limit:
        query = query.limit(args.limit)

    trades = list(query)
    close_db()

    # تحويل إلى DataFrame
    rows = []
    for t in trades:
        rows.append({
            'symbol': t.symbol,
            'side': t.side,
            'environment': t.environment,
            'entry_price': t.entry_price,
            'exit_price': t.exit_price,
            'pnl': t.pnl,
            'pnl_pct': t.pnl_pct,
            'exit_reason': t.exit_reason,
            'signal_score': t.signal_score,
            'rr_ratio': t.rr_ratio,
            'opened_at': t.opened_at,
            'closed_at': t.closed_at,
        })
    trades_df = pd.DataFrame(rows)

    # عدد الإشارات (إعادة فتح لقراءة signals)
    init_db()
    signals_count = Signal.select().count()
    close_db()

    report = build_report(trades_df, signals_count, SUCCESS_CRITERIA)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        print_report(report)


if __name__ == '__main__':
    main()
