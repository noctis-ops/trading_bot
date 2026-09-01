# Version B Scope and Audit Contract

## 1. Reference boundary

Version A is immutable at commit `89bec19164417ca27bb40ea32cc071c8c0300d8a`.
`VERSION_A_MANIFEST.json` is the reconstruction manifest for that commit. No
Version B work may rewrite, reset, force-push, or reinterpret that history.

Version B is a child of Version A and is a **correctness, measurement, and
execution baseline**. It is not an optimization branch. The Version A manifest
remains the source of the actual pre-B behavior; this document describes the
B contract and the currently implemented migration surface.

## 2. Version A effective reference

| Area | Version A reference |
|---|---|
| Runtime selection | `TRADING_MODE` from `.env`, default `paper`; `PAPER_INITIAL_BALANCE` default `10000` |
| Universe | `BTC/USDT`, `ETH/USDT`, `SOL/USDT`, `BNB/USDT`, `DOGE/USDT`; up to five concurrent positions |
| Decision clock | Declared 15M, but legacy Backtest loop was 1H; the manifest records both the declared runtime and actual adapter behavior |
| Trend input | 1H |
| Entry inputs | 15M gates/score; 5M was accepted as an input but did not affect the frozen entry formula |
| Entry rules | Long: price above EMA200, EMA50 above EMA200, ADX above 25, volume above 20-SMA, RSI 50–70, MACD above signal, price within 2% of EMA21. Short is the documented mirror with RSI 30–50 and bearish MACD/trend. |
| Score | Trend 40%, momentum 40%, volatility 20%; minimum 60, strong 80; effective score formula is `score * (0.7 + 0.3 * gate_strength)` where used by the B adapter |
| Exit levels | ATR(14): SL 1.5, TP1 2.0, TP2 4.5; TP1/TP2 allocation 50%/50%; BE after TP1 was enabled in the active behavior |
| Costs | Paper taker fee 0.04% and slippage 0.02%; legacy Backtest omitted costs; legacy Paper used fixed 10x leverage |
| Actual exit behavior | Paper polled ticker close; Backtest polled 1H close. Neither had a written intrabar order, gap, or lifecycle contract. |
| Known defects | See `VERSION_A_MANIFEST.json`: independent candle selection, divergent indicators, 1H/close-based Backtest, retriggerable Paper TP1, fixed Paper leverage, non-critical protection placement, in-memory risk state, weak restart reconciliation, and no durable lifecycle journal. |

The current `config.yaml` remains the business/runtime source. B's extracted
execution assumptions are `execution.fee_rate`, `execution.slippage_rate`,
`execution.model_version`, and the explicit 15M/5M clock names. The Version A
config hash in the manifest is validated from the immutable commit, not from a
rewritten historical file.

## 3. Version B scope

### In scope

1. One canonical indicator provider and aligned data-quality contracts.
2. Frozen Strategy Core adapter for Long/Short gates, score, regime, MTF, and
   5M entry invariance.
3. Canonical accounting: equity, planned risk, realized lifecycle net PnL,
   daily loss, and portfolio risk-at-stop.
4. One execution model: `vb-1.0-5m-stop-first`.
5. One `trade_id` from entry through TP1, BE, trailing, TP2, SL, reversal, or
   emergency close; a partial exit is an event, not a second trade.
6. Durable SQLite IDs and ordered event/fill persistence.
7. Shared deterministic execution service.
8. Explicit Version B Backtest adapter and Paper lifecycle/idempotency fixes.
9. Automated acceptance gate before any historical or Paper performance
   baseline.

### Out of scope until separately approved

- Parameter tuning, optimization, win-rate improvement, increased trade count,
  or deliberate edge changes.
- Changing RSI, ADX, EMA, volume, MACD, score, effective-score, SL/TP, BE,
  trailing, reversal, regime, 5M entry role, or Long/Short policy.
- Choosing parameters from Version B results.
- Calling Paper activity a historical baseline. Paper is operational validation
  only after the acceptance gate.
- Live trading. Live remains blocked by the explicit list in
  `VERSION_B_LIVE_BLOCKERS.md`.

## 4. Version A → Version B change table

| ID | Area | Version A observable behavior | Version B behavior | Classification | Edge changed? |
|---|---|---|---|---|---|
| `VB-DATA-001` | Data | Independent `iloc[-2]`, no single decision timestamp, no freshness/gap contract | UTC-aware closure, aligned timestamp, status and warm-up contracts | Measurement | No |
| `VB-IND-001` | Indicators | Indicator formulas were duplicated across modules | One canonical provider matching the audited formula | Measurement | No |
| `VB-STRAT-001` | Strategy adapter | Live/Backtest called different lower-level paths | One frozen Strategy Core adapter with B context | Measurement | No |
| `VB-RISK-001` | Risk | Risk/accounting semantics were distributed and partial results could be independent | Canonical equity, planned risk, daily loss, portfolio stop-risk, lifecycle net PnL | Measurement / Safety | No |
| `VB-RISK-002` | PnL | Executed-fill PnL could subtract a separately supplied slippage value twice | Executed fill prices include slippage; fees/funding are deducted once; slippage is attribution | Measurement / Execution | No |
| `VB-EXEC-001` | Exits | Ticker/1H close exits with no intrabar rule | 5M only, stop-first, TP1 before TP2, gap/adverse slippage, explicit model version | Execution / Measurement | No |
| `VB-EXEC-002` | Execution | Protection failures could leave an accepted position and adapters had separate state | Shared service plus critical protection confirmation in OrderManager | Safety / Execution | No |
| `VB-LIFE-001` | Lifecycle | TP1/TP2 could be separate trade-like records | One lifecycle and `trade_id`; exits are child events/fills | Execution / Operational | No |
| `VB-DB-001` | Persistence | Best-effort tables without durable event/fill journal or restart identity | Idempotent runs, decisions, lifecycle, order intents, events, fills, and backup API | Persistence / Operational | No |
| `VB-PAPER-001` | Paper | TP1 could retrigger; fixed leverage was applied; partials inflated trade counts | TP1 idempotency, configurable execution costs/leverage, lifecycle aggregation | Execution / Measurement | No |
| `VB-BT-001` | Backtest | 1H loop and close-based exits without costs/model version | Explicit 15M decision / 5M exit adapter using actual next-open entry fills | Measurement / Execution | No |
| `VB-CONFIG-003` | Config | Fee/slippage assumptions lived only as code constants | Same values are explicit runtime config and are snapshotted | Configuration / Measurement | No |

Every row is an observable behavior change and must remain in the Change
Ledger. A Portfolio Risk Cap, if later introduced and it only blocks a trade,
will be logged separately as a **Risk Policy Change**, not as a strategy
population change.

## 5. Frozen parameter sheet

The following values are frozen for Version B. A change to any of them is a
`Strategy` change (or a separately identified `Execution`/`Risk Policy`
change) and stops the affected workstream pending explicit approval.

### Entry and score

| Parameter/role | Frozen value | Source/notes |
|---|---:|---|
| Decision timeframe | `15m` | 15M is the decision clock |
| Trend timeframe | `1h` | Higher-timeframe filter |
| 5M entry role | non-decisional | 5M is never an entry feature |
| EMA fast/slow/medium | 50 / 200 / 21 | `config.yaml`; active gate uses EMA200/EMA50 and EMA21 distance |
| RSI period | 14 | frozen |
| Long RSI range | 50–70 | inclusive gate |
| Short RSI range | 30–50 | inclusive gate |
| MACD | 12 / 26 / 9 | frozen |
| ADX period/threshold | 14 / `>25` | frozen |
| Volume | 15M volume `>` 20-period SMA | `volume_multiplier: 1.5` remains dormant and is not activated |
| EMA21 max distance | 2.0% | frozen |
| Score weights | trend .40, momentum .40, volatility .20 | frozen |
| Score minimum / strong | 60 / 80 | frozen |
| Effective score | `score * (0.7 + 0.3 * gate_strength)` | frozen formula |
| Regime role | direction/filter only | selector must not switch to a tuned strategy |

### Exit, execution, and risk

| Parameter/role | Frozen value | Source/notes |
|---|---:|---|
| SL / TP1 / TP2 | ATR × 1.5 / 2.0 / 4.5 | Long and Short mirrored; no tuning |
| Allocation | TP1 50%, TP2 50% | one lifecycle |
| BE | enabled after TP1 | `move_sl_to_breakeven_after_tp1: true` |
| Execution model | `vb-1.0-5m-stop-first` | fixed identifier in results |
| Intrabar priority | stop first; one event per 5M bar | TP1 before TP2; same-bar TP2 is deferred after TP1 |
| Fee/slippage | 0.0004 / 0.0002 | explicit `execution` config; actual fill price contains slippage |
| Risk/trade | 2.0% | no tuning |
| Daily loss | 6.0% of start-of-day equity | canonical definition |
| Consecutive loss limit | 3 | risk policy, not edge |
| Cooldown | 60 minutes | risk policy |
| Max leverage | 10 | risk policy/execution ceiling |
| Minimum R:R | 2.0 | risk gate |

### Long/Short policy

- Long and Short are separate frozen mirror paths.
- They cannot be silently collapsed into one side or selected by a new
  optimizer.
- A new portfolio cap may block a candidate and must be reported as Risk/Safety
  with its candidate and aggregate risk values.

## 6. Canonical behavior definitions

- **Equity:** wallet value + margin used + unrealized PnL − accrued costs.
- **Free balance:** available wallet value; it is not equity, margin, or
  notional.
- **Margin:** collateral reserved for open notional under leverage.
- **Notional:** executed quantity × executed price.
- **Planned risk:** modeled loss from the actual entry fill to the active
  initial stop, including modeled fees and adverse stop slippage. Gap risk is
  reported separately.
- **Realized net PnL:** lifecycle gross price PnL from actual fills minus fees,
  slippage already reflected by adverse fill prices, and explicit funding. The
  implementation must not double-count slippage.
- **Daily loss:** `max(0, start_of_day_equity - current_equity)`.
- **Portfolio risk-at-stop:** aggregate modeled loss at each current active
  stop plus the candidate position, including modeled costs.
- **Trade:** the complete lifecycle. TP1, BE, trailing, TP2, SL, and reversal
  are events/fills inside that lifecycle.
- **Protection:** no position is accepted without confirmed protection. Failure
  to place or confirm SL is critical and blocks Live.

## 7. Intrabar execution contract

The smallest available bar is 5M. For each bar:

1. If active stop and a target are both touched, stop executes first.
2. If no stop is touched, TP1 executes before TP2.
3. If TP1 and TP2 are both touched before TP1 has been processed, only TP1 is
   emitted; TP2 is deferred to a later bar.
4. Only one lifecycle exit event is emitted per 5M bar.
5. A gap uses the bar open as the base fill; adverse slippage is then applied.
6. A 15M decision enters at the next 15M open in the B historical adapter;
   5M is not passed as an entry feature.

## 8. Hardcoded parameter disposition

### Must be extracted or validated from `config.yaml`

- Fee rate and slippage rate.
- Execution model ID and 15M/5M clock names.
- Risk percentage, daily loss, leverage, cooldown, loss limit, and minimum R:R.
- EMA/RSI/MACD/ADX/ATR/volume periods and gates.
- TP allocation and BE policy.
- Universe and maximum concurrent positions.

### Intentional defaults retained

- `PAPER_INITIAL_BALANCE=10000` is an environment selection default, not a
  strategy parameter.
- Paper `MAX_LEVERAGE=10` is the configured risk ceiling and is used only when
  no per-symbol leverage has been registered.
- `execution.fee_rate=0.0004` and `execution.slippage_rate=0.0002` are explicit
  cost defaults matching Version A Paper behavior; changing them is an
  execution-assumption experiment, not tuning.
- `vb-1.0-5m-stop-first` is an immutable model identifier for comparability.

### Must remain dormant

- `strategy.volume.volume_multiplier: 1.5` is declared metadata only. It must
  not become an active gate without an explicit Strategy change and a new
  baseline lineage.
- ML, hedging, early-loss killing, and other disabled advanced options remain
  disabled.

## 9. Sequence after the gate

`Frozen B → Baseline Acceptance Gate → Historical Baseline → Diagnostics →
Ablation → Sensitivity → OOS/Walk-forward → Paper Operational Validation →
Live Readiness`.

Version B results cannot be used to choose parameters. Paper validation is not
a historical baseline and must include deterministic fixtures, lifecycle/PnL
reconciliation, TP1 idempotency, leverage/accounting parity, and
Paper/Strategy parity.

## 10. Known Unknowns

1. Exchange-specific fill rounding, partial fills, funding, liquidation, and
   mark-price behavior are not yet captured end-to-end.
2. The legacy live adapter is not hydrated from the Version B store after a
   process restart.
3. Exchange-side confirmation semantics for stop placement need an adapter-level
   fetch/ack contract, not only a non-empty create response.
4. The Paper compatibility exchange still polls public ticker data; deterministic
   5M replay is provided by the B service/Backtest adapter.
5. Legacy RiskManager enforcement and global portfolio risk caps are not fully
   migrated to the canonical model.
6. Reversal/trailing persistence and exchange reconciliation need end-to-end
   adapter tests.
7. No historical, OOS, walk-forward, or Paper performance result exists yet.
8. Data vendor clock skew, outages, and symbol lifecycle changes require live
   operational tests.
9. Telegram authorization and alert delivery are not part of the acceptance
   gate.

See `VERSION_B_LIVE_BLOCKERS.md` for the separate Live decision list.
