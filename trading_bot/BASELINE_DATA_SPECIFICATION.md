# Baseline Data Specification — Pinned

Status: **PINNED**. This specification is fixed before any data is downloaded.
It changes no strategy rule, no threshold, no risk parameter, and no frozen
Version B implementation. It authorizes no baseline run by itself;
`VERSION_B_BASELINE_DEFINITION.md` §6 and the acceptance gate still govern
acceptance.

Pinned against code state `a782819` (`vb-1.0-5m-stop-first`).

## 1. Source

- **Binance OHLCV via CCXT, historical data only.** No order endpoint, no
  account endpoint, no live execution surface is touched. LB-002/LB-003/LB-004
  remain open and are unaffected by this specification.
- Collection must paginate with `since` (the per-call 1500-candle cap is a
  fetch-tool property, not an engine property; `VersionBReplayEngine.run()`
  accepts frames of any length).

## 2. Universe and timeframes

- Symbols: the frozen Version A universe — `BTC/USDT`, `ETH/USDT`, `SOL/USDT`,
  `BNB/USDT`, `DOGE/USDT`.
- Timeframes: `1h`, `15m`, `5m` — exactly the frame keys hashed by
  `hash_frames({"1h", "15m", "5m"})`.
- Baseline artifacts remain **per symbol per direction**
  (`universe == [symbol]` is enforced by `validate_baseline_artifact`).

## 3. Measurement window (Type-1 result scope)

```
2025-01-01 00:00:00 UTC  →  2025-12-31 23:55:00 UTC
```

Only decisions, lifecycles, and metrics whose decision time falls inside this
window are Baseline results.

## 4. Warm-up (Option B — pinned)

Warm-up rows are prepended **before** the window so the first in-window
decision is fully valid. Counts come from the frozen
`derive_warmup_requirements` (`data/time_alignment.py`):
`max(ema_slow=200, 2×adx=28, 2×atr=28, volume_ma=20, 21) = 200` for `1h` and
`15m`; `21` for `5m`.

Exact-count warm-up bounds (verified against the frozen `closed_rows`
half-open boundary `(open + delta) <= decision_time`):

| Frame | Warm-up first open (UTC) | Warm-up rows | Last warm-up close |
|---|---|---|---|
| `1h` | `2024-12-23 16:00` | 200 | `2025-01-01 00:00` |
| `15m` | `2024-12-29 22:00` | 200 | `2025-01-01 00:00` |
| `5m` | `2024-12-31 22:15` | 21 | `2025-01-01 00:00` |

Snapshot totals per symbol: `1h` 8,960 bars · `15m` 35,240 bars · `5m`
105,141 bars (warm-up start → `2025-12-31 23:55`).

**Boundary semantics — enforced by the frozen gate, not by convention:**

- At the first in-window decision (`2025-01-01 00:00`) exactly 200/200/21
  closed rows exist → all frames `VALID`; the first possible entry fill is the
  15m bar opening `2025-01-01 00:00`.
- At any pre-window decision (e.g. `2024-12-31 23:45`) fewer than the required
  closed rows exist → `INSUFFICIENT_WARMUP` → `DATA_REJECTED`. **No trade can
  open before the measurement window**; this is mechanical, not procedural.
- Pre-window `DATA_REJECTED` decision records produced during warm-up bars are
  a property of the chosen window, not model outcomes, and must be excluded
  from Baseline statistics (which count in-window decisions only).
- Warm-up rows enter indicator history and initial state only. Because the
  frames consumed include warm-up, the lineage `data_hash` covers warm-up rows
  too — by design: the warm-up choice is part of the measurement identity.

## 5. Immutable snapshot and hashes

1. Raw data is downloaded once into an immutable on-disk snapshot **before**
   any Baseline run. Baseline runs read only the snapshot, never the network.
2. A snapshot manifest records, per symbol per timeframe: source
   (`binance`/`ccxt` + library version), request bounds, row count, first/last
   open, and the **SHA-256 of each raw dataset file** — provenance.
3. The binding measurement identity remains `hash_frames` computed by the
   engine over the frames actually consumed (`lineage.data.data_hash`);
   raw-file SHA-256 values are provenance in the snapshot manifest and do not
   replace it. Chain: raw-file sha256 (manifest) → frames → `hash_frames`
   (lineage).
4. The snapshot manifest also records this document's window and warm-up
   bounds verbatim, so the warm-up/measurement boundary is preserved in the
   data lineage chain.
5. Storage follows the repository's external-storage convention: raw snapshot
   data stays out of Git; the manifest (small JSON) is committed.

## 6. Explicitly out of scope for this specification

- No data download yet. No Baseline run. No artifact. `baseline_collected`
  stays `false`.
- No strategy/threshold/risk change. No frozen-code change.
- No Binance live execution wiring; Binance is a historical data source only.
