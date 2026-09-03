# External Execution + Restart Recovery Contract

One integrated topic. It covers the path from a local decision to an
exchange-acknowledged order and back, and the reconstruction of that state
after a process restart. It does **not** authorize Live. It does not change any
strategy rule, parameter, or exit level. It does not start a Historical
Baseline.

## 1. The chain

```
Order Intent  ->  Execution Service  ->  External Adapter  ->  VersionBStore  ->  TradeLifecycle
   (1)                  (2)                    (3)                  (4)                (5)
```

| Step | Owner | Rule |
|---|---|---|
| (1) Order Intent | `core/external_execution.py` | A **durable intent row is written before any network call**. The intent owns the identity: `order_intent_id`, `client_order_id`, `purpose`, intended quantity/price. |
| (2) Execution Service | `core/external_execution.py` (`ExternalExecutionService`) + `core/execution_service.py` | Owns submission, acknowledgement resolution, fill accounting, and lifecycle events. Never contacts an exchange directly except through the adapter. Never invents state. |
| (3) External Adapter | `ExternalOrderAdapter` (abstract) | Narrow surface: `submit`, `fetch`, `fetch_open_orders`, `fetch_positions`. Returns a **normalized** `Acknowledgement`; it must not translate a lost response into a success. |
| (4) VersionBStore | `database/version_b_store.py` | The only source of truth. Identity-keyed idempotent writes. Restarts read from here, never from memory. |
| (5) TradeLifecycle | `core/trade_lifecycle.py` | One `trade_id`. Exits and protection updates are events/fills inside it. Reconstructed after restart with `TradeLifecycle.from_records`. |

## 2. Intent status machine

```
CREATED -> SUBMITTED -> {ACCEPTED, PARTIALLY_FILLED, FILLED, REJECTED, CANCELED, UNKNOWN}
UNKNOWN -> {ACCEPTED, PARTIALLY_FILLED, FILLED, REJECTED, CANCELED}   (only by reconciliation)
FILLED | REJECTED | CANCELED  -> terminal
```

- **Resolved/known:** `ACCEPTED`, `PARTIALLY_FILLED`, `FILLED`, `REJECTED`, `CANCELED`.
- **Unresolved:** `CREATED`, `SUBMITTED`, `UNKNOWN`. Unresolved is never success
  and never becomes success by itself.
- `FILLED`, `REJECTED`, `CANCELED` are terminal. A conflicting transition out of
  a terminal state raises `ValueError`; a fill-bearing intent cannot regress to
  `PARTIALLY_FILLED`. The store enforces this, not the caller.

## 3. Acknowledgement normalization

The adapter returns `ExchangeOrderStatus`:
`NEW`, `PARTIALLY_FILLED`, `FILLED`, `REJECTED`, `CANCELED`, `NOT_FOUND`, `UNKNOWN`.

Mapping and its meaning:

| Ack status | Intent result | Fill recorded? |
|---|---|---|
| `NEW` | `ACCEPTED` | no (resting order) |
| `PARTIALLY_FILLED` | `PARTIALLY_FILLED` | yes, for `filled_quantity` only |
| `FILLED` | `FILLED` | yes, for `filled_quantity` only |
| `REJECTED` | `REJECTED` | no |
| `CANCELED` | `CANCELED` | no |
| `NOT_FOUND` | **no state change** — unresolved | no |
| `UNKNOWN` | **no state change** — unresolved | no |
| transport failure (`LostResponse`) | unresolved; must be reconciled | no |

`NOT_FOUND` deliberately does **not** mean "the order never happened". An order
whose response was lost may exist and be invisible to a single query, so absence
of evidence is never converted into a rejection.

## 4. Invariants (these are what the tests prove)

1. **Write-ahead intent.** No adapter call happens before the intent row exists.
2. **One identity per purpose.** `order_intent_id = {trade_id}:{purpose}` and
   `client_order_id = {run_id}:{trade_id}:{purpose}` are deterministic and
   **stable across retries**. A retry is never a second order.
3. **No duplicate fills.** A fill is recorded only when the acknowledged
   `filled_quantity` exceeds the quantity already recorded for that intent.
   `fill_id = {order_intent_id}:fill:{n}`, so re-observing the same exchange
   fill after a restart produces the same id and no new row.
4. **No duplicate events.** Lifecycle events are found by `(trade_id,
   event_type)` before being appended, and `event_id` is
   `{trade_id}:{sequence}:{event_type}`. Sequence is `max(existing)+1`.
5. **No invented quantity.** A partial fill yields a lifecycle sized to the
   filled quantity. Protective orders are sized from the **acknowledged filled
   quantity**, never from the intended quantity.
6. **No fabricated state.** A lost response that cannot be reconciled stays
   `UNKNOWN` and raises `UnresolvedOrderState`. It is never reported as filled,
   accepted, or protected.
7. **No position without confirmed protection.** Confirmation requires a
   **fetch/ack** of the stop-loss intent, not a non-empty create response. If
   confirmation fails after an entry fill, the filled quantity is
   emergency-flattened and `ProtectionNotConfirmed` is raised.
8. **Restart is a no-op on identity.** Recovery followed by recovery again
   writes zero new intents, fills, or events, and does not change equity.
9. **Recovery is explicit.** Every unresolved intent after reconciliation is
   reported. A report containing one is not clean and blocks the run.

## 5. Recovery procedure (deterministic part)

```
recover(run_id):
  1. intents = store.reconcilable_order_intents(run_id)   # everything non-terminal
  2. for each intent:
       resting (ACCEPTED/PARTIALLY_FILLED):
           ack = adapter.fetch(intent)
           resolved & filled -> apply fill idempotently, report as observed_offline_fill
           resolved & resting -> no change
           not resolved / query failed -> downgrade to UNKNOWN, report as unresolved
       unresolved (CREATED/SUBMITTED/UNKNOWN):
           ack = adapter.fetch(intent)
           resolved -> store.update_order_intent(...); apply fill idempotently
           otherwise -> keep unresolved, add to report
  3. for each open trade: reconcile the trade row from durable fills (derive any
     lifecycle event a crash dropped), then rebuild TradeLifecycle.from_records
     and hydrate the ExecutionService position (margin, balance, tp1_hit,
     persisted-identity sets, trade counter)
  4. a trade whose protective intents are not all confirmed -> protection_unconfirmed
     (hydrated state is never treated as protected)
  5. a TP1 that filled while offline with no recorded BE update -> pending_management
     (the stop still has to be amended on the exchange; recovery does not claim it did)
  6. return RecoveryReport(resolved, unresolved, hydrated_trades,
                           protection_unconfirmed, hydration_failures,
                           observed_offline_fills, pending_management,
                           duplicate_fills_prevented, duplicate_events_prevented)
```

`is_clean` is true only when `unresolved`, `protection_unconfirmed`, and
`hydration_failures` are all empty. A restart with an unresolved intent
hydrates nothing for that trade and reports it. It is never treated as
protected or healthy.

## 6. Proven by deterministic double vs. still needing a real exchange

**Proven here** by `DeterministicExchangeDouble` in
`test_version_b_external_execution.py` — the double implements
`ExternalOrderAdapter` and models: response loss after execution, client-order-id
deduplication, partial fills, explicit rejection, cancel, not-found, and
ambiguous/unknown replies. The tests prove the **local** invariants above: no
duplicate order/fill/trade, no fabricated success, correct restart
reconstruction, fail-closed protection.

**Not proven — requires real exchange evidence:**

1. That the venue actually deduplicates on `clientOrderId` (invariant 2 depends
   on it). Binance-specific behavior must be observed, not assumed.
2. That `fetch_order`/`fetch_open_orders` semantics match the normalized
   statuses, including eventual visibility lag after acceptance.
3. Real fill rounding, `reduceOnly`/`closePosition` semantics, partial-fill
   commission reporting, funding, and mark/liquidation behavior.
4. Real timeout, disconnect, rate-limit (HTTP 429/418), and order-state query
   behavior under load.
5. That a real restart against a real account reconciles every open `trade_id`,
   intent, order, event, and fill.
6. Exchange-side confirmation latency for stop placement (the create→fetch/ack
   window).

These remain `UNKNOWN` in `version_b_acceptance.py`
(`live_exchange_acknowledgement`, `restart_exchange_reconciliation`) and in
`VERSION_B_LIVE_BLOCKERS.md` (`LB-002`, `LB-003`, `LB-004`).

## 7. Boundary rules

- No strategy rule, score, gate, level, allocation, or parameter is read or
  changed by this module. Entry candidates arrive already decided.
- No real exchange client is imported or called by this module or its tests.
- No market data is downloaded and no performance baseline is produced.
