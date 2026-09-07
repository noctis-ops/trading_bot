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

### VB-INT-001 — Durable leverage on the trade row

- **Phase:** Integration Acceptance
- **Category / severity:** Correctness / High
- **Component:** `core/execution_service.py`, `database/version_b_store.py`
- **Observable change:** **Behavior change; fixes a defect.** The unified path
  called `create_trade(...)` without `leverage`, leaving the column NULL even
  though it exists and is documented as required to rebuild margin. Restart
  hydration therefore reported `missing durable leverage` for every position
  opened through Backtest/Paper, and `reconstruct_trade` could not return it.
  `create_trade` now accepts and stores leverage, `open_position` supplies it,
  and `reconstruct_trade` returns it.
- **Strategy rules/parameters changed:** No — leverage is execution state
  already computed by the canonical RiskEngine.
- **Evidence:** `StrategyRiskExecutionEndToEndTests`,
  `RestartDuringOpenLifecycleTests`. Negative control: removing the persisted
  leverage fails 5 tests across both classes.
- **Status:** implemented and verified

### VB-INT-002 — Order intents on the unified path

- **Phase:** Integration Acceptance
- **Category / severity:** Measurement / High
- **Component:** `core/execution_service.py`
- **Observable change:** **Behavior change.** Protection orders existed only in
  memory: `_persist_lifecycle` wrote every fill with `order_intent=None` and no
  intent row was ever created, so a restart could not tell which protection was
  resting. The unified path now records `ENTRY` (FILLED) and `STOP_LOSS` /
  `TAKE_PROFIT_1` / `TAKE_PROFIT_2` (SUBMITTED) using the same identity scheme
  as the external execution contract, links each fill to the intent that
  produced it, and transitions the consumed intent to FILLED.
- **Strategy rules/parameters changed:** No.
- **Evidence:** `test_fills_are_linked_to_the_intent_that_produced_them`,
  `test_pending_protection_is_still_pending_after_the_restart`.
- **Status:** implemented and verified

### VB-INT-003 — Restart recovery for the unified path

- **Phase:** Integration Acceptance
- **Category / severity:** Correctness / High
- **Component:** `core/version_b_recovery.py` (new)
- **Observable change:** **New capability.** `recover_unified_state` rebuilds
  every open position of a run from durable rows alone — identity, fills,
  TP1/breakeven state, resting intents, and balance/margin — and reports what it
  could not rebuild rather than guessing. Previously `hydrate_position` was
  reachable only from `ExternalExecutionService.recover`, so the deterministic
  path had no restart story at all.
- **Strategy rules/parameters changed:** No.
- **Evidence:** `RestartDuringOpenLifecycleTests` (6 tests), including a
  fail-closed case for a run with no durable leverage and an idempotency case
  proving a second recovery writes no rows.
- **Status:** implemented and verified

### VB-INT-004 — Paper is durable, not memory-only

- **Phase:** Integration Acceptance
- **Category / severity:** Measurement / High
- **Component:** `core/version_b_paper.py`, `database/version_b_store.py`,
  `database/migrations.py` (schema `vb-3`)
- **Observable change:** **New capability.** `VersionBPaperPipeline` owns a
  `VersionBStore` and a `run_id`, so Paper writes trades, events, fills,
  intents, decisions, and an operational-state snapshot, and can resume with
  `resume=True`. Two run-level fields became durable because a restart cannot
  rebuild balance without them: `initial_balance` and `operational_state_json`
  (additive columns; no audit history dropped).
- **Strategy rules/parameters changed:** No.
- **Evidence:** `test_paper_is_durable_and_reconstructable`,
  `test_paper_state_survives_a_fresh_handle_on_the_same_database`,
  `test_the_resting_stop_is_durable_paper_protection`.
- **Status:** implemented and verified

### VB-INT-005 — TradingBot runs the pipeline it holds

- **Phase:** Integration Acceptance
- **Category / severity:** Measurement / High
- **Component:** `core/bot.py`
- **Observable change:** **Behavior change.** `TradingBot(version_b=True)`
  previously constructed a Paper engine with no store, so the bot's Version B
  path was memory-only and the components merely sat next to each other. With
  `version_b_db_path=...` the bot now owns a durable `VersionBPaperPipeline`,
  runs its engine, and persists operational state per run. Without a database
  path it warns explicitly that state is memory-only.
- **Strategy rules/parameters changed:** No.
- **Evidence:** `test_trading_bot_version_b_owns_a_durable_pipeline`,
  `test_memory_only_paper_is_reported_as_such`.
- **Status:** implemented and verified

### VB-INT-006 — Legacy paper path refused by default

- **Phase:** Integration Acceptance
- **Category / severity:** Measurement / High
- **Component:** `core/bot.py`, `main.py`
- **Observable change:** **Behavior change.** `main.py` constructs
  `TradingBot()`, which resolved to the pre-Version-B chain
  (`TradingStrategy → RiskManager → OrderManager → PaperTradingExchange`) with
  all position state in memory and no `VersionBStore` writes — while
  `TRADING_MODE=paper` is the documented default. That is now refused with
  `LegacyPaperPathDisabled`; the legacy chain requires the named opt-in
  `allow_legacy_paper=True`, which logs a critical warning. `main.py` reports
  the refusal instead of starting the loop. The Live path is unchanged and
  remains unproven.
- **Strategy rules/parameters changed:** No.
- **Evidence:** `LegacyFallbackClosureTests`. Negative control: disabling the
  guard fails both closure tests.
- **Status:** implemented and verified

### VB-INT-007 — A restart cannot silently change the cost model

- **Phase:** Integration Acceptance
- **Category / severity:** Correctness / Medium
- **Component:** `core/version_b_paper.py`
- **Observable change:** **Behavior change; fixes a defect found during
  testing.** On resume, `fee_rate`/`slippage_rate` defaulted to `config.yaml`
  rather than the values the run executed with, so a restarted process priced an
  already-open trade under a different cost model and recorded nothing about the
  change (observed: net PnL 32.19 instead of 32.50 on the deterministic
  fixture). Both rates are now part of the durable operational state; resume
  restores them and refuses a conflicting override, and refuses outright when
  they are absent.
- **Strategy rules/parameters changed:** No.
- **Evidence:** `test_a_restart_cannot_silently_change_the_cost_model`,
  `test_the_restarted_position_still_executes_correctly`.
- **Status:** implemented and verified

### VB-INT-008 — The gate's Paper module tested the legacy exchange

- **Phase:** Integration Acceptance
- **Category / severity:** Measurement / High
- **Component:** `test_version_b_paper.py`, `test_legacy_paper_exchange.py` (new)
- **Observable change:** **Test-surface change.** `test_version_b_paper.py` was
  a mandatory gate module that exercised `PaperTradingExchange` — the legacy
  in-memory exchange — and never touched StrategyCore, the canonical RiskEngine,
  or VersionBStore. The gate therefore reported a passing Paper surface that
  proved nothing about Version B. It now tests `VersionBPaperEngine` and
  `VersionBPaperPipeline`; the legacy exchange checks moved to
  `test_legacy_paper_exchange.py`, labelled as non-Version-B coverage, with an
  added test documenting that its state does not survive a restart.
- **Strategy rules/parameters changed:** No.
- **Evidence:** `test_the_gate_paper_module_tests_the_version_b_paper_engine`.
- **Status:** implemented and verified

### VB-INT-009 — Trailing stop is not part of the canonical path

- **Phase:** Integration Acceptance
- **Category / severity:** Measurement / Medium (documentation)
- **Component:** `core/version_b_recovery.py`, `VERSION_B_TEST_CLASSIFICATION.md`
- **Observable change:** No code behavior change; recorded divergence. Trailing
  exists only in the legacy `OrderManager._apply_trailing_stop` /
  `RiskManager.should_update_trailing_stop`. The canonical path implements
  breakeven-after-TP1 only, and `config.yaml` exposes no trailing parameter.
  `TradeLifecycle` accepts a `TRAILING_UPDATED` event but nothing in the unified
  path emits one, so recovery reports trailing updates if they ever appear
  rather than assuming continuity. Adding trailing would be a strategy change
  and is out of scope.
- **Strategy rules/parameters changed:** No.
- **Evidence:** `test_restart_rebuilds_the_open_position_from_rows_alone`
  asserts `breakeven_armed_before_restart`; `trailing_updates_before_restart`
  is reported and empty.
- **Status:** recorded

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
