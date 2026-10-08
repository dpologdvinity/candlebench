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

Optionally (`clustered=True`), volatility also clusters, as it does in real
markets: calm and turbulent days come in runs, partly shared across symbols,
and within a session a volatile minute tends to be followed by another. The
statistics resample whole market dates as if they were independent; clustered
volatility makes them not quite so, which is what makes it worth testing. It is
off by default, so the demo and every published synthetic measurement are
unchanged.

Every bar is a pure function of (symbol, date), so the same request always
returns the same bars, whenever and in whatever order it is made. Coarser
intervals are resampled from the 1m bars rather than drawn separately, so a
5m bar is exactly the five 1m bars inside it.
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass
from functools import lru_cache
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


# Volatility clustering, as log-volatility AR(1) processes. Daily levels persist
# for weeks (a half-life of about 23 calendar days) and mix a market-wide factor with
# the symbol's own; within a session, a shock to volatility halves in about 7
# minutes. Each multiplier has mean one, so average volatility is unchanged.
VOL_DAILY_PERSISTENCE = 0.97
VOL_DAILY_DISPERSION = 0.35
VOL_MARKET_SHARE = 0.36  # share of daily log-volatility variance that is market-wide
VOL_MINUTE_PERSISTENCE = 0.9
VOL_MINUTE_DISPERSION = 0.3
VOL_HORIZON = date(2040, 1, 1)


def _ar1(rng: np.random.Generator, n: int, phi: float, dispersion: float) -> np.ndarray:
    """A stationary AR(1) series with standard deviation `dispersion`."""
    shocks = rng.normal(0.0, dispersion * np.sqrt(1.0 - phi * phi), n)
    out = np.empty(n)
    level = rng.normal(0.0, dispersion)
    for i in range(n):
        level = phi * level + shocks[i]
        out[i] = level
    return out


@lru_cache(maxsize=None)
def _daily_log_vol(name: str) -> np.ndarray:
    """One log-volatility level per calendar day since EPOCH, for a symbol or the market."""
    rng = np.random.default_rng(_seed(name, "volatility"))
    return _ar1(rng, (VOL_HORIZON - EPOCH).days, VOL_DAILY_PERSISTENCE, VOL_DAILY_DISPERSION)


def _vol_multiplier(symbol: str, day: date) -> np.ndarray:
    """Per-minute volatility multipliers for one session, with mean one."""
    if not EPOCH <= day < VOL_HORIZON:
        # A negative index would silently wrap to the series' far end.
        raise ValueError(f"clustered sessions span {EPOCH} to {VOL_HORIZON}, not {day}")
    index = (day - EPOCH).days
    market = np.sqrt(VOL_MARKET_SHARE)
    daily = (market * _daily_log_vol("market")[index]
             + np.sqrt(1.0 - VOL_MARKET_SHARE) * _daily_log_vol(symbol)[index])
    rng = np.random.default_rng(_seed(symbol, day.isoformat(), "volatility"))
    minute = _ar1(rng, SESSION_MINUTES, VOL_MINUTE_PERSISTENCE, VOL_MINUTE_DISPERSION)
    return np.exp(daily - VOL_DAILY_DISPERSION ** 2 / 2 + minute - VOL_MINUTE_DISPERSION ** 2 / 2)


def _day_open(symbol: str, day: date) -> float:
    """The session's opening price, from a per-symbol daily random walk since EPOCH."""
    rng = np.random.default_rng(_seed(symbol, "daily"))
    base = float(rng.uniform(20.0, 300.0))
    days = (day - EPOCH).days + 1
    walk = rng.normal(0.0, DAILY_VOL, size=max(days, 1)).cumsum()
    return base * float(np.exp(walk[-1]))


@dataclass(frozen=True)
class Planted:
    """A known edge planted after every signal of one pattern, for measuring power.

    After each signal at bar i, the log returns of bars i+1 .. i+`bars` gain
    `drift` times that bar's volatility, in the pattern's direction. A trade
    entered at bar i+1's open therefore expects to gain from the drift, and the
    statistics have a real effect of known construction to find.
    """

    pattern: str
    drift: float
    bars: int = 5


# The trend window a 1m session uses at the default configuration
# (`runner.trend_lookback_for_length` leaves 10 bars unchanged at 390 bars).
PLANT_TREND_LOOKBACK = 10


def session(symbol: str, day: date, planted: Planted | None = None,
            clustered: bool = False) -> pd.DataFrame:
    """One regular session of 1m bars for `symbol` on `day`, indexed in UTC.

    Every random draw is taken first and in a fixed order, so a planted edge
    changes only the returns it is added to, and an unplanted session is
    unchanged by the option existing. Clustering draws from its own generator
    and only rescales those draws, so the same holds for it.
    """
    build, sigma = _parts(symbol, day, clustered)
    no_drift = np.zeros(SESSION_MINUTES)
    bars = build(no_drift if planted is None else _planted_drift(build, sigma, planted))

    start = pd.Timestamp(datetime(day.year, day.month, day.day, 9, 30), tz=MARKET_TZ)
    index = pd.date_range(start, periods=SESSION_MINUTES, freq="1min").tz_convert("UTC")
    return pd.DataFrame(bars, index=index)


def _parts(symbol: str, day: date, clustered: bool = False):
    """A session's random draws, as a function from per-bar drift to OHLCV arrays.

    Separate from `session` so the planted drift can be solved for, and so a
    test can rebuild a session bar by bar through the identical construction.
    """
    rng = np.random.default_rng(_seed(symbol, day.isoformat()))
    shape = _u_shape()
    sigma = DAILY_VOL / np.sqrt(SESSION_MINUTES) * shape / shape.mean()
    returns = rng.normal(0.0, sigma)
    # Each bar opens a small random step from the previous close, as a real
    # tape does between prints. Without it no bar could gap, and the kicker,
    # star and piercing patterns, which require gaps, would never fire.
    gaps = rng.normal(0.0, sigma * 0.3)
    wick = np.abs(rng.normal(0.0, sigma * 0.6, size=(2, SESSION_MINUTES)))
    volume = np.round(rng.lognormal(8.0, 0.5, SESSION_MINUTES) * shape).astype(float)
    day_open = _day_open(symbol, day)
    if clustered:
        scale = _vol_multiplier(symbol, day)
        sigma, returns, gaps, wick = sigma * scale, returns * scale, gaps * scale, wick * scale

    def build(drift: np.ndarray) -> dict[str, np.ndarray]:
        closes = day_open * np.exp(np.cumsum(returns + drift))
        opens = np.concatenate(([day_open], closes[:-1])) * np.exp(gaps)
        highs = np.maximum(opens, closes) * np.exp(wick[0])
        lows = np.minimum(opens, closes) * np.exp(-wick[1])
        # Cents, as a real tape prints. Rounding can push a body past its wick,
        # so the wicks are re-extended to contain it.
        opens, closes = np.round(opens, 2), np.round(closes, 2)
        highs = np.maximum(np.round(highs, 2), np.maximum(opens, closes))
        lows = np.minimum(np.round(lows, 2), np.minimum(opens, closes))
        return {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volume}

    return build, sigma


def _planted_drift(build, sigma: np.ndarray, planted: Planted) -> np.ndarray:
    """The drift that puts the planted edge after every signal, causally.

    A drift added after one signal changes later bars, and so can create or
    remove later signals; the right answer is the one a bar-by-bar generator
    would produce. It is found by iterating: detect signals on the current
    path, rebuild the drift from them, repeat until the signals stop changing.
    Detection is causal, so the bars up to the first signal the iterate gets
    wrong are already right, and that signal is right after the next pass: the
    first disagreement moves strictly later each time, which bounds the passes
    by the session length and makes the result exactly the sequential one.
    """
    from candlebench import patterns
    from candlebench.config import Thresholds
    from candlebench.patterns import context

    spec = patterns.get(planted.pattern)
    thresholds = Thresholds()
    drift = np.zeros(SESSION_MINUTES)
    for _ in range(SESSION_MINUTES + 1):
        geom = context.geometry(build(drift), PLANT_TREND_LOOKBACK, thresholds.trend_min_slope)
        signals = np.flatnonzero(patterns.detect(spec, geom, thresholds))
        updated = np.zeros(SESSION_MINUTES)
        for i in signals:
            after = slice(i + 1, min(SESSION_MINUTES, i + 1 + planted.bars))
            updated[after] += spec.direction * planted.drift * sigma[after]
        if np.array_equal(updated, drift):
            return drift
        drift = updated
    raise RuntimeError("planted drift did not converge")  # unreachable; see the docstring


def _resample(bars: pd.DataFrame, interval: str) -> pd.DataFrame:
    if interval == "1m":
        return bars
    local = bars.tz_convert(MARKET_TZ)
    out = local.resample(RULE[interval], origin="start", label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    )
    return out.dropna().tz_convert("UTC")


def downloader(planted: Planted | None = None, clustered: bool = False):
    """A download function over this market, optionally with a planted edge."""

    def fetch(tickers, interval: str, start: datetime, end: datetime) -> pd.DataFrame:
        return download(tickers, interval, start, end, planted, clustered)

    return fetch


def download(tickers, interval: str, start: datetime, end: datetime,
             planted: Planted | None = None, clustered: bool = False) -> pd.DataFrame:
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
        bars = pd.concat([_resample(session(symbol, d, planted, clustered), interval)
                          for d in days])
        frames[symbol] = bars[(bars.index >= lower) & (bars.index < upper)]
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, axis=1)
