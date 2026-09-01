# Version B Change Ledger

This ledger records every observable Version B behavior change. It does not
authorize strategy tuning. Version A remains immutable at commit
`89bec19164417ca27bb40ea32cc071c8c0300d8a`.

## Entry format

Each entry records category, severity, component, observable impact, whether a
strategy rule/parameter changed, evidence, and status.

## Existing entries

The initial implementation entries are restated here so the ledger remains
self-contained; the authoritative expanded behavior table is also in
`VERSION_B_SCOPE.md`.

### VB-DATA-001 — Aligned data contract

- **Category / severity:** Measurement / High
- **Component:** `data/time_alignment.py`
- **Observable change:** UTC-aware candle closure, one decision timestamp,
  freshness/gap/warm-up status, and explicit 15M/5M roles replace independent
  timestamp assumptions in the B adapter.
- **Strategy rules/parameters changed:** No. This changes measurement safety,
  not gate formulas.
- **Status:** implemented and verified

### VB-IND-001 — Canonical indicator provider

- **Category / severity:** Measurement / High
- **Component:** `indicators/provider.py`
- **Observable change:** B uses one provider matching audited MarketData
  formulas rather than allowing indicator drift between adapters.
- **Strategy rules/parameters changed:** No.
- **Status:** implemented and verified

### VB-STRAT-001 — Frozen StrategyCore adapter

- **Category / severity:** Measurement / High
- **Component:** `core/strategy_core.py`
- **Observable change:** B decisions carry aligned context and delegate Long,
  Short, gates, score, MTF, and effective-score behavior through one core.
- **Strategy rules/parameters changed:** No.
- **Status:** implemented and verified

### VB-RISK-001 — Canonical risk/accounting definitions

- **Category / severity:** Safety / Measurement / Critical
- **Component:** `core/risk_model.py`, `core/risk_manager.py`
- **Observable change:** Equity, planned risk, lifecycle net PnL, daily loss,
  and portfolio risk-at-stop use explicit definitions; compatibility wrappers
  delegate to the pure model.
- **Strategy rules/parameters changed:** No.
- **Status:** implemented and verified

### VB-RISK-002 — Executed-fill cost accounting

- **Category / severity:** Measurement / Execution / High
- **Component:** `core/risk_model.py`, `core/execution_service.py`
- **Observable change:** Actual fill prices contain slippage, fees are deducted
  once, and slippage is retained for attribution rather than double-counted.
- **Strategy rules/parameters changed:** No.
- **Status:** implemented and verified

### VB-EXEC-001 — Frozen 5M stop-first model

- **Category / severity:** Execution / Measurement / Critical
- **Component:** `core/execution_model.py`
- **Observable change:** 5M-only exit replay uses stop-first, TP1-before-TP2,
  same-bar deferral, gap fill, and adverse slippage under
  `vb-1.0-5m-stop-first`.
- **Strategy rules/parameters changed:** No.
- **Status:** implemented and verified

### VB-EXEC-002 — Confirmed protection safety gate

- **Category / severity:** Safety / Execution / Critical
- **Component:** `core/order_manager.py`
- **Observable change:** Stop/target placement failures are critical and a
  position is not accepted without protection confirmation where the adapter
  supports it.
- **Strategy rules/parameters changed:** No.
- **Status:** implemented and verified in deterministic component tests;
  exchange acknowledgement remains UNKNOWN.

### VB-LIFE-001 — One lifecycle per trade

- **Category / severity:** Execution / Persistence / High
- **Component:** `core/trade_lifecycle.py`
- **Observable change:** TP1, BE, TP2, SL, reversal, and emergency actions are
  ordered events/fills under one `trade_id`; partial TP1 cannot retrigger.
- **Strategy rules/parameters changed:** No.
- **Status:** implemented and verified

### VB-DB-001 — Durable B journal

- **Category / severity:** Persistence / Operational / Critical
- **Component:** `database/version_b_store.py`
- **Observable change:** Runs, decision snapshots, lifecycle state, intents,
  ordered events, fills, costs, idempotent identities, reconstruction, and
  backup are persisted.
- **Strategy rules/parameters changed:** No.
- **Status:** implemented and verified; live exchange hydration remains
  UNKNOWN.

### VB-PAPER-001 — Paper lifecycle compatibility

- **Category / severity:** Execution / Measurement / High
- **Component:** `core/paper_trading.py`
- **Observable change:** Compatibility Paper has one lifecycle, configurable
  costs/leverage, TP1 idempotency, and lifecycle-level statistics.
- **Strategy rules/parameters changed:** No.
- **Status:** implemented and verified

### VB-BT-001 — Explicit B Backtest adapter

- **Category / severity:** Measurement / Execution / High
- **Component:** `backtesting/version_b_backtest.py`
- **Observable change:** The explicit adapter uses a 15M decision clock, next
  15M open entry, 5M exits, shared lifecycle/accounting, and model metadata.
- **Strategy rules/parameters changed:** No.
- **Status:** implemented and verified

### VB-CONFIG-003 — Explicit execution configuration

- **Category / severity:** Configuration / Measurement / High
- **Component:** `config.yaml`, `core/runtime_config.py`
- **Observable change:** Cost/model/clock assumptions are declared and
  snapshot-able while dormant volume metadata remains dormant.
- **Strategy rules/parameters changed:** No.
- **Status:** implemented and verified

The entries below document the integration acceptance remediation performed
after the initial component implementation.

### VB-REM-001 — Runtime execution model source of truth

- **Phase:** Integration acceptance remediation
- **Category / severity:** Measurement / High
- **Component:** `core/runtime_config.py`, `test_version_b_config_contracts.py`
- **Observable change:** Effective config and run snapshot now retain the
  declared `execution.model_version`; the previous silent legacy overwrite is
  removed. A public `execution_model_version` property exposes the same value.
- **Strategy rules/parameters changed:** No.
- **Evidence:** Runtime config contract and acceptance gate pass.
- **Status:** implemented and verified

### VB-REM-002 — Shared Backtest/Paper replay path

- **Phase:** Integration acceptance remediation
- **Category / severity:** Execution / Measurement / High
- **Component:** `core/version_b_replay.py`,
  `backtesting/version_b_backtest.py`, `core/version_b_paper.py`
- **Observable change:** Explicit Version B Backtest and deterministic Paper
  now share StrategyCore, canonical RiskEngine planning, VersionBExecutionService,
  TradeLifecycle, and optional VersionBStore persistence. 5M rows are consumed
  once for exits; decisions remain on the 15M clock.
- **Strategy rules/parameters changed:** No. Frozen gate/score/SL/TP behavior is
  delegated to the existing Strategy and config.
- **Evidence:** Replay parity, Backtest lifecycle, and DB reconstruction tests
  pass.
- **Status:** implemented and verified

### VB-REM-003 — Canonical risk facade and stop plan

- **Phase:** Integration acceptance remediation
- **Category / severity:** Safety / Measurement / High
- **Component:** `core/risk_engine.py`, `core/strategy_core.py`,
  `test_version_b_execution_service.py`, `test_version_b_strategy_freeze.py`
- **Observable change:** StrategyCore produces the canonical stop and sizing
  plan; planned risk, realized net PnL, daily loss, and portfolio risk-at-stop
  remain delegated to `core.risk_model` through one B facade.
- **Strategy rules/parameters changed:** No. Existing score factors and ATR
  multipliers are preserved and explicitly tested for Long/Short and multiple
  ATR values.
- **Evidence:** Canonical execution-service and strategy stop-plan tests pass.
- **Status:** implemented and verified

### VB-REM-004 — Deterministic TradingBot B entry point

- **Phase:** Integration acceptance remediation
- **Category / severity:** Execution / Operational / High
- **Component:** `core/bot.py`, `test_version_b_replay_parity.py`
- **Observable change:** `TradingBot(version_b=True)` accepts injected finite
  MarketData, constructs StrategyCore and VersionBPaperEngine, and makes no
  exchange/Binance call. `run_version_b_once()` exercises the full deterministic
  coordinator path.
- **Strategy rules/parameters changed:** No.
- **Evidence:** Network-free TradingBot fixture verifies Market Data → TradingBot
  → StrategyCore → Risk → Execution → Lifecycle → report.
- **Status:** implemented and verified

### VB-REM-005 — Acceptance reporting and test classification

- **Phase:** Integration acceptance remediation
- **Category / severity:** Measurement / High
- **Component:** `version_b_acceptance.py`, `VERSION_B_ACCEPTANCE.md`,
  `VERSION_B_TEST_CLASSIFICATION.md`
- **Observable change:** The gate selects an installed pytest runner, reports
  full-path and Backtest↔Paper parity statuses separately from operational
  `UNKNOWN` surfaces, and runs 50 deterministic assertions without baseline
  collection.
- **Strategy rules/parameters changed:** No.
- **Evidence:** Gate result `PASS`; 50 passed, 0 failed, 0 errors, 0 skipped,
  one non-blocking PyArrow warning; `baseline_collected=false`.
- **Status:** implemented and verified

## Retained intentional paths

- Legacy `TradingBot()` and `TradingStrategy`/`RiskManager` production paths
  remain available for Version A reproduction and compatibility. They are not
  silently counted as Version B evidence.
- Legacy `PaperTradingExchange` remains available for compatibility tests;
  deterministic Version B Paper uses `VersionBPaperEngine` and the shared
  replay service.
- Legacy `BacktestEngine.backtest()` remains available. Only
  `backtest_version_b()` is the explicit B path.

## Accidental active legacy paths to remove before Live

- The default `TradingBot()` constructor still builds the legacy coordinator;
  callers must opt into `version_b=True` until an operational migration is
  separately approved.
- The live/exchange reconciliation and protection acknowledgement path is not
  yet connected to VersionBStore.

No Historical Baseline or Paper performance baseline was started by these
changes.
