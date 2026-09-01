"""Version B candle closure, alignment, freshness, gap, and warm-up rules."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from enum import Enum
from typing import Any, Mapping

import pandas as pd


TIMEFRAME_MINUTES: dict[str, int] = {
    "1m": 1,
    "5m": 5,
    "15m": 15,
    "30m": 30,
    "1h": 60,
    "2h": 120,
    "4h": 240,
    "6h": 360,
    "8h": 480,
    "12h": 720,
    "1d": 1440,
    "1w": 10080,
}


class DataStatus(str, Enum):
    VALID = "VALID"
    EMPTY = "EMPTY"
    INVALID_SCHEMA = "INVALID_SCHEMA"
    INSUFFICIENT_WARMUP = "INSUFFICIENT_WARMUP"
    STALE = "STALE"
    DATA_GAP = "DATA_GAP"


def timeframe_delta(timeframe: str) -> pd.Timedelta:
    try:
        return pd.Timedelta(minutes=TIMEFRAME_MINUTES[timeframe])
    except KeyError as exc:
        raise ValueError(f"unsupported timeframe: {timeframe}") from exc


def as_utc_timestamp(value: Any) -> pd.Timestamp:
    """Normalize exchange/local timestamps to UTC-aware pandas timestamps."""
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        return stamp.tz_localize("UTC")
    return stamp.tz_convert("UTC")


def normalize_ohlcv_index(df: pd.DataFrame) -> pd.DataFrame:
    """Return a sorted, deduplicated copy with a UTC-aware DatetimeIndex."""
    if df is None:
        return pd.DataFrame()
    # Replay callers normalize once and pass the same immutable frame through
    # many decision timestamps. Avoid an O(n) copy on every alignment while
    # retaining a copy for schema/index conversions.
    if (
        isinstance(df.index, pd.DatetimeIndex)
        and df.index.tz is not None
        and str(df.index.tz) == "UTC"
        and df.index.is_monotonic_increasing
        and df.index.is_unique
        and "timestamp" not in df.columns
    ):
        return df
    result = df.copy()
    if not isinstance(result.index, pd.DatetimeIndex):
        if "timestamp" not in result.columns:
            raise ValueError("OHLCV frame must have a DatetimeIndex or timestamp column")
        result = result.set_index("timestamp")
    result.index = pd.DatetimeIndex([as_utc_timestamp(item) for item in result.index])
    result = result[~result.index.duplicated(keep="last")].sort_index()
    return result


def candle_close_time(open_time: Any, timeframe: str) -> pd.Timestamp:
    return as_utc_timestamp(open_time) + timeframe_delta(timeframe)


def legacy_closed_view(frame: pd.DataFrame, decision_time: Any) -> pd.DataFrame:
    """Adapt an already-closed frame to legacy ``iloc[-2]`` consumers.

    The existing Strategy implementation deliberately reads ``iloc[-2]`` to
    skip an open candle.  Version B supplies only closed rows, so this helper
    appends a synthetic, non-market row duplicating the latest closed row.  It
    preserves the existing decision formulas while making the selected row
    explicit.  The synthetic row is never persisted as market data.
    """
    normalized = normalize_ohlcv_index(frame)
    if normalized.empty:
        return normalized
    result = normalized.copy()
    synthetic_time = as_utc_timestamp(decision_time)
    if synthetic_time <= result.index[-1]:
        synthetic_time = result.index[-1] + pd.Timedelta(nanoseconds=1)
    result.loc[synthetic_time] = result.iloc[-1]
    return result.sort_index()


def closed_rows(
    df: pd.DataFrame,
    timeframe: str,
    decision_time: Any,
) -> pd.DataFrame:
    """Select only candles whose close time is at or before decision_time."""
    normalized = normalize_ohlcv_index(df)
    decision = as_utc_timestamp(decision_time)
    delta = timeframe_delta(timeframe)
    mask = (normalized.index + delta) <= decision
    return normalized.loc[mask].copy()


@dataclass(frozen=True)
class FrameQuality:
    timeframe: str
    status: DataStatus
    required_rows: int
    available_rows: int
    latest_open: pd.Timestamp | None
    latest_close: pd.Timestamp | None
    gap_count: int = 0
    stale_by_seconds: float = 0.0


@dataclass(frozen=True)
class AlignedFrames:
    decision_time: pd.Timestamp
    frames: Mapping[str, pd.DataFrame]
    quality: Mapping[str, FrameQuality]

    @property
    def valid(self) -> bool:
        return all(item.status == DataStatus.VALID for item in self.quality.values())


def _required_rows_for_frame(timeframe: str, warmup: Mapping[str, int] | None) -> int:
    if warmup is None:
        return 0
    return int(warmup.get(timeframe, 0))


def _count_gaps(frame: pd.DataFrame, timeframe: str) -> int:
    if len(frame.index) < 2:
        return 0
    expected = timeframe_delta(timeframe)
    differences = frame.index.to_series().diff().dropna()
    return int((differences != expected).sum())


def assess_frame(
    frame: pd.DataFrame,
    timeframe: str,
    decision_time: Any,
    *,
    required_rows: int = 0,
) -> tuple[pd.DataFrame, FrameQuality]:
    """Close, validate, and classify one timeframe without evaluating strategy."""
    if frame is None or frame.empty:
        quality = FrameQuality(timeframe, DataStatus.EMPTY, required_rows, 0, None, None)
        return pd.DataFrame(), quality

    try:
        normalized = normalize_ohlcv_index(frame)
    except (TypeError, ValueError, KeyError):
        quality = FrameQuality(timeframe, DataStatus.INVALID_SCHEMA, required_rows, 0, None, None)
        return pd.DataFrame(), quality

    required_columns = {"open", "high", "low", "close", "volume"}
    if not required_columns.issubset(normalized.columns):
        quality = FrameQuality(timeframe, DataStatus.INVALID_SCHEMA, required_rows, 0, None, None)
        return pd.DataFrame(), quality

    closed = closed_rows(normalized, timeframe, decision_time)
    latest_open = closed.index[-1] if not closed.empty else None
    latest_close = candle_close_time(latest_open, timeframe) if latest_open is not None else None
    available = len(closed)
    gaps = _count_gaps(closed, timeframe)

    if closed.empty:
        status = DataStatus.EMPTY
        stale_by = 0.0
    else:
        age = as_utc_timestamp(decision_time) - latest_close
        stale_by = max(0.0, age.total_seconds())
        # A timeframe may legitimately close before T (e.g. 1h at a 15m
        # decision boundary). More than one full timeframe behind is stale.
        if age > timeframe_delta(timeframe):
            status = DataStatus.STALE
        elif gaps:
            status = DataStatus.DATA_GAP
        elif available < required_rows:
            status = DataStatus.INSUFFICIENT_WARMUP
        else:
            status = DataStatus.VALID

    quality = FrameQuality(
        timeframe=timeframe,
        status=status,
        required_rows=required_rows,
        available_rows=available,
        latest_open=latest_open,
        latest_close=latest_close,
        gap_count=gaps,
        stale_by_seconds=stale_by,
    )
    return closed, quality


def derive_warmup_requirements(config: Any) -> dict[str, int]:
    """Derive minimum valid rows from active consumers, not a generic 21-bar gate.

    EMA200 is the longest active entry/trend consumer on 1h and 15m.  The 5m
    frame remains non-decisional and retains the current 21-row availability
    requirement.  This does not change an entry rule; it prevents decisions
    from unstable indicator history.
    """
    if hasattr(config, "get"):
        get = config.get
        ema_slow = int(get("strategy", "indicators", "ema_slow", default=200))
        adx_period = int(get("strategy", "trend", "adx_period", default=14))
        atr_period = int(get("strategy", "volatility", "atr_period", default=14))
        volume_period = int(get("strategy", "volume", "volume_ma_period", default=20))
    else:
        strategy = config.get("strategy", {})
        ema_slow = int(strategy.get("indicators", {}).get("ema_slow", 200))
        adx_period = int(strategy.get("trend", {}).get("adx_period", 14))
        atr_period = int(strategy.get("volatility", {}).get("atr_period", 14))
        volume_period = int(strategy.get("volume", {}).get("volume_ma_period", 20))

    stable_longest = max(ema_slow, 2 * adx_period, 2 * atr_period, volume_period, 21)
    return {"1h": stable_longest, "15m": stable_longest, "5m": 21}


def align_timeframes(
    frames: Mapping[str, pd.DataFrame],
    *,
    decision_time: Any | None = None,
    decision_timeframe: str = "15m",
    warmup: Mapping[str, int] | None = None,
) -> AlignedFrames:
    """Align frames to one decision timestamp and return quality classifications.

    If decision_time is omitted, it is derived from the latest close of the
    decision timeframe. Production/replay callers should pass it explicitly.
    """
    if decision_time is None:
        source = frames.get(decision_timeframe)
        if source is None or len(source) == 0:
            now = pd.Timestamp.now(tz="UTC")
            decision = now
        else:
            normalized = normalize_ohlcv_index(source)
            decision = candle_close_time(normalized.index[-1], decision_timeframe)
    else:
        decision = as_utc_timestamp(decision_time)

    aligned: dict[str, pd.DataFrame] = {}
    quality: dict[str, FrameQuality] = {}
    for timeframe, frame in frames.items():
        closed, item = assess_frame(
            frame,
            timeframe,
            decision,
            required_rows=_required_rows_for_frame(timeframe, warmup),
        )
        aligned[timeframe] = closed
        quality[timeframe] = item

    return AlignedFrames(decision, aligned, quality)
