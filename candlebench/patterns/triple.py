"""Three-bar patterns, and the five-bar three methods.

Several are a two-bar pattern plus a confirming bar: three inside up is a
bullish harami confirmed by a close above the first bar's open, three outside
up a bullish engulfing confirmed by a higher close. The doji stars and
abandoned babies are stricter morning and evening stars, so a bar that fires
one usually fires the other too. Each is still its own row, because the
literature names and trades them separately.
"""

from __future__ import annotations

import numpy as np

from candlebench.patterns import pattern
from candlebench.patterns.context import Geometry, lag


@pattern("morning_star", bias="bull", bars_required=3, requires_trend=-1)
def morning_star(g: Geometry, t) -> np.ndarray:
    """Long down bar, a small body gapping below it, then a long up bar back."""
    first_bottom = lag(g.body_bottom, 2)
    midpoint = first_bottom + 0.5 * lag(g.body, 2)
    return (
        lag(g.is_bear, 2)
        & (lag(g.body_ratio, 2) >= t.long_body)
        & (lag(g.body_ratio, 1) <= t.small_body)
        & (lag(g.body_top, 1) < first_bottom)
        & g.is_bull
        & (g.close > midpoint)
    )


@pattern("evening_star", bias="bear", bars_required=3, requires_trend=+1)
def evening_star(g: Geometry, t) -> np.ndarray:
    """Long up bar, a small body gapping above it, then a long down bar back."""
    first_top = lag(g.body_top, 2)
    midpoint = lag(g.body_bottom, 2) + 0.5 * lag(g.body, 2)
    return (
        lag(g.is_bull, 2)
        & (lag(g.body_ratio, 2) >= t.long_body)
        & (lag(g.body_ratio, 1) <= t.small_body)
        & (lag(g.body_bottom, 1) > first_top)
        & g.is_bear
        & (g.close < midpoint)
    )


@pattern("three_white_soldiers", bias="bull", bars_required=3, requires_trend=-1)
def three_white_soldiers(g: Geometry, t) -> np.ndarray:
    """Three long up bars, each closing higher and opening inside the last body."""
    return (
        lag(g.is_bull, 2)
        & lag(g.is_bull, 1)
        & g.is_bull
        & (lag(g.body_ratio, 2) >= t.long_body)
        & (lag(g.body_ratio, 1) >= t.long_body)
        & (g.body_ratio >= t.long_body)
        & (lag(g.close, 1) > lag(g.close, 2))
        & (g.close > lag(g.close, 1))
        & (lag(g.open, 1) > lag(g.body_bottom, 2))
        & (lag(g.open, 1) < lag(g.body_top, 2))
        & (g.open > lag(g.body_bottom, 1))
        & (g.open < lag(g.body_top, 1))
    )


@pattern("three_black_crows", bias="bear", bars_required=3, requires_trend=+1)
def three_black_crows(g: Geometry, t) -> np.ndarray:
    """Three long down bars, each closing lower and opening inside the last body."""
    return (
        lag(g.is_bear, 2)
        & lag(g.is_bear, 1)
        & g.is_bear
        & (lag(g.body_ratio, 2) >= t.long_body)
        & (lag(g.body_ratio, 1) >= t.long_body)
        & (g.body_ratio >= t.long_body)
        & (lag(g.close, 1) < lag(g.close, 2))
        & (g.close < lag(g.close, 1))
        & (lag(g.open, 1) > lag(g.body_bottom, 2))
        & (lag(g.open, 1) < lag(g.body_top, 2))
        & (g.open > lag(g.body_bottom, 1))
        & (g.open < lag(g.body_top, 1))
    )


@pattern("three_inside_up", bias="bull", bars_required=3, requires_trend=-1)
def three_inside_up(g: Geometry, t) -> np.ndarray:
    """A bullish harami, confirmed by an up bar closing above the first bar's open."""
    return (
        lag(g.is_bear, 2)
        & (lag(g.body_ratio, 2) >= t.long_body)
        & lag(g.is_bull, 1)
        & (lag(g.body_top, 1) <= lag(g.body_top, 2))
        & (lag(g.body_bottom, 1) >= lag(g.body_bottom, 2))
        & g.is_bull
        & (g.close > lag(g.open, 2))
    )


@pattern("three_inside_down", bias="bear", bars_required=3, requires_trend=+1)
def three_inside_down(g: Geometry, t) -> np.ndarray:
    """A bearish harami, confirmed by a down bar closing below the first bar's open."""
    return (
        lag(g.is_bull, 2)
        & (lag(g.body_ratio, 2) >= t.long_body)
        & lag(g.is_bear, 1)
        & (lag(g.body_top, 1) <= lag(g.body_top, 2))
        & (lag(g.body_bottom, 1) >= lag(g.body_bottom, 2))
        & g.is_bear
        & (g.close < lag(g.open, 2))
    )


@pattern("three_outside_up", bias="bull", bars_required=3, requires_trend=-1)
def three_outside_up(g: Geometry, t) -> np.ndarray:
    """A bullish engulfing, confirmed by an up bar closing higher still."""
    return (
        lag(g.is_bear, 2)
        & lag(g.is_bull, 1)
        & (lag(g.body, 1) > 0)
        & (lag(g.body_bottom, 1) <= lag(g.body_bottom, 2))
        & (lag(g.body_top, 1) >= lag(g.body_top, 2))
        & g.is_bull
        & (g.close > lag(g.close, 1))
    )


@pattern("three_outside_down", bias="bear", bars_required=3, requires_trend=+1)
def three_outside_down(g: Geometry, t) -> np.ndarray:
    """A bearish engulfing, confirmed by a down bar closing lower still."""
    return (
        lag(g.is_bull, 2)
        & lag(g.is_bear, 1)
        & (lag(g.body, 1) > 0)
        & (lag(g.body_bottom, 1) <= lag(g.body_bottom, 2))
        & (lag(g.body_top, 1) >= lag(g.body_top, 2))
        & g.is_bear
        & (g.close < lag(g.close, 1))
    )


@pattern("morning_doji_star", bias="bull", bars_required=3, requires_trend=-1)
def morning_doji_star(g: Geometry, t) -> np.ndarray:
    """A morning star whose middle bar is a doji."""
    return morning_star(g, t) & (lag(g.rng, 1) > 0) & (lag(g.body_ratio, 1) <= t.doji_body)


@pattern("evening_doji_star", bias="bear", bars_required=3, requires_trend=+1)
def evening_doji_star(g: Geometry, t) -> np.ndarray:
    """An evening star whose middle bar is a doji."""
    return evening_star(g, t) & (lag(g.rng, 1) > 0) & (lag(g.body_ratio, 1) <= t.doji_body)


@pattern("bullish_abandoned_baby", bias="bull", bars_required=3, requires_trend=-1)
def bullish_abandoned_baby(g: Geometry, t) -> np.ndarray:
    """A morning doji star whose doji gaps clear of both neighbours' ranges."""
    return (
        morning_doji_star(g, t)
        & (lag(g.high, 1) < lag(g.low, 2))
        & (g.low > lag(g.high, 1))
    )


@pattern("bearish_abandoned_baby", bias="bear", bars_required=3, requires_trend=+1)
def bearish_abandoned_baby(g: Geometry, t) -> np.ndarray:
    """An evening doji star whose doji gaps clear of both neighbours' ranges."""
    return (
        evening_doji_star(g, t)
        & (lag(g.low, 1) > lag(g.high, 2))
        & (g.high < lag(g.low, 1))
    )


def _held_inside_first(g: Geometry, t) -> np.ndarray:
    """Bars 1-3 of a three methods: small bodies within the first bar's range."""
    out = np.ones(len(g), dtype=bool)
    for k in (1, 2, 3):
        out &= (
            (lag(g.body_ratio, k) <= t.small_body)
            & (lag(g.high, k) <= lag(g.high, 4))
            & (lag(g.low, k) >= lag(g.low, 4))
        )
    return out


# The three methods are continuations: they need the trend they continue.
@pattern("rising_three_methods", bias="bull", bars_required=5, requires_trend=+1)
def rising_three_methods(g: Geometry, t) -> np.ndarray:
    """A long up bar, three small bars held inside it, then a long up bar beyond it."""
    return (
        lag(g.is_bull, 4)
        & (lag(g.body_ratio, 4) >= t.long_body)
        & _held_inside_first(g, t)
        & g.is_bull
        & (g.body_ratio >= t.long_body)
        & (g.close > lag(g.close, 4))
    )


@pattern("falling_three_methods", bias="bear", bars_required=5, requires_trend=-1)
def falling_three_methods(g: Geometry, t) -> np.ndarray:
    """A long down bar, three small bars held inside it, then a long down bar beyond it."""
    return (
        lag(g.is_bear, 4)
        & (lag(g.body_ratio, 4) >= t.long_body)
        & _held_inside_first(g, t)
        & g.is_bear
        & (g.body_ratio >= t.long_body)
        & (g.close < lag(g.close, 4))
    )
