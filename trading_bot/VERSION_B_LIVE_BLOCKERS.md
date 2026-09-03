# LIVE BLOCKERS

Live trading is blocked. This is an explicit release checklist, not a
warning. Every item needs evidence and an owner before the Live Readiness gate
can pass.

| ID | Severity | Component | Blocker | Observable impact | Required evidence/decision |
|---|---|---|---|---|---|
| `LB-001` | Critical | Acceptance | No approved historical baseline, OOS/walk-forward review, or Paper operational validation exists yet. | Live performance, drawdown, and parity are unknown. | Complete the ordered gates in `VERSION_B_SCOPE.md`; do not select parameters from B results. |
| `LB-002` | Critical | Safety / Execution | `No position without confirmed protection` is enforced in code and fails closed, but is not yet proven against the real exchange acknowledgement/query path. | A filled position could remain without an exchange-confirmed SL if the venue's ack semantics differ from the modeled ones. | Deterministic proof exists (`VB-EXEC-003`/`VB-EXEC-005`: fetch/ack confirmation, fail-closed, emergency flatten). Remaining: real create → fetch/ack evidence per venue. |
| `LB-003` | Critical | Persistence / Recovery | OrderManager and the live adapter are still not wired to the external execution contract, so restart reconciliation is proven only against a double. | Duplicate orders, missed exits, or an unknown lifecycle can occur after a crash on the legacy live path. | Deterministic proof exists (`VB-EXEC-004`: intent reconciliation, offline fills, dropped-event derivation, idempotent re-recovery, hydrated accounting). Remaining: wire the live adapter, then a live-account restart drill. |
| `LB-004` | Critical | Execution | Timeout/lost response, `UNKNOWN`, partial fill, rejection, cancel, and duplicate submission are resolved deterministically; real disconnect, rate-limit, and cancel-rejection behavior is not. | The local lifecycle may still diverge from exchange state under real network conditions. | Deterministic failure fixtures pass (`FailureAccountingTests`). Remaining: exchange-specific recovery policy evidence under real failure injection. |
| `LB-005` | High | Accounting | Funding, liquidation/mark price, exchange contract precision, and full fee schedule are not captured end-to-end. | Net PnL, equity, planned risk, and portfolio risk-at-stop can be misstated. | Reconciliation against exchange statements and explicit funding/fee inputs. |
| `LB-006` | High | Risk | Canonical risk functions exist, but legacy RiskManager enforcement and a global portfolio risk cap are not fully migrated. | Daily loss and aggregate stop risk may not actually block live entries. | Wire and test enforcement. If a new cap blocks candidates, log it as a separate Risk Policy Change. |
| `LB-007` | High | Strategy / Data | Full bot wiring to the aligned 15M decision clock and frozen Strategy Core is incomplete. | Live signal timing may differ from the audited B contract. | Paper/Strategy parity and decision snapshot evidence for all configured symbols/timeframes. |
| `LB-008` | High | Paper parity | Paper compatibility mode still uses ticker polling; deterministic 5M replay is not yet the sole operational path. | Paper and historical exit behavior can differ. | Pass deterministic replay, lifecycle, PnL reconciliation, and parity fixtures. |
| `LB-009` | High | Operations | Backup restore, retention, alerting, and process supervision have not passed an operational drill. | Audit data or safety alerts may be unavailable during failure. | Restore drill, backup policy, alert delivery test, and supervised restart evidence. |
| `LB-010` | High | Security | Telegram command authorization and secret/runtime separation have not passed a security review. | Unauthorized commands or secret leakage could affect execution. | Authorized-user tests, secret scan, and `.env`/effective snapshot review. |
| `LB-011` | Medium | Data vendor | Exchange clock skew, outages, stale candles, gaps, symbol status changes, and rate limits need live drills. | Invalid or stale data can create or delay decisions. | Monitoring thresholds, fail-closed policy, and outage/recovery tests. |
| `LB-012` | Medium | Scope control | No optimization or tuning is permitted in Version B, and no live approval exists. | Any unlogged parameter/edge change invalidates the lineage. | Change Ledger review and explicit Live Readiness approval only after all critical/high blockers clear. |

## Non-negotiable stop conditions

- Stop placement/confirmation failure.
- Any unknown exchange order state without a deterministic reconciliation result.
- Missing or stale decision data.
- Persistence failure that prevents lifecycle reconstruction.
- Daily loss or portfolio stop-risk state unavailable.
- Any mismatch between observed live fills and the canonical lifecycle accounting
  that cannot be reconciled.
