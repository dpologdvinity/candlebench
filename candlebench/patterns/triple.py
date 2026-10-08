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


@pattern("two_crows", bias="bear", bars_required=3, requires_trend=+1)
def two_crows(g: Geometry, t) -> np.ndarray:
    """A long up bar, a down bar gapping above it, then a down bar back into its body."""
    return (
        lag(g.is_bull, 2)
        & (lag(g.body_ratio, 2) >= t.long_body)
        & lag(g.is_bear, 1)
        & (lag(g.body_bottom, 1) > lag(g.close, 2))
        & g.is_bear
        & (g.open < lag(g.open, 1))
        & (g.open > lag(g.close, 1))
        & (g.close < lag(g.close, 2))
        & (g.close > lag(g.open, 2))
    )


@pattern("upside_gap_two_crows", bias="bear", bars_required=3, requires_trend=+1)
def upside_gap_two_crows(g: Geometry, t) -> np.ndarray:
    """A long up bar, a small down bar gapping above it, then a down bar engulfing that
    one while the gap stays open."""
    return (
        lag(g.is_bull, 2)
        & (lag(g.body_ratio, 2) >= t.long_body)
        & lag(g.is_bear, 1)
        & (lag(g.body_ratio, 1) <= t.small_body)
        & (lag(g.body_bottom, 1) > lag(g.close, 2))
        & g.is_bear
        & (g.open > lag(g.open, 1))
        & (g.close < lag(g.close, 1))
        & (g.close > lag(g.close, 2))
    )


@pattern("identical_three_crows", bias="bear", bars_required=3, requires_trend=+1)
def identical_three_crows(g: Geometry, t) -> np.ndarray:
    """Three long down bars, each opening at the previous close."""
    return (
        lag(g.is_bear, 2)
        & lag(g.is_bear, 1)
        & g.is_bear
        & (lag(g.body_ratio, 2) >= t.long_body)
        & (lag(g.body_ratio, 1) >= t.long_body)
        & (g.body_ratio >= t.long_body)
        & (lag(g.close, 1) < lag(g.close, 2))
        & (g.close < lag(g.close, 1))
        & (np.abs(lag(g.open, 1) - lag(g.close, 2)) <= t.near_equal * lag(g.close, 2))
        & (np.abs(g.open - lag(g.close, 1)) <= t.near_equal * lag(g.close, 1))
    )


@pattern("stick_sandwich", bias="bull", bars_required=3, requires_trend=-1)
def stick_sandwich(g: Geometry, t) -> np.ndarray:
    """A down bar, an up bar trading above its close, then a down bar closing at the same
    close as the first."""
    return (
        lag(g.is_bear, 2)
        & lag(g.is_bull, 1)
        & (lag(g.low, 1) > lag(g.close, 2))
        & g.is_bear
        & (np.abs(g.close - lag(g.close, 2)) <= t.near_equal * lag(g.close, 2))
    )


@pattern("unique_three_river", bias="bull", bars_required=3, requires_trend=-1)
def unique_three_river(g: Geometry, t) -> np.ndarray:
    """A long down bar, a down bar inside its body that probes a new low, then a small
    up bar below the second close."""
    return (
        lag(g.is_bear, 2)
        & (lag(g.body_ratio, 2) >= t.long_body)
        & lag(g.is_bear, 1)
        & (lag(g.body_top, 1) <= lag(g.body_top, 2))
        & (lag(g.body_bottom, 1) >= lag(g.body_bottom, 2))
        & (lag(g.low, 1) < lag(g.low, 2))
        & g.is_bull
        & (g.body_ratio <= t.small_body)
        & (g.close < lag(g.close, 1))
    )


@pattern("upside_tasuki_gap", bias="bull", bars_required=3, requires_trend=+1)
def upside_tasuki_gap(g: Geometry, t) -> np.ndarray:
    """Two up bars with a gap between them, then a down bar that dips into the gap
    without closing it."""
    return (
        lag(g.is_bull, 2)
        & lag(g.is_bull, 1)
        & (lag(g.low, 1) > lag(g.high, 2))
        & g.is_bear
        & (g.open < lag(g.close, 1))
        & (g.open > lag(g.open, 1))
        & (g.close < lag(g.low, 1))
        & (g.close > lag(g.high, 2))
    )


@pattern("downside_tasuki_gap", bias="bear", bars_required=3, requires_trend=-1)
def downside_tasuki_gap(g: Geometry, t) -> np.ndarray:
    """Two down bars with a gap between them, then an up bar that rises into the gap
    without closing it."""
    return (
        lag(g.is_bear, 2)
        & lag(g.is_bear, 1)
        & (lag(g.high, 1) < lag(g.low, 2))
        & g.is_bull
        & (g.open > lag(g.close, 1))
        & (g.open < lag(g.open, 1))
        & (g.close > lag(g.high, 1))
        & (g.close < lag(g.low, 2))
    )


@pattern("upside_gap_three_methods", bias="bull", bars_required=3, requires_trend=+1)
def upside_gap_three_methods(g: Geometry, t) -> np.ndarray:
    """Two up bars with a gap between their bodies, then a down bar that closes the gap."""
    return (
        lag(g.is_bull, 2)
        & lag(g.is_bull, 1)
        & (lag(g.body_bottom, 1) > lag(g.body_top, 2))
        & g.is_bear
        & (g.open > lag(g.body_bottom, 1))
        & (g.open < lag(g.body_top, 1))
        & (g.close < lag(g.body_top, 2))
        & (g.close > lag(g.body_bottom, 2))
    )


@pattern("downside_gap_three_methods", bias="bear", bars_required=3, requires_trend=-1)
def downside_gap_three_methods(g: Geometry, t) -> np.ndarray:
    """Two down bars with a gap between their bodies, then an up bar that closes the gap."""
    return (
        lag(g.is_bear, 2)
        & lag(g.is_bear, 1)
        & (lag(g.body_top, 1) < lag(g.body_bottom, 2))
        & g.is_bull
        & (g.open > lag(g.body_bottom, 1))
        & (g.open < lag(g.body_top, 1))
        & (g.close > lag(g.body_bottom, 2))
        & (g.close < lag(g.body_top, 2))
    )


@pattern("advance_block", bias="bear", bars_required=3, requires_trend=+1)
def advance_block(g: Geometry, t) -> np.ndarray:
    """Three up bars climbing on shrinking bodies and lengthening upper shadows."""
    return (
        lag(g.is_bull, 2)
        & lag(g.is_bull, 1)
        & g.is_bull
        & (lag(g.close, 1) > lag(g.close, 2))
        & (g.close > lag(g.close, 1))
        & (lag(g.open, 1) > lag(g.body_bottom, 2))
        & (lag(g.open, 1) < lag(g.body_top, 2))
        & (g.open > lag(g.body_bottom, 1))
        & (g.open < lag(g.body_top, 1))
        & (lag(g.body, 1) < lag(g.body, 2))
        & (g.body < lag(g.body, 1))
        & (g.upper_shadow > lag(g.upper_shadow, 1))
    )


def _doji(g: Geometry, t, k: int) -> np.ndarray:
    """Whether the bar k bars back is a doji (k = 0 is the signal bar)."""
    rng = lag(g.rng, k) if k else g.rng
    ratio = lag(g.body_ratio, k) if k else g.body_ratio
    return (rng > 0) & (ratio <= t.doji_body)


@pattern("bullish_tri_star", bias="bull", bars_required=3, requires_trend=-1)
def bullish_tri_star(g: Geometry, t) -> np.ndarray:
    """Three doji, the middle one gapping below the other two."""
    middle_top = lag(g.body_top, 1)
    return (
        _doji(g, t, 2) & _doji(g, t, 1) & _doji(g, t, 0)
        & (middle_top < lag(g.body_bottom, 2))
        & (middle_top < g.body_bottom)
    )


@pattern("bearish_tri_star", bias="bear", bars_required=3, requires_trend=+1)
def bearish_tri_star(g: Geometry, t) -> np.ndarray:
    """Three doji, the middle one gapping above the other two."""
    middle_bottom = lag(g.body_bottom, 1)
    return (
        _doji(g, t, 2) & _doji(g, t, 1) & _doji(g, t, 0)
        & (middle_bottom > lag(g.body_top, 2))
        & (middle_bottom > g.body_top)
    )


# Hikkake: an inside bar, then a bar breaking out of it on one side. The trade is
# against that breakout, on the reading that it was a trap. No trend gate: the
# setup is defined by the inside bar, not by what preceded it.
def _inside_bar(g: Geometry) -> np.ndarray:
    return (lag(g.high, 1) < lag(g.high, 2)) & (lag(g.low, 1) > lag(g.low, 2))


@pattern("bullish_hikkake", bias="bull", bars_required=3, requires_trend=0)
def bullish_hikkake(g: Geometry, t) -> np.ndarray:
    """An inside bar, then a lower high and lower low: a downside breakout to fade."""
    return _inside_bar(g) & (g.high < lag(g.high, 1)) & (g.low < lag(g.low, 1))


@pattern("bearish_hikkake", bias="bear", bars_required=3, requires_trend=0)
def bearish_hikkake(g: Geometry, t) -> np.ndarray:
    """An inside bar, then a higher high and higher low: an upside breakout to fade."""
    return _inside_bar(g) & (g.high > lag(g.high, 1)) & (g.low > lag(g.low, 1))
