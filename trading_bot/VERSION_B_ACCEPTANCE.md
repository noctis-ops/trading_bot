# Frozen Version B Acceptance Gate

This document defines the machine-checkable gate required before any Historical or Paper performance baseline is collected.

## Current remediation gate record

As of 2026-09-01, the automated gate reports **PASS** with 50 mandatory
assertions passed, 0 failed, 0 errors, 0 skipped, and one non-blocking pandas
PyArrow deprecation warning. `baseline_collected` remains `false`.

| Acceptance surface | Status | Evidence / limitation |
|---|---|---|
| Full Version B path | `PASS` | Deterministic gate suite, including explicit TradingBot B wiring |
| Backtest ↔ Paper replay parity | `PASS` | Shared replay engine and identical decision/lifecycle fixture |
| Runtime/exchange protection acknowledgement | `UNKNOWN` | Network-free acceptance intentionally does not claim exchange evidence |
| Restart reconciliation against an exchange | `UNKNOWN` | Durable DB reconstruction is covered; live exchange hydration is not |

Statuses are intentionally reported separately as `PASS`, `FAIL`, `BLOCKED`, or
`UNKNOWN`; a residual `UNKNOWN` outside the mandatory deterministic path does
not authorize Live.

## Required result

The gate must report exactly:

- `PASS` only when every mandatory test passes and there are no unresolved critical exceptions.
- `FAIL` otherwise, including test count, passed count, failed count, names, reasons, and known exceptions.

## Mandatory groups

- Data: 15m decision clock, closed-candle alignment at or before `T`, warm-up, stale data, gaps, UTC semantics.
- Indicators: one provider and deterministic outputs.
- Strategy: differential/golden fixtures for Long, Short, gates, score, gate strength, effective score, MTF, and 5m entry invariance.
- Risk: planned risk, realized net PnL, daily loss, cooldown, consecutive loss, portfolio risk-at-stop.
- Execution: entry, fill, partial fill, TP1 idempotency, TP1-to-TP2 lifecycle, BE, trailing, reversal, SL, emergency, unknown order state, fees/slippage, intrabar rule.
- Persistence: IDs, uniqueness, event ordering, decision snapshot reconstruction, restart/recovery, open lifecycle preservation.
- Backtest: no lookahead, 15m cadence, 5m exit path only, explicit stop-first rule, gap handling, end-of-data handling.
- Paper: deterministic replay, accounting reconciliation, leverage semantics, lifecycle parity.
- Parity: same event stream produces equivalent strategy decision and lifecycle state behavior.
- Reports: chronological order, lifecycle aggregation, gross/net/cost separation, open positions separated.

No baseline is allowed before this document is satisfied by the automated
command below. The command is deterministic and does not download market data
or create a performance baseline:

```bash
cd trading_bot
python version_b_acceptance.py
```

The final line is exactly `PASS` or `FAIL`. `PASS` requires the immutable
Version A commit/config checks and every mandatory Version B test group to pass;
otherwise the gate returns `FAIL` with test counts, failed test names, reasons,
and known exceptions. A passing gate authorizes the next historical-baseline
step only; it does not authorize Live. See `VERSION_B_SCOPE.md` and
`VERSION_B_LIVE_BLOCKERS.md`.
