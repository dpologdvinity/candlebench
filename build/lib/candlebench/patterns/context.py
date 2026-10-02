"""Bar geometry and prior-trend context.

Every derived quantity a detector needs is computed once per bar array and
shared, so twenty detectors do not each recompute the same shadows.

Prior trend is a first-class input here. `hammer` and `hanging_man` are the same
geometry and differ only in the trend that precedes them; a library that omits
trend reports one shape twice under two names and calls them two results.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Geometry:
    """Per-bar measurements plus the prior-trend label for each bar."""

    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray

    rng: np.ndarray
    body: np.ndarray
    body_top: np.ndarray
    body_bottom: np.ndarray
    upper_shadow: np.ndarray
    lower_shadow: np.ndarray
    body_ratio: np.ndarray
    is_bull: np.ndarray
    is_bear: np.ndarray
    trend: np.ndarray
    trend_lookback: int

    def __len__(self) -> int:
        return len(self.close)


def _safe_ratio(numerator: np.ndarray, denominator: np.ndarray) -> np.ndarray:
    """Element-wise division that yields 0 where the denominator is 0.

    A one-minute bar on a quiet stock can have open == high == low == close, so
    a zero range is ordinary rather than exceptional. Such a bar must match no
    pattern, which a ratio of 0 achieves without a warning or a NaN.
    """
    out = np.zeros_like(numerator, dtype=np.float64)
    np.divide(numerator, denominator, out=out, where=denominator > 0)
    return out


def trend_series(close: np.ndarray, lookback: int, min_slope: float) -> np.ndarray:
    """Trend label per bar: -1 down, 0 flat, +1 up.

    The label is the sign of the least-squares slope of the closes over the
    `lookback` bars ending at that bar, normalised by their mean so the
    threshold is scale-free and comparable across a $5 stock and a $500 one.

    The regression weights are constant for a fixed window, so the whole series
    is one convolution rather than a Python loop.
    """
    n = len(close)
    out = np.zeros(n, dtype=np.int8)
    if lookback < 2 or n < lookback:
        return out

    k = np.arange(lookback, dtype=np.float64)
    weights = k - k.mean()
    denominator = np.sum(weights**2)

    # mode="valid" yields one value per complete window; index w-1 onward.
    slopes = np.convolve(close, weights[::-1], mode="valid") / denominator
    means = np.convolve(close, np.ones(lookback) / lookback, mode="valid")
    normalised = _safe_ratio(slopes, means)

    labels = np.zeros(len(normalised), dtype=np.int8)
    labels[normalised >= min_slope] = 1
    labels[normalised <= -min_slope] = -1
    if min_slope == 0:
        # With a zero threshold both comparisons hold at exactly flat; call it flat.
        labels[normalised == 0] = 0

    out[lookback - 1 :] = labels
    return out


def geometry(
    arrays: dict[str, np.ndarray], trend_lookback: int, trend_min_slope: float
) -> Geometry:
    """Build the shared geometry for one session's bars."""
    o = arrays["open"]
    h = arrays["high"]
    l = arrays["low"]
    c = arrays["close"]

    rng = h - l
    body = np.abs(c - o)
    body_top = np.maximum(o, c)
    body_bottom = np.minimum(o, c)

    return Geometry(
        open=o,
        high=h,
        low=l,
        close=c,
        volume=arrays["volume"],
        rng=rng,
        body=body,
        body_top=body_top,
        body_bottom=body_bottom,
        upper_shadow=h - body_top,
        lower_shadow=body_bottom - l,
        body_ratio=_safe_ratio(body, rng),
        is_bull=c > o,
        is_bear=c < o,
        trend=trend_series(c, trend_lookback, trend_min_slope),
        trend_lookback=trend_lookback,
    )


def lag(a: np.ndarray, k: int = 1) -> np.ndarray:
    """Shift so position i holds the value from bar i-k.

    Float arrays are padded with NaN and boolean arrays with False, so that any
    comparison involving a bar before the start of the data is false. That is
    what makes a detector's leading rows harmless even before the registry
    masks them off.
    """
    if k <= 0:
        raise ValueError("lag requires a positive shift")
    out = np.empty_like(a)
    if a.dtype == np.bool_:
        out[:k] = False
    elif np.issubdtype(a.dtype, np.integer):
        out[:k] = 0
    else:
        out[:k] = np.nan
    out[k:] = a[:-k]
    return out


def _window_stack(a: np.ndarray, window: int) -> np.ndarray:
    """Rows 0..window-1 holding a, lag(a,1), ... lag(a,window-1)."""
    return np.vstack([a] + [lag(a, k) for k in range(1, window)])


def rolling_min(a: np.ndarray, window: int) -> np.ndarray:
    """Minimum over the `window` bars ending at each position.

    Leading positions see a partial window rather than NaN, which is harmless:
    the registry masks off every bar without enough history before a detector's
    output is ever used.
    """
    return np.nanmin(_window_stack(a, window), axis=0)


def rolling_max(a: np.ndarray, window: int) -> np.ndarray:
    """Maximum over the `window` bars ending at each position."""
    return np.nanmax(_window_stack(a, window), axis=0)
