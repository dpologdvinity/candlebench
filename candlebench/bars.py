"""Bar acquisition, validation, caching, and session grouping.

This is the only module that knows where bars come from. Everything downstream
sees plain numpy arrays, so swapping in a tick provider later touches nothing
else.

yfinance caps intraday lookback, and the caps are hard: there is no interval
below 1m, and intraday history does not reach past 60 days. Those limits are
encoded here rather than documented elsewhere, so a request for an impossible
window fails in one place with one message.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

SUPPORTED_INTERVALS = ("1m", "2m", "5m", "15m", "30m", "1h")

INTERVAL_MAX_LOOKBACK_DAYS = {
    "1m": 30,
    "2m": 60,
    "5m": 60,
    "15m": 60,
    "30m": 60,
    "1h": 60,
}

# Yahoo refuses a 1m request spanning more than 7 days, so it is fetched in
# chunks. Any interval absent here is fetched in a single request.
INTERVAL_CHUNK_DAYS = {"1m": 7}

BAR_COLUMNS = ("open", "high", "low", "close", "volume")

MARKET_TZ = "America/New_York"
SESSION_OPEN = (9, 30)
SESSION_CLOSE = (16, 0)

# Interval in minutes, used to decide how many bars a session should hold.
INTERVAL_MINUTES = {"1m": 1, "2m": 2, "5m": 5, "15m": 15, "30m": 30, "1h": 60}


@dataclass
class CacheReport:
    """Outcome of a cache warm-up. Failures are collected, not raised."""

    written: dict[tuple[str, str], int] = field(default_factory=dict)
    failures: dict[tuple[str, str], str] = field(default_factory=dict)
    dropped_rows: int = 0

    def summary(self) -> str:
        total = sum(self.written.values())
        lines = [
            f"cached {total:,} bars across {len(self.written)} symbol/interval pairs",
        ]
        if self.dropped_rows:
            lines.append(f"dropped {self.dropped_rows:,} malformed or empty-volume bars")
        if self.failures:
            lines.append(f"{len(self.failures)} fetch failure(s):")
            lines += [f"  {sym} {iv}: {msg}" for (sym, iv), msg in sorted(self.failures.items())]
        return "\n".join(lines)


def cache_file(cache_dir: Path, symbol: str, interval: str) -> Path:
    return Path(cache_dir) / interval / f"{symbol}.parquet"


def _default_download(tickers: list[str], interval: str, start: datetime, end: datetime):
    import yfinance as yf

    return yf.download(
        tickers=tickers,
        interval=interval,
        start=start,
        end=end,
        auto_adjust=False,
        prepost=False,
        actions=False,
        progress=False,
        threads=True,
        group_by="ticker",
    )


def _extract(frame: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Pull one symbol's OHLCV out of a yfinance result.

    yfinance returns flat columns for a single ticker and a (ticker, field)
    MultiIndex for several, so both shapes are handled.
    """
    if frame is None or frame.empty:
        return pd.DataFrame(columns=list(BAR_COLUMNS))

    if isinstance(frame.columns, pd.MultiIndex):
        if symbol not in frame.columns.get_level_values(0):
            return pd.DataFrame(columns=list(BAR_COLUMNS))
        sub = frame[symbol]
    else:
        sub = frame

    sub = sub.rename(columns=str.lower)
    missing = [c for c in BAR_COLUMNS if c not in sub.columns]
    if missing:
        return pd.DataFrame(columns=list(BAR_COLUMNS))
    return sub[list(BAR_COLUMNS)]


def validate(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Drop bars that cannot be real, returning the count removed.

    Yahoo intraday data contains occasional bad bars. A bar whose high is below
    its body, or whose volume is zero, would fabricate pattern geometry that
    never traded, so it is removed rather than repaired.
    """
    if df.empty:
        return df, 0

    before = len(df)
    clean = df.dropna(subset=list(BAR_COLUMNS))
    clean = clean[clean["volume"] > 0]
    body_high = clean[["open", "close"]].max(axis=1)
    body_low = clean[["open", "close"]].min(axis=1)
    clean = clean[(clean["high"] >= body_high) & (clean["low"] <= body_low)]
    clean = clean[clean["high"] >= clean["low"]]
    clean = clean[~clean.index.duplicated(keep="first")]
    return clean.sort_index(), before - len(clean)


def _utc_index(df: pd.DataFrame) -> pd.DataFrame:
    """Normalise the index to tz-aware UTC so the cache format is unambiguous."""
    idx = pd.DatetimeIndex(df.index)
    df = df.copy()
    df.index = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
    df.index.name = "timestamp"
    return df


def _windows(interval: str, now: datetime) -> list[tuple[datetime, datetime]]:
    """Fetch windows covering the maximum lookback yfinance permits."""
    span = INTERVAL_MAX_LOOKBACK_DAYS[interval]
    chunk = INTERVAL_CHUNK_DAYS.get(interval, span)
    out = []
    end = now
    remaining = span
    while remaining > 0:
        days = min(chunk, remaining)
        out.append((end - timedelta(days=days), end))
        end -= timedelta(days=days)
        remaining -= days
    return list(reversed(out))


def warm_cache(
    symbols: tuple[str, ...] | list[str],
    intervals: tuple[str, ...] | list[str],
    cache_dir: Path,
    throttle_s: float = 0.3,
    batch_size: int = 10,
    download=_default_download,
    now: datetime | None = None,
) -> CacheReport:
    """Download the maximum permitted window for each symbol and interval.

    Trials never touch the network, so that a run's duration does not depend on
    Yahoo's mood and two patterns in the same trial cannot see different data.
    """
    now = now or datetime.now(timezone.utc)
    report = CacheReport()
    cache_dir = Path(cache_dir)

    for interval in intervals:
        if interval not in SUPPORTED_INTERVALS:
            raise ValueError(f"unsupported interval {interval!r}")

        collected: dict[str, list[pd.DataFrame]] = {s: [] for s in symbols}
        for start, end in _windows(interval, now):
            for i in range(0, len(symbols), batch_size):
                batch = list(symbols[i : i + batch_size])
                try:
                    frame = download(batch, interval, start, end)
                except Exception as exc:  # one bad batch must not abort the warm-up
                    for symbol in batch:
                        report.failures[(symbol, interval)] = str(exc)
                    continue
                for symbol in batch:
                    piece = _extract(frame, symbol)
                    if not piece.empty:
                        collected[symbol].append(piece)
                if throttle_s:
                    time.sleep(throttle_s)

        out_dir = cache_dir / interval
        out_dir.mkdir(parents=True, exist_ok=True)
        for symbol, pieces in collected.items():
            if not pieces:
                report.failures.setdefault((symbol, interval), "no data returned")
                continue
            merged = _utc_index(pd.concat(pieces))
            merged, dropped = validate(merged)
            report.dropped_rows += dropped
            if merged.empty:
                report.failures[(symbol, interval)] = "all bars failed validation"
                continue
            merged.to_parquet(cache_file(cache_dir, symbol, interval))
            report.written[(symbol, interval)] = len(merged)

    return report


def load(symbol: str, interval: str, cache_dir: Path) -> pd.DataFrame:
    """Read cached bars. A cache miss is an error, not an implicit download."""
    path = cache_file(cache_dir, symbol, interval)
    if not path.exists():
        raise FileNotFoundError(
            f"no cached {interval} bars for {symbol} at {path}. "
            "run `python -m candlebench fetch` first."
        )
    df = pd.read_parquet(path)
    df, _ = validate(_utc_index(df))
    return df


def regular_hours(df: pd.DataFrame) -> pd.DataFrame:
    """Restrict to 09:30-16:00 Eastern.

    Day trading does not hold through an overnight gap, and extended-hours bars
    are thin enough that their geometry is mostly spread artefacts.
    """
    if df.empty:
        return df
    local = df.tz_convert(MARKET_TZ)
    minutes = local.index.hour * 60 + local.index.minute
    start = SESSION_OPEN[0] * 60 + SESSION_OPEN[1]
    close = SESSION_CLOSE[0] * 60 + SESSION_CLOSE[1]
    return local[(minutes >= start) & (minutes < close)]


def sessions(df: pd.DataFrame) -> dict[date, pd.DataFrame]:
    """Split bars into one frame per trading session, keyed by Eastern date."""
    hours = regular_hours(df)
    if hours.empty:
        return {}
    return {day: frame for day, frame in hours.groupby(hours.index.date)}


def to_arrays(df: pd.DataFrame) -> dict[str, np.ndarray]:
    """Float64 OHLCV arrays, the only shape the rest of the package sees."""
    return {name: df[name].to_numpy(dtype=np.float64) for name in BAR_COLUMNS}


def available_sessions(
    symbols: tuple[str, ...], interval: str, cache_dir: Path
) -> dict[str, list[date]]:
    """Sessions present in the cache per symbol, for the sampler to draw from."""
    out: dict[str, list[date]] = {}
    for symbol in symbols:
        try:
            days = sorted(sessions(load(symbol, interval, cache_dir)))
        except FileNotFoundError:
            continue
        if days:
            out[symbol] = days
    return out
