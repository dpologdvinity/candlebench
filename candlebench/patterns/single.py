"""Single-bar patterns.

Hammer and hanging man share one geometry, as do inverted hammer and shooting
star, and dragonfly and gravestone doji. In each pair the only difference is the
prior trend, which the registry applies. Writing the geometry once and
registering it twice makes that explicit instead of duplicating the thresholds
and inviting them to drift apart.
"""

from __future__ import annotations

import numpy as np

from candlebench.patterns import pattern
from candlebench.patterns.context import Geometry


def _long_lower_shadow(g: Geometry, t) -> np.ndarray:
    """A small body near the top of the range with a long lower shadow."""
    return (
        (g.rng > 0)
        & (g.body_ratio <= t.small_body)
        & (g.lower_shadow >= t.shadow_dominance * g.body)
        & (g.upper_shadow <= t.opposite_shadow_max * g.rng)
    )


def _long_upper_shadow(g: Geometry, t) -> np.ndarray:
    """A small body near the bottom of the range with a long upper shadow."""
    return (
        (g.rng > 0)
        & (g.body_ratio <= t.small_body)
        & (g.upper_shadow >= t.shadow_dominance * g.body)
        & (g.lower_shadow <= t.opposite_shadow_max * g.rng)
    )


def _dragonfly(g: Geometry, t) -> np.ndarray:
    """A doji body with essentially all of the range below it."""
    return (
        (g.rng > 0)
        & (g.body_ratio <= t.doji_body)
        & (g.lower_shadow >= t.doji_shadow_min * g.rng)
        & (g.upper_shadow <= t.opposite_shadow_max * g.rng)
    )


def _gravestone(g: Geometry, t) -> np.ndarray:
    """A doji body with essentially all of the range above it."""
    return (
        (g.rng > 0)
        & (g.body_ratio <= t.doji_body)
        & (g.upper_shadow >= t.doji_shadow_min * g.rng)
        & (g.lower_shadow <= t.opposite_shadow_max * g.rng)
    )


# A long lower shadow after a decline is a hammer; the identical bar after an
# advance is a hanging man. Same for the inverted pair and the doji pair.
pattern("hammer", bias="bull", bars_required=1, requires_trend=-1)(_long_lower_shadow)
pattern("hanging_man", bias="bear", bars_required=1, requires_trend=+1)(_long_lower_shadow)
pattern("inverted_hammer", bias="bull", bars_required=1, requires_trend=-1)(_long_upper_shadow)
pattern("shooting_star", bias="bear", bars_required=1, requires_trend=+1)(_long_upper_shadow)
pattern("dragonfly_doji", bias="bull", bars_required=1, requires_trend=-1)(_dragonfly)
pattern("gravestone_doji", bias="bear", bars_required=1, requires_trend=+1)(_gravestone)


def _bullish_marubozu(g: Geometry, t) -> np.ndarray:
    """An up bar that is nearly all body."""
    return (g.rng > 0) & g.is_bull & (g.body_ratio >= t.marubozu_body)


def _bearish_marubozu(g: Geometry, t) -> np.ndarray:
    """A down bar that is nearly all body."""
    return (g.rng > 0) & g.is_bear & (g.body_ratio >= t.marubozu_body)


def _bullish_belt_hold(g: Geometry, t) -> np.ndarray:
    """A long up bar opening at, or within a marubozu's shadow of, its low."""
    return (
        (g.rng > 0)
        & g.is_bull
        & (g.body_ratio >= t.long_body)
        & (g.lower_shadow <= (1 - t.marubozu_body) * g.rng)
    )


def _bearish_belt_hold(g: Geometry, t) -> np.ndarray:
    """A long down bar opening at, or within a marubozu's shadow of, its high."""
    return (
        (g.rng > 0)
        & g.is_bear
        & (g.body_ratio >= t.long_body)
        & (g.upper_shadow <= (1 - t.marubozu_body) * g.rng)
    )


# A marubozu is read as conviction in its own direction whatever came before;
# a belt hold is the same open-at-the-extreme bar arriving against the trend.
pattern("bullish_marubozu", bias="bull", bars_required=1, requires_trend=0)(_bullish_marubozu)
pattern("bearish_marubozu", bias="bear", bars_required=1, requires_trend=0)(_bearish_marubozu)
pattern("bullish_belt_hold", bias="bull", bars_required=1, requires_trend=-1)(_bullish_belt_hold)
pattern("bearish_belt_hold", bias="bear", bars_required=1, requires_trend=+1)(_bearish_belt_hold)
