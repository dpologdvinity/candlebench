"""Three-bar patterns."""

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
