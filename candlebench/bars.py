"""Bar acquisition, validation, caching, and session grouping.

This is the only module that knows where bars come from. Everything downstream
sees plain numpy arrays, so swapping in a tick provider later touches nothing
else.

yfinance caps intraday lookback, and the caps are hard: there is no interval
below 1m, and intraday history does not reach past about 59 days. Those limits are
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

# Lookbacks deliberately sit inside Yahoo's documented limits rather than on
# them. Two reasons, both observed against the live API:
#
# 1. The server compares a request against *its* current time, while a warm-up
#    captures `now` once and then runs for a minute or more. A window built at
#    exactly the 60-day limit is over the limit by the time a later request
#    lands, and Yahoo rejects it with "the requested range must be within the
#    last 60 days".
# 2. The documented 30 days of 1m history is not served. A 7-day chunk ending
#    21 days ago returns bars; one ending 28 days ago returns nothing.
INTERVAL_MAX_LOOKBACK_DAYS = {
    "1m": 28,
    "2m": 59,
    "5m": 59,
    "15m": 59,
    "30m": 59,
    "1h": 59,
}

# Yahoo caps a 1m request at 8 days ("only 8 days worth of 1m granularity data
# are allowed to be fetched per request"), so it is fetched in 7-day chunks.
# Any interval absent here is fetched in a single request.
INTERVAL_CHUNK_DAYS = {"1m": 7}

BAR_COLUMNS = ("open", "high", "low", "close", "volume")

MARKET_TZ = "America/New_York"
SESSION_OPEN = (9, 30)
SESSION_CLOSE = (16, 0)

# Interval in minutes, used to decide how many bars a session should hold.
INTERVAL_MINUTES = {"1m": 1, "2m": 2, "5m": 5, "15m": 15, "30m": 30, "1h": 60}


def _alpaca_download(tickers, interval, start, end):
    # Imported lazily so that nothing requiring credentials loads unless the
    # Alpaca source is actually selected.
    from candlebench import alpaca

    return alpaca.download(tickers, interval, start, end)


@dataclass(frozen=True)
class Source:
    """One bar provider: how to fetch from it, and how far back it reaches.

    `cap` is the provider's own ceiling per interval, or None where the limit is
    years rather than days. Yahoo serves less than it is asked for without
    saying so, so the ceiling has to be enforced before the fetch rather than
    discovered after it.
    """

    download: object
    chunk_days: dict[str, int]
    cap: dict[str, int] | None
    default_lookback: object
    # How far short of the present a request must stop. Zero for providers with
    # no such restriction.
    end_lag: timedelta = timedelta(0)


SOURCES: dict[str, Source] = {}


def provider_cap(source: str, interval: str) -> int | None:
    """The most history `source` will serve at `interval`, in days."""
    caps = SOURCES[source].cap
    return None if caps is None else caps.get(interval)


def lookback_days(source: str, interval: str, configured: int) -> int:
    """How far back to fetch, resolving 0 to the source's own default."""
    if configured:
        return configured
    default = SOURCES[source].default_lookback
    return default(interval) if callable(default) else default


def _register_sources() -> None:
    from candlebench import alpaca

    SOURCES["yfinance"] = Source(
        download=_default_download,
        chunk_days=INTERVAL_CHUNK_DAYS,
        cap=INTERVAL_MAX_LOOKBACK_DAYS,
        default_lookback=lambda interval: INTERVAL_MAX_LOOKBACK_DAYS[interval],
    )
    SOURCES["alpaca"] = Source(
        download=_alpaca_download,
        # Pagination already walks a long window, but a bounded chunk keeps one
        # failed request from costing a year and keeps progress visible.
        chunk_days={interval: 30 for interval in alpaca.TIMEFRAMES},
        cap=None,  # years, not days; the ceiling is the 2016 history start
        default_lookback=alpaca.DEFAULT_LOOKBACK_DAYS,
        end_lag=alpaca.SIP_DELAY,
    )


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

    # A multi-symbol response is outer-joined on timestamp, so this symbol has an
    # all-NaN row wherever only *other* symbols traded. Those rows are not bars
    # and are dropped here rather than counted as malformed data downstream:
    # a fetch reporting tens of thousands of dropped malformed bars would make a
    # user distrust bars that are in fact fine.
    return sub[list(BAR_COLUMNS)].dropna(how="all")


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


def _windows(
    interval: str, now: datetime, source: str = "yfinance", configured: int = 0
) -> list[tuple[datetime, datetime]]:
    """Fetch windows covering the lookback this source will serve."""
    span = lookback_days(source, interval, configured)
    chunk = SOURCES[source].chunk_days.get(interval, span)
    out = []
    # Stop short of the present where the provider refuses recent data, and
    # extend the far end by the same amount so the requested span is still
    # covered rather than quietly shortened.
    lag = SOURCES[source].end_lag
    end = now - lag
    remaining = span
    while remaining > 0:
        days = min(chunk, remaining)
        out.append((end - timedelta(days=days), end))
        end -= timedelta(days=days)
        remaining -= days
    return list(reversed(out))


def _fetch_with_retry(
    download, batch: list[str], interval: str, start, end, retries: int, backoff_s: float
):
    """Fetch one batch, retrying on an error or an empty result.

    Yahoo signals throttling by returning an empty frame rather than by
    raising, which is indistinguishable at the call site from a window that
    genuinely holds no bars. Both are retried: a real empty window costs a few
    quick attempts, while a throttled one would otherwise be recorded as a
    permanent failure. Observed in practice when a 1m fetch's chunked requests
    are immediately followed by a request for another interval.
    """
    delay = backoff_s
    last_error: Exception | None = None

    for attempt in range(retries + 1):
        if attempt:
            time.sleep(delay)
            delay *= 2
        try:
            frame = download(batch, interval, start, end)
        except Exception as exc:
            last_error = exc
            continue
        if frame is not None and not frame.empty:
            return frame, None

    return None, last_error


def warm_cache(
    symbols: tuple[str, ...] | list[str],
    intervals: tuple[str, ...] | list[str],
    cache_dir: Path,
    throttle_s: float = 0.3,
    batch_size: int = 10,
    retries: int = 3,
    backoff_s: float = 1.0,
    download=None,
    now: datetime | None = None,
    source: str = "yfinance",
    lookback_days: int = 0,
) -> CacheReport:
    """Download the maximum permitted window for each symbol and interval.

    Trials never touch the network, so that a run's duration does not depend on
    Yahoo's mood and two patterns in the same trial cannot see different data.
    """
    report = CacheReport()
    cache_dir = Path(cache_dir)
    if source not in SOURCES:
        raise ValueError(f"unknown source {source!r}. valid: {', '.join(SOURCES)}")
    download = download or SOURCES[source].download
    # Aliased because the parameter shadows the module-level function of the
    # same name, which `_windows` still needs to resolve.
    span = lookback_days

    for interval in intervals:
        if interval not in SUPPORTED_INTERVALS:
            raise ValueError(f"unsupported interval {interval!r}")

        # Re-read the clock per interval. A full warm-up runs for minutes, and
        # windows measured from a single start-of-run timestamp drift past
        # Yahoo's lookback limit while the earlier intervals are downloading.
        # A caller may pin `now` to make a fetch reproducible in tests.
        reference = now or datetime.now(timezone.utc)

        collected: dict[str, list[pd.DataFrame]] = {s: [] for s in symbols}
        for start, end in _windows(interval, reference, source, span):
            for i in range(0, len(symbols), batch_size):
                batch = list(symbols[i : i + batch_size])
                frame, error = _fetch_with_retry(
                    download, batch, interval, start, end, retries, backoff_s
                )
                if frame is None:
                    # One bad batch must not abort the warm-up; a delisted or
                    # illiquid symbol should not cost the other forty-nine.
                    for symbol in batch:
                        report.failures[(symbol, interval)] = (
                            str(error) if error else "no data returned after retries"
                        )
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


def minutes_from_open(df: pd.DataFrame) -> np.ndarray:
    """Clock minutes since 09:30 Eastern for each bar of one session.

    A bar's position in the array is not its time of day. `validate` removes
    malformed bars, and Yahoo omits minutes with no trades, so index times
    interval drifts from the clock by however many bars went missing. Anything
    that groups trades by time of day needs the clock, not the index.
    """
    local = df.index if str(getattr(df.index, "tz", "")) == MARKET_TZ else (
        pd.DatetimeIndex(df.index).tz_convert(MARKET_TZ)
    )
    minutes = local.hour * 60 + local.minute - (SESSION_OPEN[0] * 60 + SESSION_OPEN[1])
    return np.asarray(minutes, dtype=np.int64)


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


# Registered at import, after the downloaders exist. `alpaca` imports nothing
# from this package, so there is no cycle.
_register_sources()
