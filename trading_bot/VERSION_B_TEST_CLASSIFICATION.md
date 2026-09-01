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
| `test_version_b_order_manager.py` | Integration | Protection failure is critical and prevents unsafe acceptance | Full exchange reconciliation |

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
