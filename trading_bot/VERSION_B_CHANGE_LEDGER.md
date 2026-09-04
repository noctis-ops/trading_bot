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

### VB-EXEC-003 — External execution intent contract

- **Phase:** External Execution + Restart Recovery
- **Category / severity:** Safety / Execution / Critical
- **Component:** `core/external_execution.py`
- **Observable change:** Orders to an external venue now go through a durable,
  write-ahead **order intent** with a deterministic identity
  (`order_intent_id = {trade_id}:{purpose}`,
  `client_order_id = {run_id}-{trade_id}-{purpose}`). A retry reuses that
  identity, so a retry is never a second order. Exchange replies are normalized
  (`NEW / PARTIALLY_FILLED / FILLED / REJECTED / CANCELED / NOT_FOUND /
  UNKNOWN`); `NOT_FOUND` and `UNKNOWN` never resolve to success, a fill is
  recorded only for newly acknowledged quantity, and an unresolved intent is
  reconciled rather than resubmitted.
- **Strategy rules/parameters changed:** No.
- **Evidence:** `test_version_b_external_execution.py`
  (`WriteAheadAndIdentityTests`, `FailureAccountingTests`) — duplicate
  submission, lost response, `UNKNOWN`, partial fill, and rejection cases.
- **Status:** implemented and verified against a deterministic exchange double.
  Real venue deduplication and ack semantics remain `UNKNOWN`.

### VB-EXEC-004 — Restart hydration and reconciliation

- **Phase:** External Execution + Restart Recovery
- **Category / severity:** Persistence / Operational / Critical
- **Component:** `core/external_execution.py`, `core/execution_service.py`,
  `core/trade_lifecycle.py`
- **Observable change:** `ExternalExecutionService.recover()` reconciles every
  non-terminal intent (including resting orders that filled while the process
  was down), derives any lifecycle event a crash dropped from the durable fill,
  recomputes trade quantity/state, and hydrates `VersionBExecutionService`
  positions via `TradeLifecycle.from_records` + `hydrate_position` (margin,
  balance, TP1/BE state, persisted-identity sets, trade counter). Recovery is
  idempotent; anything still unknown is reported instead of assumed successful.
- **Strategy rules/parameters changed:** No.
- **Evidence:** `test_version_b_external_execution.py`
  (`RestartRecoveryTests`) — offline TP1 fill, crash between fill and event,
  unresolvable intent, vanished resting stop, repeated recovery, and hydrated
  accounting equal to the recorded fills.
- **Status:** implemented and verified against a deterministic exchange double.
  A restart drill against a real account is still required.

### VB-EXEC-005 — Protection confirmation fails closed

- **Phase:** External Execution + Restart Recovery
- **Category / severity:** Safety / Execution / Critical
- **Component:** `core/order_manager.py`
- **Observable change:** **Behavior change.** Previously
  `if hasattr(self.exchange, 'record_protection') and not ...` silently skipped
  the protection check for any adapter without that method — which is every
  live adapter today — so a live position could be accepted on a non-empty
  create response alone. `OrderManager._require_confirmed_protection` now fails
  closed: an adapter that cannot confirm protection raises
  `LIVE BLOCKER: adapter cannot confirm protection`, and the filled entry is
  emergency-closed. In `core/external_execution.py`, confirmation additionally
  requires a **fetch/ack**, and a protective order that cannot be seen
  afterwards is downgraded to `UNKNOWN` rather than left `ACCEPTED`.
- **Strategy rules/parameters changed:** No.
- **Evidence:** `test_version_b_order_manager.py::test_adapter_without_confirmation_fails_closed`,
  `test_version_b_external_execution.py::test_confirmation_requires_a_query_not_the_create_response`,
  `::test_unconfirmed_stop_blocks_acceptance_and_flattens_the_fill`.
- **Status:** implemented and verified. This makes the legacy live path fail
  closed until the live adapter implements a real fetch/ack confirmation, which
  is the documented `LB-002` requirement.

### VB-DB-002 — Reconcilable order intent schema

- **Phase:** External Execution + Restart Recovery
- **Category / severity:** Persistence / High
- **Component:** `database/version_b_store.py`, `database/migrations.py`
- **Observable change:** `vb_order_intents` gains `purpose`, `exchange_status`,
  `filled_quantity`, `average_fill_price`, `acknowledged_at`, `attempt_count`,
  and `last_error`; `vb_trade_lifecycles` gains `leverage`. Without them an
  intent cannot be reconciled and margin cannot be rebuilt after a restart.
  Columns are added by an additive migration (`_ensure_additive_columns`) so
  existing audit history is preserved. `SCHEMA_VERSION` is `vb-2`. New additive
  read/idempotent APIs (`get_or_create_order_intent`, `update_order_intent` with
  a transition guard, `reconcilable_order_intents`, `fills_for_intent`,
  `find_event`, `max_event_sequence`, `open_trades`, `get_fill`) were added.
  `record_fill` duplicate protection is unchanged.
- **Strategy rules/parameters changed:** No.
- **Evidence:** `test_version_b_external_execution.py::StoreContractTests`
  (additive upgrade of an existing store, conflicting identity refused) and
  `::test_terminal_intent_state_cannot_be_rewritten`.
- **Status:** implemented and verified

### VB-REM-006 — Acceptance gate computes contract surfaces

- **Phase:** External Execution + Restart Recovery
- **Category / severity:** Measurement / High
- **Component:** `version_b_acceptance.py`, `VERSION_B_ACCEPTANCE.md`,
  `VERSION_B_TEST_CLASSIFICATION.md`
- **Observable change:** The gate runs 68 deterministic assertions and now
  reports `external_execution_contract` and `restart_recovery_contract` as
  **computed** per-group results from the junit report instead of literals.
  `live_exchange_acknowledgement` and `restart_exchange_reconciliation` stay
  `UNKNOWN` by construction and publish the evidence each one requires.
- **Strategy rules/parameters changed:** No.
- **Evidence:** Gate run: 68 passed, 0 failed, 0 errors, 0 skipped;
  `baseline_collected=false`. Negative control: forcing `NOT_FOUND` to resolve
  as accepted turns both new surfaces to `FAIL` with 3 named tests.
- **Status:** implemented and verified

### VB-MEAS-001 — Binding baseline artifact contract

- **Phase:** Baseline Readiness
- **Category / severity:** Measurement / High
- **Component:** `core/baseline_artifact.py`, `VERSION_B_BASELINE_DEFINITION.md`
- **Observable change:** A Historical Baseline artifact now has an enforced
  schema (`vb-baseline-1`): mandatory `report_type=STRATEGY_MODEL_BASELINE`,
  `measurement_scope=TYPE_1_STRATEGY_MODEL`, `not_an_operational_result`,
  complete lineage, pinned metric keys, a self-labelling filename, and
  `profitability_verdict: null`. Operational-verdict keys (`final_verdict`,
  bare `win_rate`, `max_drawdown_pct`, `criteria`, ...) are rejected in the
  artifact, in `metrics`, **and** in `counts`. `universe` must equal
  `[symbol]`, so several symbols cannot be silently aggregated into one
  per-symbol result.
- **Strategy rules/parameters changed:** No.
- **Evidence:** `test_version_b_baseline_readiness.py::ArtifactContractTests`.
  Negative control: removing the `counts` guard fails the contract surface.
- **Status:** implemented and verified

### VB-MEAS-002 — Reproducible measurement lineage

- **Phase:** Baseline Readiness
- **Category / severity:** Measurement / High
- **Component:** `core/measurement_lineage.py`, `core/version_b_replay.py`
- **Observable change:** **Behavior change.** The run header previously recorded
  `code_version="working-tree"` and `data_hash=None`, which made a run
  unreproducible while looking recorded. It now records the real commit plus a
  dirty flag, a sha256 identity of the exact frames consumed, the config
  snapshot hash, and the execution-model version. `UNRESOLVED` replaces the
  placeholder, and a dirty tree is rejected by default.
- **Strategy rules/parameters changed:** No.
- **Evidence:** `LineageTests`, `ReplayLineageTests`, `ReproducibilityTests`.
- **Status:** implemented and verified

### VB-MEAS-003 — One pinned max-drawdown definition

- **Phase:** Baseline Readiness
- **Category / severity:** Measurement / High
- **Component:** `core/measurement_metrics.py`, `performance_report.py`
- **Observable change:** Two incompatible definitions existed:
  `performance_report.compute_max_drawdown` cumulated closed-trade PnL on an
  arbitrary `+100` base, while the replay engine emitted an `equity_curve` and
  no drawdown at all. One definition is now pinned
  (`compute_equity_curve_max_drawdown`, on the marked curve against the actual
  `initial_balance`) and is declared a **lower bound**, because the curve is
  sampled per 15m decision bar while exits resolve on 5m bars. The legacy
  function is marked as such and points at the canonical one.
- **Strategy rules/parameters changed:** No.
- **Evidence:** `MetricDefinitionTests` (hand-computed 25% case, monotonic
  curve, empty-curve and non-positive-balance refusal).
- **Status:** implemented and verified

### VB-MEAS-004 — Legacy report cannot be confused with Version B

- **Phase:** Baseline Readiness
- **Category / severity:** Measurement / High
- **Component:** `performance_report.py`
- **Observable change:** **Behavior change.** The legacy report emitted
  `final_verdict: 'PASS'` from the legacy `Trade` table with no model or data
  lineage, indistinguishable from a Version B result. It now emits
  `report_type=LEGACY_OPERATIONAL_SUMMARY`,
  `measurement_scope=TYPE_2_OPERATIONAL_LEGACY`,
  `not_a_version_b_measurement=true`, a printed banner, and
  `legacy_operational_verdict` with `LEGACY_OPERATIONAL_*` values.
  `assert_not_baseline_artifact` refuses to score a baseline artifact.
- **Strategy rules/parameters changed:** No.
- **Evidence:** `test_the_legacy_report_is_typed_and_carries_no_bare_verdict`,
  `test_a_baseline_artifact_cannot_be_consumed_as_operational`.
- **Status:** implemented and verified

### VB-MEAS-005 — Costs are no longer silently zero

- **Phase:** Baseline Readiness
- **Category / severity:** Measurement / Medium
- **Component:** `core/version_b_replay.py`
- **Observable change:** `_trade_rows()` had no `fees`/`slippage` keys, so
  `model_cost_total` would have reported `0.0` while net PnL already had fees
  deducted — an internally inconsistent artifact. Both keys are now emitted and
  asserted.
- **Strategy rules/parameters changed:** No.
- **Evidence:** `test_trade_rows_expose_fees_so_costs_are_not_silently_zero`.
- **Status:** implemented and verified

### VB-OPS-001 — Test runs no longer dirty tracked files

- **Phase:** Baseline Readiness
- **Category / severity:** Operational / Measurement / Medium
- **Component:** repository tracking (`.gitignore` already declared the policy)
- **Observable change:** `trading_bot/logs/bot.log` was tracked even though
  `.gitignore` lists `logs/` and `*.log`, so every test or gate run modified a
  tracked file and left the tree permanently dirty. That made the
  `require_clean_tree` lineage condition impossible to satisfy after any test
  run. The 10 files already excluded by the repo's own `.gitignore`
  (1 log, 9 `.pyc`) are now untracked; the files remain on disk.
- **Strategy rules/parameters changed:** No.
- **Evidence:** Full suite plus gate run produces zero tracked-file changes.
- **Status:** implemented and verified

### VB-REM-007 — Acceptance gate repairs Version A availability

- **Phase:** Baseline Readiness
- **Category / severity:** Measurement / High
- **Component:** `version_b_acceptance.py`
- **Observable change:** Three mandatory checks (`version_a_commit_exists`,
  `version_a_is_ancestor`, `version_a_config_hash`) failed on a shallow
  single-branch clone because commit `89bec19` was not in the object store —
  an environmental failure that made the gate unusable. The gate now fetches
  the object and removes shallow boundaries, and reports the attempt as a
  separate `version_a_object_available` check. No check is skipped or weakened,
  Version A is not rewritten, and HEAD, branch pointers, and the working tree
  are untouched.
- **Strategy rules/parameters changed:** No.
- **Evidence:** Gate `PASS` with all 7 checks true; verified from a cold
  `--depth 1` clone (`restored: true`).
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
- The live/exchange path now has a durable contract
  (`core/external_execution.py`) but `OrderManager`/`BinanceExchange` are not
  yet wired to it. Until they are, the live adapter fails closed on protection
  confirmation (`VB-EXEC-005`) and cannot open positions.

No Historical Baseline or Paper performance baseline was started by these
changes.
