# Frozen Version B Acceptance Gate

This document defines the machine-checkable gate required before any Historical or Paper performance baseline is collected.

## Current remediation gate record

As of 2026-09-11, the automated gate reports **PASS** with 181 mandatory
assertions passed, 0 failed, 0 errors, 0 skipped across 17 modules.
`baseline_collected` remains `false`.

The Version A object checks previously failed for an environmental reason: a
shallow single-branch clone does not contain commit `89bec19`. The gate now
repairs availability itself (`_ensure_version_a_object`: fetch the object, then
remove shallow boundaries) and reports the attempt as its own
`version_a_object_available` check. **No check is skipped or weakened** — the
immutable commit, ancestry, and config-hash checks still run and still have to
pass, and Version A is never rewritten. Verified from a cold `--depth 1`
clone: `restored: true`.

| Acceptance surface | Status | Evidence / limitation |
|---|---|---|
| Full Version B path | `PASS` | Deterministic gate suite, including explicit TradingBot B wiring |
| Backtest ↔ Paper replay parity | `PASS` | Shared replay engine and identical decision/lifecycle fixture |
| External execution contract | `PASS` | Computed from `WriteAheadAndIdentityTests`, `FailureAccountingTests`, `StoreContractTests`: write-ahead intent, retry identity, lost response, `UNKNOWN`, partial fill, rejection, terminal-state guard |
| Restart recovery contract | `PASS` | Computed from `RestartRecoveryTests`: offline fill, crash between fill and event, unresolved reporting, vanished resting stop, idempotent re-recovery, hydrated accounting |
| Baseline readiness contract | `PASS` | Computed from `LineageTests`, `MetricDefinitionTests`, `ArtifactContractTests`, `ReplayLineageTests`, `ReproducibilityTests`: artifact contract, reproducible lineage, pinned drawdown, legacy separation, aggregation guard |
| Operational acceptance contract | `PASS` | Computed from the eight `test_version_b_operational_acceptance` classes: bar-by-bar operation, crash/restart/continue, duplicate events, venue degradation, bad data, single instance, operational controls, and event-driven vs replay parity |
| Paper driver contract | `PASS` | Computed from the seven `test_version_b_paper_driver` classes: end-to-end start→clock→event→decision→risk→intent→fill→lifecycle→DB→restart→resume **through the driver**, the `MarketDataAdapter` boundary, clock separation, window/boundary negative controls, live heartbeat and lease, **process liveness independent of market-data flow**, and preserved parity plus guarantees |
| Integration acceptance contract | `PASS` | Computed from `OnePathNotParallelImplementationTests`, `StrategyRiskExecutionEndToEndTests`, `RestartDuringOpenLifecycleTests`, `LegacyFallbackClosureTests`: one unified path, decision→DB end to end, restart from rows alone, legacy fallback closure |
| Runtime/exchange protection acknowledgement | `UNKNOWN` | Requires a real venue: create → fetch/ack confirmation and `clientOrderId` deduplication |
| Restart reconciliation against an exchange | `UNKNOWN` | Durable DB reconstruction and deterministic reconciliation are covered; a live-account restart drill is not |

The eight `PASS` rows are computed from the junit report per test group. The two
`UNKNOWN` rows are `UNKNOWN` by construction: a network-free gate never infers
exchange evidence, and the gate publishes the evidence each one requires under
`residual_unknowns`. A residual `UNKNOWN` does not authorize Live.

**Negative controls.** Forcing `NOT_FOUND` to resolve as `ACCEPTED` in
`core/external_execution.py` turns `external_execution_contract` and
`restart_recovery_contract` to `FAIL` with three named tests. Removing the
forbidden-key guard on `counts` in `core/baseline_artifact.py` turns
`baseline_readiness_contract` to `FAIL` with one named test while the other two
surfaces stay `PASS`. These surfaces are therefore computed and independent,
not vacuous.

**Integration negative controls.** Removing the persisted `leverage` from
`open_position` fails 5 tests across `StrategyRiskExecutionEndToEndTests` and
`RestartDuringOpenLifecycleTests`. Disabling the legacy-paper guard fails both
`LegacyFallbackClosureTests` tests. Both sabotages were reverted and verified
byte-identical to the pre-sabotage source.

**Operational negative controls.** Removing the consumed-event guard fails
exactly the two idempotency tests. Reordering the feed so a decision precedes
the 5m bar closing at the same instant fails the replay-parity test. Both
sabotages were reverted and verified byte-identical.

**Paper driver negative controls.** Making `VersionBPaperRuntime.run()` silently
accept a window fails `test_handing_the_runtime_a_window_fails`. Letting
`DeterministicClock` move backwards fails both clock-boundary tests. Disabling
the driver's consumed-event check — so a restart replays the window instead of
resuming after the last persisted event — fails both end-to-end tests, and fails
them with `ClockCannotRewind`, i.e. the clock catches the replay independently of
the store. Removing the driver's heartbeat call fails
`test_the_driver_heartbeats_and_the_lease_advances`, which is what keeps the
lease from being dead code. All four sabotages were reverted and verified
byte-identical to the pre-sabotage source.

**Liveness negative controls.** Restoring the event-counter beat
(`events_delivered % 25`) fails **5** liveness tests, including
`test_two_live_processes_cannot_own_the_same_run` — that is the false takeover
reproduced. Removing the freshness assessment from the quiet wait fails 4.
Making `_wait_for_source` return immediately, so a quiet source reads as an
exhausted one, fails 5. All three sabotages were reverted and verified
byte-identical.

**Known limit — a silence-opened breaker is cleared by the bar that ends the
silence.** `on_event` closes an `_AUTO_RESET_FAULTS` breaker after any event that
processes cleanly, so the outage breaker cannot block the decision that
immediately follows the outage. Entry blocking on stale data is guaranteed
instead by `_process_decision`'s own staleness check and by the breaker whenever
it is open at decision time; both are proven. Recorded as `VB-LIV-005` rather
than silently changed.

**Tree cleanliness.** Running the full suite and the gate now changes **no
tracked file**. Previously every run appended to the tracked `logs/bot.log`,
which left the tree permanently dirty and made `require_clean_tree` impossible
to satisfy.

See `VERSION_B_EXTERNAL_EXECUTION.md` for the contract itself and the explicit
split between double-proven behavior and required exchange evidence.

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
- Baseline readiness: artifact contract and labels, reproducible code/data/config/model lineage, one pinned max-drawdown definition, legacy-report separation, per-symbol aggregation guard, artifact reproducibility.
- External execution: write-ahead intent, retry identity, lost response, `UNKNOWN`, partial fill, rejection, unconfirmed protection, terminal-state guard.
- Restart recovery: reconciliation of every non-terminal intent, offline fills, dropped-event derivation, idempotent re-recovery, hydrated accounting, unresolved reporting.

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
