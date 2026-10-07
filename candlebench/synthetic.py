"""A synthetic bar source: random-walk sessions that need no network or key.

Two jobs. It lets anyone run the whole pipeline — fetch, run, dashboard — from
a fresh clone in seconds, which neither real source allows: Yahoo serves weeks
of intraday history and Alpaca needs credentials. And it is a negative control
for the method itself. Prices here are a driftless random walk, so no
candlestick pattern can predict anything; a run over them that reported an
EDGE would be a false positive of the statistics, not a discovery.

The walk is not white noise. Intraday volatility and volume follow the U shape
real sessions show, because the cost estimator and the time-of-day breakdowns
read both, and a flat profile would exercise neither.

Every bar is a pure function of (symbol, date), so the same request always
returns the same bars, whenever and in whatever order it is made. Coarser
intervals are resampled from the 1m bars rather than drawn separately, so a
5m bar is exactly the five 1m bars inside it.
"""

from __future__ import annotations

import zlib
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

INTERVALS = ("1m", "5m", "15m", "30m", "1h")
DEFAULT_LOOKBACK_DAYS = 120
SYMBOLS = ("SYNA", "SYNB", "SYNC", "SYND", "SYNE", "SYNF", "SYNG", "SYNH")

MARKET_TZ = "America/New_York"
SESSION_MINUTES = 390
EPOCH = date(2020, 1, 1)
DAILY_VOL = 0.015
RULE = {"1m": "1min", "5m": "5min", "15m": "15min", "30m": "30min", "1h": "60min"}


def _seed(*parts: object) -> int:
    """A stable seed. Python's `hash` of a string changes between processes."""
    return zlib.crc32(":".join(map(str, parts)).encode())


def _u_shape() -> np.ndarray:
    """Relative intraday activity: high at the open, low midday, rising into the close."""
    t = np.linspace(0.0, 1.0, SESSION_MINUTES)
    return 0.7 + 1.6 * np.exp(-t / 0.08) + 0.6 * np.exp((t - 1.0) / 0.1)


def _day_open(symbol: str, day: date) -> float:
    """The session's opening price, from a per-symbol daily random walk since EPOCH."""
    rng = np.random.default_rng(_seed(symbol, "daily"))
    base = float(rng.uniform(20.0, 300.0))
    days = (day - EPOCH).days + 1
    walk = rng.normal(0.0, DAILY_VOL, size=max(days, 1)).cumsum()
    return base * float(np.exp(walk[-1]))


def session(symbol: str, day: date) -> pd.DataFrame:
    """One regular session of 1m bars for `symbol` on `day`, indexed in UTC."""
    rng = np.random.default_rng(_seed(symbol, day.isoformat()))
    shape = _u_shape()
    sigma = DAILY_VOL / np.sqrt(SESSION_MINUTES) * shape / shape.mean()

    closes = _day_open(symbol, day) * np.exp(np.cumsum(rng.normal(0.0, sigma)))
    # Each bar opens a small random step from the previous close, as a real
    # tape does between prints. Without it no bar could gap, and the kicker,
    # star and piercing patterns, which require gaps, would never fire.
    opens = np.concatenate(([_day_open(symbol, day)], closes[:-1]))
    opens = opens * np.exp(rng.normal(0.0, sigma * 0.3))
    wick = np.abs(rng.normal(0.0, sigma * 0.6, size=(2, SESSION_MINUTES)))
    highs = np.maximum(opens, closes) * np.exp(wick[0])
    lows = np.minimum(opens, closes) * np.exp(-wick[1])

    # Cents, as a real tape prints. Rounding can push a body past its wick, so
    # the wicks are re-extended to contain it.
    opens, closes = np.round(opens, 2), np.round(closes, 2)
    highs = np.maximum(np.round(highs, 2), np.maximum(opens, closes))
    lows = np.minimum(np.round(lows, 2), np.minimum(opens, closes))
    volume = np.round(rng.lognormal(8.0, 0.5, SESSION_MINUTES) * shape).astype(float)

    start = pd.Timestamp(datetime(day.year, day.month, day.day, 9, 30), tz=MARKET_TZ)
    index = pd.date_range(start, periods=SESSION_MINUTES, freq="1min").tz_convert("UTC")
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volume},
        index=index,
    )


def _resample(bars: pd.DataFrame, interval: str) -> pd.DataFrame:
    if interval == "1m":
        return bars
    local = bars.tz_convert(MARKET_TZ)
    out = local.resample(RULE[interval], origin="start", label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    )
    return out.dropna().tz_convert("UTC")


def download(tickers, interval: str, start: datetime, end: datetime) -> pd.DataFrame:
    """Bars for every weekday in [start, end), shaped like a multi-ticker yfinance frame."""
    if interval not in INTERVALS:
        raise ValueError(f"the synthetic source serves {', '.join(INTERVALS)}, not {interval!r}")
    first, last = start.date(), end.date()
    days = [first + timedelta(n) for n in range((last - first).days + 1)]
    days = [d for d in days if d.weekday() < 5]
    lower, upper = pd.Timestamp(start), pd.Timestamp(end)

    frames = {}
    for symbol in tickers:
        if not days:
            continue
        bars = pd.concat([_resample(session(symbol, d), interval) for d in days])
        frames[symbol] = bars[(bars.index >= lower) & (bars.index < upper)]
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, axis=1)
