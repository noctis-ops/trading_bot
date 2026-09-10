# Version B test classification

The acceptance command runs only deterministic tests. Results are reported by
`version_b_acceptance.py`; no market-data download or performance baseline is
performed.

| Test module | Class | What it proves | What it does not prove |
|---|---|---|---|
| `test_version_b_config_contracts.py` | Pure unit | Runtime source/effective snapshot agreement, dormant parameter handling, domain contracts | Exchange environment behavior |
| `test_version_b_data_semantics.py` | Pure unit | UTC closure, alignment, freshness, gap and warm-up classifications | Vendor clock quality |
| `test_version_b_strategy_freeze.py` | Pure unit / integration | Indicator parity, frozen Long/Short StrategyCore outputs, stop-plan parity, 5M entry invariance | Future/live signal quality |
| `test_version_b_risk_model.py` | Pure unit | Equity, planned risk, realized net PnL, daily loss and portfolio risk-at-stop formulas | Exchange liquidation/funding statements |
| `test_version_b_execution_lifecycle.py` | Pure unit | Frozen intrabar decision and lifecycle state contracts | Exchange acknowledgement |
| `test_version_b_execution_service.py` | Integration | Shared entry/fill/protection/TP1/BE/TP2/close service, costs and canonical risk facade | Network, exchange order state and restart hydration |
| `test_version_b_persistence.py` | Integration / recovery | Durable identity, ordered events, fills, backup and reconstruction | Filesystem/SQLite failure drills under production load |
| `test_version_b_backtest.py` | Replay / deterministic end-to-end | StrategyCore-to-risk-to-execution replay, 15M decisions, 5M exits and DB reconstruction | Historical performance or intrabar truth beyond the frozen model |
| `test_version_b_replay_parity.py` | Replay / deterministic end-to-end | Backtest and Paper share the same decision and lifecycle output; explicit TradingBot B path is network-free | Binance connectivity, live protection acknowledgement |
| `test_version_b_paper.py` | Deterministic end-to-end | Compatibility Paper lifecycle, leverage and TP1 idempotency | Deterministic replay alone is not operational Paper validation |
| `test_version_b_order_manager.py` | Integration | Protection failure and missing confirmation capability are both critical and prevent unsafe acceptance | Full exchange reconciliation |
| `test_version_b_operational_acceptance.py` | Integration / operational | Event-driven Paper runtime: bar-by-bar operation, crash and restart mid-lifecycle with continuation, duplicate events, deterministic venue degradation (UNKNOWN, partial fill, unconfirmed protection), stale/gapped/out-of-order data, single-instance guard, health and audit, parity against the batch replay oracle | Historical performance, or any real-venue acknowledgement |
| `test_version_b_paper_driver.py` | Operational end-to-end / architecture | **Liveness is separate from data freshness:** the beat is due on elapsed time and continues with no market data arriving, a quiet source is waited out rather than ending the run, staleness is detected by the clock with no event involved, a dead process still lets the lease expire into an audited takeover, and two live processes can never own one run. | The Paper **operating layer**: `MarketDataAdapter` → `Clock` → `PaperDriver` → `VersionBPaperRuntime`. Drives start→clock→market event→decision→risk→intent→fill→lifecycle→DB→restart→resume through the driver (never by calling runtime internals); proves the adapter boundary is swappable, the clock is injected and monotonic, whole-window ingestion is refused, resume starts after the last persisted event, heartbeat/lease is live and an expired-lease takeover is audited, and parity with the batch replay oracle is unchanged | Historical performance; any real network market-data feed; multi-symbol operation; real-venue acknowledgement |
| `test_version_b_integration_acceptance.py` | Integration | One decision driven through StrategyCore → RiskEngine → ExecutionService → TradeLifecycle → VersionBStore; restart during an open lifecycle rebuilt from rows alone; legacy fallback closure | Historical performance, or any real-exchange acknowledgement |
| `test_legacy_paper_exchange.py` | Legacy component | The pre-Version-B in-memory exchange. **Not Version B evidence.** Retained so its behaviour stays covered while it exists for the Live path | Version B readiness of any kind; restart safety |
| `test_version_b_baseline_readiness.py` | Pure unit / integration | Artifact contract enforcement, reproducible lineage, pinned equity-curve drawdown, legacy-report separation, per-symbol aggregation guard, artifact fingerprint reproducibility | Historical performance, statistical validity, or any operational result |
| `test_version_b_external_execution.py` | Integration / recovery | Write-ahead intent identity, retry without duplicate order/fill, lost response, `UNKNOWN` never reported as success, partial fill, rejection, fetch/ack protection, terminal-state guard, restart hydration and idempotent reconciliation | Any real venue behavior: `clientOrderId` deduplication, ack/visibility semantics, live-account restart |

## Test doubles and fixtures

- `FixtureStrategy` is a deterministic strategy **double** used only to make a
  known entry available for replay/lifecycle assertions. It proves adapter
  wiring and does not prove the real Strategy rules, indicator parity, or
  signal quality. Real StrategyCore parity is tested separately against the
  frozen Version A fixture.
- `FixtureMarketData` is an in-memory finite-frame **double** used by the
  explicit `TradingBot(version_b=True)` test. It proves the call order and
  network-free wiring and does not prove exchange/vendor freshness, REST
  retries, or live data correctness.
- OHLCV frames in replay tests are synthetic deterministic fixtures. They prove
  no-lookahead/clock/lifecycle mechanics and do not constitute Historical
  Baseline data.
- `DeterministicExchangeDouble` implements `ExternalOrderAdapter` in memory and
  injects response loss after execution, submit timeouts, query failures,
  `NOT_FOUND`, `UNKNOWN` replies, explicit rejection, partial fills, and
  client-order-id deduplication. It proves the **local** invariants of the
  external execution and restart recovery contract — no duplicate
  order/fill/trade, and no fabricated exchange state. It does **not** prove that
  any real venue behaves this way; that evidence is tracked separately in
  `VERSION_B_EXTERNAL_EXECUTION.md` §6 and `VERSION_B_LIVE_BLOCKERS.md`.

- `DeterministicMarketAdapter` is the **only** shipped `MarketDataAdapter`
  implementation. `LiveMarketAdapter` is a boundary that raises
  `NotImplementedError` on construction: no network market-data source is
  authorised in this phase. Paper therefore runs on local deterministic frames
  delivered in a runtime-realistic way — it is a genuinely runnable runtime, but
  its *source* is still a double, and swapping in a real feed remains unproven.
- **Known limit — single symbol.** `VersionBPaperRuntime.symbol` is one string,
  so one runtime instance trades one symbol; events for any other symbol return
  `IGNORED`. Widening this would change the identity of the single-instance
  lock, the recovery report and the parity oracle, so it is out of scope and
  recorded as a limit (`VB-DRV-011`) rather than silently widened.
- The `main.py --version-b-paper` entry point runs the real `TradingStrategy`,
  which requires 200 warm-up bars on 1h/15m. On a short frame set it correctly
  rejects every decision as `INSUFFICIENT_WARMUP` and opens nothing; that is the
  data-quality gate working, not a driver fault.

- **Liveness is not data freshness.** `PaperDriver.beat()` is due on elapsed
  time, so a live process waiting on a quiet feed keeps its lease; a stopped
  feed produces `STALE_DATA` and an entry-blocking breaker instead of making a
  live process look dead. `MarketDataAdapter.idle_until()` is the optional hook
  a socket-backed feed would use to declare that it is waiting; the default
  `None` preserves the deterministic adapter exactly.
- **Known limit (`VB-LIV-005`).** `on_event` closes a data-fault breaker on any
  cleanly processed event, so a breaker opened by an outage is cleared by the
  first bar that ends it. Stale-data entry blocking is therefore proven via
  `_process_decision`'s staleness check and via the breaker when it is open at
  decision time — not via the outage path alone.
