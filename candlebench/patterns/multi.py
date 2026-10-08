"""Four- and five-bar patterns.

These describe a whole move rather than a turn: a run of bars and the bar that
ends or confirms it. The stop still sits at the extreme of every bar in the
pattern, so a longer formation usually means a wider stop and fewer R lost to
the spread, which the matched controls copy rather than reward.
"""

from __future__ import annotations

import numpy as np

from candlebench.patterns import pattern
from candlebench.patterns.context import Geometry, lag


def _falling_run(g: Geometry, first: int, last: int) -> np.ndarray:
    """Down bars from `first` to `last` bars back, each opening and closing lower."""
    out = lag(g.is_bear, first)
    for k in range(first - 1, last - 1, -1):
        out = out & lag(g.is_bear, k) & (lag(g.open, k) < lag(g.open, k + 1)) & (
            lag(g.close, k) < lag(g.close, k + 1))
    return out


def _rising_run(g: Geometry, first: int, last: int) -> np.ndarray:
    """Up bars from `first` to `last` bars back, each opening and closing higher."""
    out = lag(g.is_bull, first)
    for k in range(first - 1, last - 1, -1):
        out = out & lag(g.is_bull, k) & (lag(g.open, k) > lag(g.open, k + 1)) & (
            lag(g.close, k) > lag(g.close, k + 1))
    return out


@pattern("ladder_bottom", bias="bull", bars_required=5, requires_trend=-1)
def ladder_bottom(g: Geometry, t) -> np.ndarray:
    """Three falling down bars, a fourth with an upper shadow, then an up bar gapping
    above the fourth's body and closing above its high."""
    return (
        _falling_run(g, 4, 2)
        & lag(g.is_bear, 1)
        & (lag(g.upper_shadow, 1) > lag(g.body, 1))
        & g.is_bull
        & (g.open > lag(g.body_top, 1))
        & (g.close > lag(g.high, 1))
    )


@pattern("mat_hold", bias="bull", bars_required=5, requires_trend=+1)
def mat_hold(g: Geometry, t) -> np.ndarray:
    """A long up bar, three small bars that gap above it and hold above its body,
    then an up bar closing above all of them."""
    held = np.ones(len(g), dtype=bool)
    for k in (1, 2, 3):
        held &= (lag(g.body_ratio, k) <= t.small_body) & (
            lag(g.body_bottom, k) > lag(g.body_bottom, 4))
    top = np.fmax(np.fmax(lag(g.high, 1), lag(g.high, 2)), lag(g.high, 3))
    return (
        lag(g.is_bull, 4)
        & (lag(g.body_ratio, 4) >= t.long_body)
        & (lag(g.body_bottom, 3) > lag(g.close, 4))
        & held
        & g.is_bull
        & (g.close > top)
    )


@pattern("bullish_breakaway", bias="bull", bars_required=5, requires_trend=-1)
def bullish_breakaway(g: Geometry, t) -> np.ndarray:
    """A long down bar, a down bar gapping below it, two more lower closes, then a long
    up bar closing inside the gap."""
    return (
        lag(g.is_bear, 4)
        & (lag(g.body_ratio, 4) >= t.long_body)
        & lag(g.is_bear, 3)
        & (lag(g.body_top, 3) < lag(g.body_bottom, 4))
        & (lag(g.close, 2) < lag(g.close, 3))
        & lag(g.is_bear, 1)
        & (lag(g.close, 1) < lag(g.close, 2))
        & g.is_bull
        & (g.body_ratio >= t.long_body)
        & (g.close > lag(g.body_top, 3))
        & (g.close < lag(g.body_bottom, 4))
    )


@pattern("bearish_breakaway", bias="bear", bars_required=5, requires_trend=+1)
def bearish_breakaway(g: Geometry, t) -> np.ndarray:
    """A long up bar, an up bar gapping above it, two more higher closes, then a long
    down bar closing inside the gap."""
    return (
        lag(g.is_bull, 4)
        & (lag(g.body_ratio, 4) >= t.long_body)
        & lag(g.is_bull, 3)
        & (lag(g.body_bottom, 3) > lag(g.body_top, 4))
        & (lag(g.close, 2) > lag(g.close, 3))
        & lag(g.is_bull, 1)
        & (lag(g.close, 1) > lag(g.close, 2))
        & g.is_bear
        & (g.body_ratio >= t.long_body)
        & (g.close < lag(g.body_bottom, 3))
        & (g.close > lag(g.body_top, 4))
    )


# Three-line strike, named for the direction of the bar that strikes: three
# bars running one way, then one bar that reverses all of them.
@pattern("bullish_three_line_strike", bias="bull", bars_required=4, requires_trend=-1)
def bullish_three_line_strike(g: Geometry, t) -> np.ndarray:
    """Three falling down bars, then an up bar opening below the third and closing
    above the first's open."""
    return (
        _falling_run(g, 3, 1)
        & g.is_bull
        & (g.open < lag(g.close, 1))
        & (g.close > lag(g.open, 3))
    )


@pattern("bearish_three_line_strike", bias="bear", bars_required=4, requires_trend=+1)
def bearish_three_line_strike(g: Geometry, t) -> np.ndarray:
    """Three rising up bars, then a down bar opening above the third and closing
    below the first's open."""
    return (
        _rising_run(g, 3, 1)
        & g.is_bear
        & (g.open > lag(g.close, 1))
        & (g.close < lag(g.open, 3))
    )
