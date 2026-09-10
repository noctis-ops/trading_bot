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

### VB-OPS-002 — Event-driven Paper runtime

- **Phase:** Operational Acceptance
- **Category / severity:** Measurement / High
- **Component:** `core/version_b_runtime.py` (new), `core/version_b_replay.py`
- **Observable change:** **New capability.** `VersionBPaperRuntime` consumes one
  market event at a time through the same StrategyCore → RiskEngine →
  ExecutionService → TradeLifecycle → VersionBStore path. The engine's decision
  and execution steps are exposed publicly (`persist_decision`,
  `open_from_decision`) so event-driven Paper and batch replay share one
  implementation instead of two kept identical by hand. `DeterministicMarketFeed`
  emits closed bars in time order plus the open of the bar starting now; the
  engine is never handed the frame.
- **Strategy rules/parameters changed:** No.
- **Evidence:** `NormalBarByBarTests`, `EventDrivenReplayParityTests`. Negative
  control: reordering the feed so the decision precedes the 5m bar closing at
  the same instant fails the parity test.
- **Status:** implemented and verified

### VB-OPS-003 — Durable single-instance guard

- **Phase:** Operational Acceptance
- **Category / severity:** Operational / High
- **Component:** `database/version_b_store.py` (`vb_runtime_locks`),
  `core/version_b_runtime.py`
- **Observable change:** **New capability.** Ownership of a run is a row. A
  second live instance is refused with `NotPrimaryInstance`; releasing another
  holder's lock is refused. A crashed process never released its lock, so a
  supervisor may take over with an explicit `start(takeover=True)`, which
  records `LOCK_TAKEN_OVER` at CRITICAL. Takeover is never silent, because two
  processes trading one account is exactly what the lock prevents.
- **Strategy rules/parameters changed:** No.
- **Evidence:** `SingleInstanceAndLegacyTests`,
  `test_crash_without_shutdown_still_recovers_and_the_lock_is_reclaimable`.
- **Status:** implemented and verified

### VB-OPS-004 — Durable health state and audit trail

- **Phase:** Operational Acceptance
- **Category / severity:** Operational / Medium
- **Component:** `database/version_b_store.py` (`vb_runtime_audit`),
  `core/version_b_runtime.py`
- **Observable change:** **New capability.** Data, execution, and database
  component health plus the circuit-breaker state are written to the run header
  and reloaded on resume. Component failures append audit rows
  (`STALE_DATA`, `OUT_OF_ORDER_EVENT`, `ENTRY_SUBMIT_UNKNOWN`,
  `PROTECTION_NOT_CONFIRMED`, `EMERGENCY_FLATTEN`, `CIRCUIT_BREAKER_OPEN`,
  `CIRCUIT_BREAKER_CLOSED`, `LOCK_TAKEN_OVER`, `*_PERSIST_FAILED`) with
  severity, so a degraded run explains itself after the fact.
- **Strategy rules/parameters changed:** No.
- **Evidence:** `OperationalControlsTests`,
  `test_health_state_is_stored_and_survives_a_restart`.
- **Status:** implemented and verified

### VB-OPS-005 — Consumed-event identity makes the stream resumable

- **Phase:** Operational Acceptance
- **Category / severity:** Correctness / High
- **Component:** `database/version_b_store.py` (`vb_runtime_events`),
  `core/version_b_runtime.py`, `database/migrations.py` (schema `vb-4`)
- **Observable change:** **New capability.** Every consumed market event is
  recorded under its own identity. A redelivery returns `DUPLICATE_IGNORED` and
  executes nothing; a restart continues after the last persisted event instead
  of replaying the window, and redelivering the consumed prefix is a no-op.
- **Strategy rules/parameters changed:** No.
- **Evidence:** `DuplicateHandlingTests`,
  `test_a_restart_continues_after_the_last_persisted_event`. Negative control:
  removing the duplicate guard fails exactly the two idempotency tests.
- **Status:** implemented and verified

### VB-OPS-006 — Stale-data circuit breaker

- **Phase:** Operational Acceptance
- **Category / severity:** Measurement / High
- **Component:** `core/version_b_runtime.py`
- **Observable change:** **New capability.** Staleness, gaps, schema failures,
  and out-of-order delivery are counted as data faults; at the threshold the
  breaker opens and new entries are refused with `CIRCUIT_OPEN_REJECTED`.
  **Exits keep processing while the breaker is open** — being able to get out is
  never the thing to disable. Warm-up and empty history at start-up are
  deliberately *not* faults, or every run would trip the breaker before its
  first decision. A breaker opened by a data fault closes on the next healthy
  decision and records `CIRCUIT_BREAKER_CLOSED`.
- **Strategy rules/parameters changed:** No.
- **Evidence:** `BadDataTests` (4 tests).
- **Status:** implemented and verified

### VB-OPS-007 — Deterministic venue double and fail-closed protection

- **Phase:** Operational Acceptance
- **Category / severity:** Measurement / High
- **Component:** `core/version_b_runtime.py`
- **Observable change:** **New capability.** `DeterministicExchangeDouble`
  implements the same `ExternalOrderAdapter` contract as the real path, so
  timeout/UNKNOWN, rejection, partial fill, and vanished orders exercise the
  real intent semantics. A lost response never becomes a fill: the intent stays
  pending, health goes UNKNOWN, and `reconcile_pending` resolves only on
  concrete evidence — reporting `STILL_UNKNOWN` rather than dropping it. A
  partial fill is reported and alerted, and canonical risk sizing is **not**
  silently overridden. Unconfirmed protection flattens the position with
  `EMERGENCY_EXIT` rather than carrying an unprotected trade.
- **Strategy rules/parameters changed:** No.
- **Evidence:** `VenueDegradationTests` (3 tests).
- **Status:** implemented and verified; the venue remains a double

### VB-INT-010 — Resting-stop intent follows the breakeven amendment

- **Phase:** Operational Acceptance
- **Category / severity:** Correctness / Medium
- **Component:** `core/execution_service.py`
- **Observable change:** **Behavior change; fixes a defect found during
  testing.** When TP1 moved the stop to breakeven, the `BE_UPDATED` event
  recorded it but the durable `STOP_LOSS` intent kept advertising the
  pre-breakeven price and the original quantity. Recovery read the stop from the
  event and was correct, so nothing caught it — but the intent row is what an
  operator or a reconciler reads, and it disagreed with the lifecycle. The
  intent is now amended to the level actually resting.
- **Strategy rules/parameters changed:** No.
- **Evidence:** `test_the_resting_stop_is_durable_paper_protection`,
  `test_one_decision_travels_the_whole_chain` (both previously asserted the
  stale 98.5 and were asserting the defect),
  `test_restart_after_tp1_and_breakeven_keeps_tp2_protection_alive`.
- **Status:** implemented and verified

### VB-DRV-001 — Market data boundary extracted from the runtime
- **Phase:** Paper Driver Layer
- **Category / severity:** Architecture / Informational
- **Component:** `core/version_b_paper_driver.py`, `core/version_b_runtime.py`
- **Observable change:** Market data now reaches the runtime through
  `MarketDataAdapter` (`open` / `next_event` / `close`), an interface whose
  contract is *one `MarketEvent` at a time*. `DeterministicMarketFeed` is
  demoted to an implementation detail of `DeterministicMarketAdapter`; it is no
  longer the runtime's only source. No method on the interface can return a
  frame, and a new adapter is a constructor argument — not a runtime change.
  `LiveMarketAdapter` exists as the documented boundary and raises
  `NotImplementedError` on construction: no network market-data source is
  authorised in this phase, so Binance is deliberately **not** wired.
- **Strategy rules/parameters changed:** No.
- **Evidence:** `test_the_deterministic_feed_is_only_one_implementation`,
  `test_a_custom_adapter_drives_the_same_runtime_unchanged`,
  `test_the_live_adapter_boundary_refuses_to_be_constructed`.
- **Status:** implemented and verified

### VB-DRV-002 — Paper driver/scheduler; the runtime is no longer test-driven
- **Phase:** Paper Driver Layer
- **Category / severity:** Architecture / Informational
- **Component:** `core/version_b_paper_driver.py`
- **Observable change:** `PaperDriver` owns the operating loop: it opens the
  source, seeds the clock, takes the single-instance lock, and delivers events
  **event-by-event in time order** (`step()` / `run(max_events, until)`),
  returning a `DriverReport` derived from the runtime rather than from memory.
  Event position is durable in `VersionBStore`, so a restart resumes *after*
  the last persisted event instead of replaying the window. Before this the
  loop existed only inside the test harness.
- **Strategy rules/parameters changed:** No.
- **Evidence:** `test_start_clock_event_decision_risk_intent_fill_lifecycle_db_restart_resume`,
  `test_shutdown_does_not_lose_the_event_position`,
  `test_the_driver_never_holds_a_window`.
- **Status:** implemented and verified

### VB-DRV-003 — Clock separated from the market feed
- **Phase:** Paper Driver Layer
- **Category / severity:** Architecture / Informational
- **Component:** `core/version_b_clock.py` (new), `core/version_b_runtime.py`
- **Observable change:** `Clock` (`now` / `advance_to`) is its own module so the
  driver and the runtime can both depend on it without a cycle. The runtime
  takes `clock=` and stamps **every** durable timestamp (lock, heartbeat,
  health, operational state, audit) from it. Tests run on
  `DeterministicClock`, which refuses to move backwards
  (`ClockCannotRewind`); production can take `WallClock`. There is no
  wall-clock dependence anywhere in the deterministic path.
- **Strategy rules/parameters changed:** No.
- **Evidence:** `test_the_deterministic_clock_never_reads_the_wall_clock`,
  `test_the_clock_refuses_to_move_backwards`,
  `test_durable_timestamps_come_from_the_injected_clock`,
  `test_a_wall_clock_can_be_substituted_without_touching_the_runtime`.
- **Status:** implemented and verified

### VB-DRV-004 — Whole-window ingestion into the runtime is now refused
- **Phase:** Paper Driver Layer
- **Category / severity:** Correctness / High
- **Component:** `core/version_b_runtime.py`
- **Observable change:** **Behavior change.** `VersionBPaperRuntime.run(...)`
  raises `WindowRejected` instead of being an available batch entry point, and
  its signature no longer names a `frames` parameter, so there is no public
  method on the runtime that accepts a frame or a set of frames. Batch replay
  remains available *only* through the separate `VersionBPaperPipeline` oracle,
  which is the parity reference and never the operating path.
- **Strategy rules/parameters changed:** No.
- **Evidence:** `test_handing_the_runtime_a_window_fails`,
  `test_the_runtime_exposes_no_batch_ingestion_path`,
  `test_the_driver_has_no_window_entry_point_either`,
  `test_an_adapter_that_returns_a_window_is_not_a_valid_source`. Negative
  control: restoring a silent `run()` fails
  `test_handing_the_runtime_a_window_fails`; disabling the consumed-event check
  so a restart replays the window fails both end-to-end tests with
  `ClockCannotRewind`.
- **Status:** implemented and verified

### VB-DRV-005 — `main.py` runs Version B Paper only through the driver
- **Phase:** Paper Driver Layer
- **Category / severity:** Architecture / Medium
- **Component:** `main.py`
- **Observable change:** **Behavior change.** `main.py --version-b-paper
  --frames-dir DIR` starts a real Paper run through
  `MarketDataAdapter → Clock → PaperDriver → VersionBPaperRuntime`, with
  `--db`, `--run-id`, `--resume`, `--symbol`, `--takeover` and `--iterations`.
  There is **no fallback to Legacy Paper**: a missing or invalid `--frames-dir`
  exits `2` with a refusal, and `TRADING_MODE=paper` without
  `allow_legacy_paper` still exits `2`. The source is local CSV because no
  network feed is authorised; the boundary is the adapter, so replacing it does
  not touch the runtime.
- **Strategy rules/parameters changed:** No.
- **Evidence:** Manual run — `main.py --version-b-paper --frames-dir /tmp/frames
  --db /tmp/main_paper.sqlite --run-id MAIN-1` delivered 510 events, clocked
  `2025-12-30T18:10Z → 2026-01-01T01:00Z`, emitted 20 heartbeats and exited 0.
  The real `TradingStrategy` correctly rejected all 119 decisions as
  `INSUFFICIENT_WARMUP` (it requires 200 1h/15m bars; the fixture has 30/120),
  which is the data-quality gate working, not a driver fault. Refusal paths
  verified at exit code 2.
- **Status:** implemented and verified

### VB-DRV-006 — Heartbeat/lease activated, not dead code
- **Phase:** Paper Driver Layer
- **Category / severity:** Correctness / Medium
- **Component:** `core/version_b_paper_driver.py`, `core/version_b_runtime.py`,
  `database/version_b_store.py`
- **Observable change:** **Behavior change.** `store.heartbeat()` was written
  and never called. The driver now heartbeats every `heartbeat_every` events
  (default 25) through the injected clock, and the runtime passes
  `lease_seconds` (default 600) when taking the lock. A holder **inside** its
  lease is never displaced; a holder whose heartbeat is older than the lease is
  treated as dead and may be replaced, with the takeover recorded as a CRITICAL
  `LOCK_TAKEN_OVER` audit row. A crashed process no longer deadlocks the
  account permanently, and a live second instance still cannot steal it.
- **Strategy rules/parameters changed:** No.
- **Evidence:** `test_the_driver_heartbeats_and_the_lease_advances`,
  `test_a_holder_inside_its_lease_is_never_displaced`,
  `test_a_holder_past_its_lease_can_be_replaced`. Negative control: removing
  the driver's heartbeat call fails
  `test_the_driver_heartbeats_and_the_lease_advances`.
- **Status:** implemented and verified

### VB-DRV-007 — Defect: lease comparison mixed naive and aware datetimes
- **Phase:** Paper Driver Layer
- **Category / severity:** Correctness / High (defect found while testing)
- **Component:** `database/version_b_store.py`
- **Observable change:** **Behavior change; fixes a defect.** `lock_is_expired`
  normalised the stored `heartbeat_at` to UTC-aware and then subtracted
  `_dt(now)`, but `_dt()` deliberately stores **naive** UTC (the DB contract).
  Any caller passing an aware clock raised `TypeError: can't subtract
  offset-naive and offset-aware datetimes`, which `start()` re-raised as
  `NotPrimaryInstance` — i.e. an expired lease was reported as "a live instance
  owns this account", permanently deadlocking the run. The comparison now
  happens in naive-UTC on both sides.
- **Strategy rules/parameters changed:** No.
- **Evidence:** `test_a_holder_past_its_lease_can_be_replaced` failed with the
  `TypeError` before the fix and passes after.
- **Status:** implemented and verified

### VB-DRV-008 — Defect: clock seeding bypassed the consumed-event check
- **Phase:** Paper Driver Layer
- **Category / severity:** Correctness / High (defect found while testing)
- **Component:** `core/version_b_paper_driver.py`
- **Observable change:** **Behavior change; fixes a defect.** `start()` peeks
  the first event to seed `DeterministicClock` and buffers it. `_take_event()`
  returned that buffered event **without** checking `is_event_processed`, so a
  resumed driver re-delivered the very first event of the window and reported
  it as newly delivered (509 skipped / 1 delivered, instead of 510 / 0). The
  runtime's own duplicate guard caught it downstream, so no trade was doubled —
  but the resume position the driver reported was wrong. Buffered events now go
  through the same consumed-event check as any other.
- **Strategy rules/parameters changed:** No.
- **Evidence:** Reproduced before the fix (resume delivered 1, skipped 509);
  after the fix `test_shutdown_does_not_lose_the_event_position` asserts
  `skipped == consumed` and `delivered == 510 - consumed`.
- **Status:** implemented and verified

### VB-DRV-009 — Defect: END_OF_DATA marker was written with timeframe `5m`
- **Phase:** Paper Driver Layer
- **Category / severity:** Correctness / Low (defect found while testing)
- **Component:** `core/version_b_runtime.py`
- **Observable change:** **Behavior change; fixes a defect.**
  `close_at_end_of_data` recorded its synthetic marker event with
  `timeframe="5m"`. `last_processed_event` orders by `open_time`, and resume
  reports whatever that returns, so the marker was presented to an operator as
  a 5m market bar it never was. It is now labelled `END_OF_DATA`.
- **Strategy rules/parameters changed:** No.
- **Evidence:** `test_shutdown_does_not_lose_the_event_position` asserts the
  resumed driver reports the last *real* event id.
- **Status:** implemented and verified

### VB-DRV-010 — Defect: automatic lease takeover was silent
- **Phase:** Paper Driver Layer
- **Category / severity:** Observability / High (defect found while testing)
- **Component:** `core/version_b_runtime.py`
- **Observable change:** **Behavior change; fixes a defect.** Expired-lease
  takeover happened inside `store.acquire_lock()`, which writes no audit row.
  Only the *explicit* `takeover=True` path audited `LOCK_TAKEN_OVER`, so an
  automatic takeover — the one case where a second process takes a live
  account — left no record and was indistinguishable from two processes
  trading one account. `start()` now snapshots the incumbent before acquiring
  and audits `LOCK_TAKEN_OVER` with `mechanism` =
  `lease_expired` / `explicit_takeover`, the previous holder and its last
  heartbeat.
- **Strategy rules/parameters changed:** No.
- **Evidence:** `test_a_holder_past_its_lease_can_be_replaced` failed with
  `'LOCK_TAKEN_OVER' not found in ['RUNTIME_STARTED', 'RUNTIME_STARTED']`
  before the fix and passes after.
- **Status:** implemented and verified

### VB-DRV-011 — Known limit retained: one runtime owns one symbol
- **Phase:** Paper Driver Layer
- **Category / severity:** Scope / Informational
- **Component:** `core/version_b_runtime.py`, `core/version_b_paper_driver.py`
- **Observable change:** No behavior change; recorded as an explicit limit.
  `VersionBPaperRuntime.symbol` is a single string, so one runtime instance
  trades one symbol and events for any other symbol return `IGNORED`. Widening
  to multi-symbol would change the identity of the single-instance lock, the
  recovery report and the parity oracle, so it is **out of scope** here and
  documented as a known limit rather than silently widened.
- **Strategy rules/parameters changed:** No.
- **Evidence:** `test_single_symbol_remains_an_explicit_known_limit` asserts
  the cross-symbol `IGNORED` outcome *and* that the limit stays documented in
  the driver source.
- **Status:** documented, intentionally not changed

### VB-LIV-001 — Heartbeat decoupled from market-data arrival
- **Phase:** Liveness Independence
- **Category / severity:** Correctness / High
- **Component:** `core/version_b_paper_driver.py`
- **Observable change:** **Behavior change; fixes a defect.** `PaperDriver` beat
  only inside `_deliver`, on `events_delivered % heartbeat_every == 0`. Liveness
  was therefore a function of data arrival, with three consequences: a quiet
  market produced no beats at all, so the 600 s lease expired while the process
  was alive and a supervisor saw a dead-looking live process; the cadence was
  coupled to an unrelated quantity, so 25 events is ~2 h on a 5m stream but may
  never occur inside one lease on a 1h-only stream; and because "alive but no
  data" and "dead" produced the same observable, the two were
  indistinguishable — exactly what a lease exists to disambiguate. `beat()` is
  now due on elapsed time (`heartbeat_interval`, default 60 s) and is called at
  the top of every loop iteration *before* asking the adapter for data.
  `heartbeat_every` is removed.
- **Strategy rules/parameters changed:** No.
- **Evidence:** `test_the_beat_is_scheduled_by_time_not_by_event_count` (same
  events, 10× the interval ⇒ far fewer beats),
  `test_heartbeat_continues_while_no_market_data_arrives`. Negative control:
  restoring the event counter fails **5** liveness tests, including
  `test_two_live_processes_cannot_own_the_same_run` — i.e. it reproduces the
  false takeover.
- **Status:** implemented and verified

### VB-LIV-002 — The driver waits out a quiet source instead of ending the run
- **Phase:** Liveness Independence
- **Category / severity:** Architecture / Medium
- **Component:** `core/version_b_paper_driver.py`
- **Observable change:** **Behavior change.** `MarketDataAdapter` gains one
  optional method, `idle_until() -> datetime | None`, defaulting to `None`
  ("ask me again immediately"), so every existing adapter is unaffected. A
  source that can be quiet — a socket-backed feed waiting for the next bar —
  reports when it next expects data. `PaperDriver._wait_for_source` then walks
  the clock through the gap in heartbeat-sized steps, beating and assessing
  freshness at each one, instead of treating silence as the end of the stream.
  `DriverReport` gains `idle_waits`, `data_quiet_seconds` and `last_beat`, and
  `stopped_reason` gains `source_idle`. Bounded by `max_idle_waits` (default 64)
  so a source that never resumes cannot hang the loop.
- **Strategy rules/parameters changed:** No.
- **Evidence:** `test_heartbeat_continues_while_no_market_data_arrives`
  (`idle_waits == 1`, `data_quiet_seconds >= 1700`, `heartbeats >= 30`).
  Negative control: making `_wait_for_source` return immediately fails 5
  liveness tests.
- **Status:** implemented and verified

### VB-LIV-003 — Staleness is now detectable with no market events at all
- **Phase:** Liveness Independence
- **Category / severity:** Correctness / High
- **Component:** `core/version_b_runtime.py`
- **Observable change:** **Behavior change; fixes a defect.** Staleness was only
  ever assessed inside `_process_decision`, so it required a decision event to
  arrive. A feed that simply *stopped* therefore produced nothing: no
  `STALE_DATA`, no breaker, no audit row — the one condition the check exists
  for was the one it could not see. `assess_data_freshness()` now measures the
  newest consumed bar against the injected clock and registers the same
  `STALE_DATA` fault through the same `_register_data_fault` path, so it opens
  the same breaker and writes the same audit rows. It returns `None` when no
  data has been consumed yet, because an empty history at start-up is warm-up
  and calling it stale would open the breaker before the first decision. It is
  deliberately **one-directional**: an assessment can register a fault, it never
  clears one, so there is still exactly one path that closes the breaker.
- **Strategy rules/parameters changed:** No. `stale_after_seconds` and
  `data_fault_threshold` keep their existing defaults and meaning.
- **Evidence:** `test_stale_market_data_opens_the_breaker_without_any_event`,
  `test_a_breaker_opened_by_assessment_refuses_the_next_entry`,
  `test_an_open_position_is_still_exited_while_data_is_stale`. Negative control:
  removing the assessment call from the quiet wait fails 4 liveness tests.
- **Status:** implemented and verified

### VB-LIV-004 — `step()` no longer confuses a quiet source with a finished one
- **Phase:** Liveness Independence
- **Category / severity:** Correctness / Medium (defect found while testing)
- **Component:** `core/version_b_paper_driver.py`
- **Observable change:** **Behavior change; fixes a defect.** `step()` returned
  `None` both when the adapter had nothing *yet* and when the stream was over,
  so a caller stepping the driver by hand could not tell a pause from the end of
  the run — and would silently stop a live Paper run. `step()` now waits the
  quiet window out (beating throughout) and only returns `None` when the source
  is genuinely exhausted.
- **Strategy rules/parameters changed:** No.
- **Evidence:** `test_stale_market_data_opens_the_breaker_without_any_event`
  drives the transition with a single `step()`; before the fix that step
  returned `None` and emitted no `MARKET_DATA_QUIET`.
- **Status:** implemented and verified

### VB-LIV-005 — Finding: a silence-opened breaker is cleared by the bar that ends the silence
- **Phase:** Liveness Independence
- **Category / severity:** Behavior finding / Medium — **documented, deliberately
  not changed**
- **Component:** `core/version_b_runtime.py` (`on_event` auto-reset)
- **Observable change:** None. Recorded so the limit is not mistaken for
  protection that does not exist. `on_event` closes a breaker whose reason is in
  `_AUTO_RESET_FAULTS` (`STALE_DATA`, `DATA_NOT_VALID`, `OUT_OF_ORDER_EVENT`)
  after **any** event that processes without error, treating arrival as
  recovery. So a breaker opened by an outage is closed by the first bar that
  ends the outage — and in this data every 15m decision co-emits with a 5m bar,
  so that bar always arrives first. Verified directly: `CIRCUIT_BREAKER_OPEN`
  and then `CIRCUIT_BREAKER_CLOSED`, with the following decision executing
  normally.
  The consequence is bounded and stated plainly: the assessment path produces
  correct health and audit state, but on its own it cannot block the decision
  that immediately follows an outage. Entry blocking on stale data is instead
  guaranteed by `_process_decision`'s own staleness check (`StaleDataRejected`,
  which fires before any entry), and by the breaker whenever it is open at
  decision time. Both are proven.
  Not changed here because arrival of a fresh bar genuinely does make the data
  fresh, so closing a *data-fault* breaker is defensible; making recovery require
  sustained freshness would be a new debouncing policy, and it would alter
  semantics proven at `749468d`. No test asserted `CIRCUIT_BREAKER_CLOSED`
  before this step, so the auto-close was previously unexercised.
- **Strategy rules/parameters changed:** No.
- **Evidence:** `test_stale_market_data_opens_the_breaker_without_any_event`
  asserts the open-then-close order explicitly rather than asserting a state
  that does not survive; `test_a_breaker_opened_by_assessment_refuses_the_next_entry`
  proves the breaker refuses an entry when it is open at decision time
  (`CIRCUIT_OPEN_REJECTED`, no position, `ENTRY_BLOCKED_CIRCUIT_OPEN` audited).
- **Status:** **superseded by `VB-LIV-008`.** Recording it as an accepted limit
  was the wrong call: on review this is a correctness defect, not a policy
  choice, and it has been fixed. The entry above is kept as found so the change
  of verdict is visible.

### VB-LIV-006 — Liveness, lease and staleness transitions are auditable
- **Phase:** Liveness Independence
- **Category / severity:** Observability / Medium
- **Component:** `core/version_b_runtime.py`, `core/version_b_paper_driver.py`
- **Observable change:** **Behavior change.** New audit codes:
  `MARKET_DATA_QUIET` (WARNING, when the source goes quiet, with
  `quiet_since`, `expected_resume_at` and `last_event_time`) and
  `MARKET_DATA_WAIT_ENDED` (INFO). `RUNTIME_STARTED` now records
  `heartbeat_interval` alongside `lease_seconds` and `holder`, so the policy in
  force is readable from the trail later. `STALE_DATA` rows raised by the clock
  carry `source: "liveness_assessment"`, so a stale reading is never confused
  with a decision-path rejection. The beat itself is durable lease state
  (`runtime_locks.heartbeat_at`), not a log line.
- **Strategy rules/parameters changed:** No.
- **Evidence:** `test_liveness_and_staleness_transitions_are_auditable`.
- **Status:** implemented and verified

### VB-LIV-007 — Liveness policy is operator-settable from `main.py`
- **Phase:** Liveness Independence
- **Category / severity:** Architecture / Low
- **Component:** `main.py`
- **Observable change:** **Behavior change.** `--heartbeat-interval SEC`
  (default 60) and `--lease-seconds SEC` (default 600) are exposed, and the run
  summary reports beat count, quiet windows and total quiet seconds. Both
  refusal paths are unchanged and still exit `2`.
- **Strategy rules/parameters changed:** No.
- **Evidence:** Manual run — 200 events with `--heartbeat-interval 300
  --lease-seconds 900` produced 142 beats and exit 0; `--resume` then skipped
  200 and delivered 310 (= 510). `TRADING_MODE=paper` and
  `--version-b-paper` without `--frames-dir` both still exit `2`.
- **Status:** implemented and verified

### VB-LIV-008 — Verdict on `VB-LIV-005`: CORRECTNESS DEFECT; recovery rebound to freshness
- **Phase:** Stale-Recovery Semantics
- **Category / severity:** Correctness / **High** — fixes a defect
- **Component:** `core/version_b_runtime.py`
- **Verdict:** **CORRECTNESS DEFECT**, not `CORRECT AS DESIGNED`.
- **Source of truth before the fix — there was not one.** Three places decided
  stale-data state and they disagreed:
  1. `_process_decision` → `_staleness(decision_time)` measures the **decision
     timeframe**, is recomputed from `self.frames` on every call, and raises
     `StaleDataRejected` unconditionally. This is the only gate that could never
     be talked past, and it is why no decision was ever *taken* on stale data.
  2. `assess_data_freshness()` measures the newest bar on **any** timeframe.
  3. `on_event`'s recovery block measured **nothing** — it granted `HEALTHY`,
     zeroed `consecutive_data_faults` and closed an `_AUTO_RESET_FAULTS` breaker
     because an event had *arrived*.
  (3) wrote over (1) and (2) with no evidence, and (3) is what
  `operational_state()` publishes durably and what the audit trail records.
- **Why it is a defect, from the code.** The module's own comment stated the
  policy as "closes on the next healthy **decision**"; the code closed it on the
  next healthy **event**. At a 15m boundary the feed delivers the 1h bar, then
  the closing 5m bar, then the 15m decision — so the breaker was cleared by two
  events that carry no evidence about the timeframe gating entries, one step
  before the decision it existed to guard. Measured on the identical sequence
  (5-minute silence before the boundary, `stale_after_seconds=60`,
  `data_fault_threshold=1`):

  | step | before | after |
  |---|---|---|
  | after silence, 1h bar | `circuit_open=False` | `circuit_open=True` |
  | 5m bar | `circuit_open=False` | `circuit_open=True`, `data=STALE` |
  | 15m decision | **`EXECUTED`** | **`CIRCUIT_OPEN_REJECTED`** |
  | positions opened | **1** | **0** |

  So an entry *was* taken on the first decision after a data outage. Separately,
  a 3-hour stall of the 15m stream with 5m bars still flowing published
  `data: HEALTHY, circuit_open: false` durably while `_staleness` read 12600 s
  against a 3600 s threshold and every decision was rejected.
- **The fix.** `_recover_data_health()` replaces the inline block. Recovery is
  granted only when `_decision_data_age()` — the newest bar on the timeframe
  that gates entries, measured against the injected clock — is within
  `stale_after_seconds`. `None` (no decision-timeframe bar yet) is start-up, not
  staleness, and still recovers, so warm-up is unaffected. The threshold is the
  existing `stale_after_seconds`, already calibrated to this timeframe, so **no
  new parameter and no new policy** were introduced. When nothing needs
  recovering the method returns immediately, leaving the healthy path identical.
  `CIRCUIT_BREAKER_CLOSED` now records `recovered_on` and
  `decision_data_age_seconds`, so the trail says what granted recovery.
- **Source of truth after the fix, stated explicitly.** `assess_data_freshness()`
  (any timeframe) answers *"has the feed stopped?"* and opens the breaker.
  `_decision_data_age()` (decision timeframe) answers *"is it safe to enter
  again?"* and is the **only** thing that grants recovery. `_staleness` still
  gates every decision unconditionally. No event clears anything by arriving.
- **Not a debouncing policy.** Recovery is not delayed by a counter or a timer;
  it is granted by the first event that is actual evidence — in this fixture the
  15m decision bar itself, one event later. Proven not to stick:
  `test_the_decision_bar_is_the_recovery_event_and_the_next_one_proceeds` shows
  the breaker closing, health returning to `HEALTHY`, the next signal executing
  and the trade closing at `net_pnl 32.5`.
- **Strategy rules/parameters changed:** No. Risk, execution and lifecycle
  semantics untouched.
- **Evidence:** `test_neither_the_1h_nor_the_5m_bar_clears_a_stale_breaker`,
  `test_the_first_decision_after_an_outage_is_refused_not_executed`,
  `test_the_decision_bar_is_the_recovery_event_and_the_next_one_proceeds`,
  `test_recovery_never_depends_on_how_many_events_arrive`, and the corrected
  `test_stale_market_data_opens_the_breaker_without_any_event`. Negative
  control: reverting the fix fails all 5, including
  `EXECUTED != CIRCUIT_OPEN_REJECTED`.
- **Known limit that remains, stated plainly.** A stall of the *decision*
  timeframe while other timeframes keep flowing is not detected by
  `assess_data_freshness`, because that measure asks "is anything arriving".
  Such a stall produces no decision events, so no entry can be taken, and the
  first decision attempted on it is refused by `_staleness`. Detecting the stall
  itself would require a per-timeframe expected cadence — a new parameter and a
  new policy — which is out of scope here.
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
