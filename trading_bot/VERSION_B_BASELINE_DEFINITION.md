# Historical Baseline — Definition and Interpretation Contract

This document pins what the Historical Baseline measures before any result is
collected. It changes no strategy rule and no parameter. It authorizes no
baseline run by itself; `VERSION_B_ACCEPTANCE.md` still gates that.

## 1. The Baseline is Type 1, and the code proves it

**Definition.** The Historical Baseline measures the **frozen decision and
execution model** — Strategy → `StrategyCore` → `RiskEngine` →
`VersionBReplayEngine` → `VersionBExecutionService` under the
`vb-1.0-5m-stop-first` model — replayed over historical OHLCV.

It is **not** a measurement of the operational system, and it is not a forecast
of live results.

This is not a policy choice; it is what the code does:

| Evidence | Location |
|---|---|
| The replay opens positions directly on the execution service | `core/version_b_replay.py:208` — `self.service.open_position(...)` |
| The External Execution contract is **not** in the baseline path | `grep -rn "external_execution\|ExternalExecutionService\|OrderIntent"` over `core/version_b_replay.py`, `core/version_b_paper.py`, `backtesting/`, `core/bot.py` → **zero hits** |
| Entry fill is the next 15M open, full quantity, guaranteed | `core/version_b_replay.py:195` — `entry_price = float(decision_row["open"])` |
| Exits come from the frozen intrabar model only | `core/execution_model.py` — `evaluate_intrabar`, one event per 5M bar, stop-first |
| Funding is never charged | `core/execution_service.py:142` — `funding=0.0`; `calculate_realized_net_pnl` is called without a funding argument |
| Costs are modeled constants, not observed fills | `execution.fee_rate=0.0004`, `execution.slippage_rate=0.0002` from `config.yaml` |

Consequence: an order intent, an adapter, an acknowledgement, a rejection, a
partial fill, a timeout, an `UNKNOWN` state, a cancel rejection, a disconnect,
latency, and exchange-side rounding **do not exist in this measurement**. The
contract in `VERSION_B_EXTERNAL_EXECUTION.md` governs the operational path and
is deliberately outside the Baseline.

## 2. What the Baseline represents

1. That the frozen Long/Short decision logic, on aligned 15M/1H data with the
   data-quality contracts, produces a specific, reproducible set of decisions.
2. That those decisions, passed through the canonical risk/accounting
   definitions and the frozen 5M stop-first exit model, produce a specific,
   reproducible lifecycle and net-PnL series.
3. A **reference point** for Ablation, Sensitivity, and OOS/Walk-forward: any
   later variant is compared against this same model, on the same data, with
   the same execution assumptions.
4. That the pipeline is instrumented: every decision, lifecycle, event, and
   fill is durably recorded with its lineage.

## 3. What the Baseline does NOT represent

It is not evidence for any of the following, and must never be quoted as such:

1. Expected live or Paper return, win rate, drawdown, or profitability.
2. Executable capacity: fill probability, slippage under real spread/depth,
   partial fills, rejections, or queue position.
3. Cost reality: real taker/maker fees, **funding** (modeled as exactly 0.0),
   borrowing, or exchange contract precision.
4. Operational reliability: restart recovery, duplicate orders, missed exits,
   protection confirmation, disconnects, rate limits, clock skew, stale data.
5. Statistical validity. A single in-sample replay is not OOS, not
   walk-forward, and not a significance claim.
6. A pass/fail judgement on the strategy. See §6.

## 4. Naming rules (mandatory)

The artifact must be self-labelling so it cannot be read as Type 2.

- **Report type field:** `"report_type": "STRATEGY_MODEL_BASELINE"`.
- **Scope field:** `"measurement_scope": "TYPE_1_STRATEGY_MODEL"`.
- **Mandatory banner field:** `"not_an_operational_result": true`.
- **File name pattern:**
  `baseline_strategymodel_<symbol>_<direction>_<execution_model_version>_<data_hash12>.json`
- **Forbidden in a Type-1 artifact:** the words `performance`, `live`,
  `operational`, `expected return`, and any profitability `PASS`/`FAIL`
  verdict. The existing `performance_report.py` name must not be reused for a
  Baseline artifact.

## 5. Metric names and the single allowed definition

Two incompatible "Max Drawdown" definitions exist in this repository today.
`performance_report.compute_max_drawdown` (`performance_report.py:60-77`)
cumulates **closed-trade PnL** and adds an arbitrary `+100.0` base, so its
percentage is scale-arbitrary and it is not equity-based.
`VersionBReplayEngine` emits an `equity_curve` but computes **no** drawdown at
all. Both are therefore unusable as-is for a Baseline.

Pinned definitions for the Baseline artifact:

| Metric (exact key) | Definition |
|---|---|
| `model_trade_count` | Closed lifecycles. Partials are events inside one lifecycle, never extra trades. |
| `model_win_rate_pct` | Closed lifecycles with `realized_net_pnl > 0` ÷ `model_trade_count`. |
| `model_profit_factor` | Σ positive lifecycle net PnL ÷ \|Σ negative lifecycle net PnL\|. |
| `model_net_pnl` | Σ `calculate_realized_net_pnl(fills)` over closed lifecycles. **Funding is 0.0 and must be reported as such.** |
| `equity_curve_max_drawdown_pct` | Computed from the replay `equity_curve` against the **actual** `initial_balance`. Never the `+100` base. Never from closed-trade PnL. |
| `model_cost_total` | Fees deducted, with slippage reported separately as attribution (it is already inside fill prices). |

Every metric key is prefixed `model_` or `equity_curve_` on purpose: a bare
`win_rate` or `max_drawdown` in a Version B artifact is ambiguous and is not
allowed.

## 6. Acceptance for the Baseline step

The Baseline is **accepted as a measurement** only when all of these hold:

1. The artifact carries §4's `report_type`, `measurement_scope`,
   `not_an_operational_result`, and file-name pattern.
2. The artifact carries complete lineage: `code_commit`, `config_sha256`,
   `data_hash`, `execution_model_version`, `run_id`, `strategy_version`,
   universe, and timeframes. Today `_ensure_run_metadata` writes
   `code_version="working-tree"` and `data_hash=None`
   (`core/version_b_replay.py:95,98`); a Baseline run recorded that way is not
   reproducible and must not be accepted.
3. Every metric uses the §5 key and definition.
4. The artifact contains **no** profitability verdict. `SUCCESS_CRITERIA`
   (`performance_report.py:47-53`: win rate > 55, PF > 1.5, DD < 15%, PnL > 0,
   ≥ 100 trades) must **not** be applied to a Type-1 artifact. Those are
   operational targets; scoring a model against them manufactures a tuning
   incentive that `VERSION_B_SCOPE.md` §3 forbids.
5. The acceptance gate in `VERSION_B_ACCEPTANCE.md` reports `PASS`, including
   the immutable Version A object checks.
6. `baseline_collected` is flipped only in the same change that stores the
   artifact, and the Change Ledger records the run identity.

A Baseline that fails any of these is not a Baseline; it is an unlabelled
model output.

## 7. Drawdown is a lower bound, not a measurement of the true trough

The equity curve is sampled **once per 15m decision bar**
(`core/version_b_replay.py:400,444`), while exits resolve on 5m bars. A deeper
trough inside a decision interval is therefore invisible to
`equity_curve_max_drawdown_pct`. The metric definition states this, and the
artifact carries it in `metric_definitions.max_drawdown`. It must be reported
as a lower bound and never as the true maximum drawdown.

## 8. Costs must be visible

`model_cost_total` reads the `fees` key from each trade row. That key did not
exist in `_trade_rows()`, so the metric would have silently reported `0.0`
while net PnL already had fees deducted — an internally inconsistent artifact.
`_trade_rows()` now emits `fees` and `slippage`, and a test asserts both the
key's presence and that the metric is non-zero when fees exist.

## 9. Readiness verdict

**All six gaps are closed.** None required a strategy, threshold, or risk
parameter change.

| # | Gap | Resolution | Proof |
|---|---|---|---|
| G1 | No binding artifact contract | `core/baseline_artifact.py`: schema, mandatory labels, pinned metric keys, forbidden-key rejection, filename pattern, `write_baseline_artifact` | `ArtifactContractTests` (10 tests) |
| G2 | Lineage not reproducible | `core/measurement_lineage.py`: real commit + dirty flag, sha256 frame identity, config sha256, model version. `version_b_replay` records them instead of `working-tree` / `None` | `LineageTests`, `ReplayLineageTests` |
| G3 | Two incompatible drawdown definitions | `core/measurement_metrics.py` pins one; `performance_report.compute_max_drawdown` is marked legacy and points at the canonical function | `MetricDefinitionTests` |
| G4 | Legacy report could emit a confusable `PASS` | `report_type=LEGACY_OPERATIONAL_SUMMARY`, `measurement_scope=TYPE_2_OPERATIONAL_LEGACY`, verdict renamed to `legacy_operational_verdict` with `LEGACY_OPERATIONAL_*` values, plus `assert_not_baseline_artifact` | `test_the_legacy_report_is_typed_and_carries_no_bare_verdict` |
| G5 | A test run dirtied a **tracked** `logs/bot.log`, so any post-test tree was dirty and `require_clean_tree` could never hold | Untracked the 10 files the repo's own `.gitignore` already excludes (`logs/`, `*.log`, `*.pyc`); files remain on disk | Full suite + gate run produces **zero** tracked-file changes |
| G6 | Silently aggregating symbols into one per-symbol artifact | `universe == [symbol]` is enforced by the validator | `test_a_silently_aggregated_universe_is_refused` |

Reproducibility is now testable rather than asserted:
`artifact_fingerprint()` hashes the artifact with only the declared
`non_deterministic_fields` (`generated_at`) removed, and
`validate_baseline_artifact` refuses to widen that list. Two runs over
identical frames produce identical fingerprints; different frames produce a
different `data_hash` and a different fingerprint.

**Verdict: Version B is READY as a measurement layer for a Type-1 Historical
Baseline**, subject to two standing conditions: the artifact must be built with
`require_clean_tree=True` from a committed tree, and the gate must report
`PASS`.
