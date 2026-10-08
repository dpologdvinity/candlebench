"""Two-bar patterns.

Each rule compares the signal bar against `lag(..., 1)`, the preceding bar. The
lag helper pads with NaN and False, so the first bar of a session compares
false rather than wrapping around to the last bar of the previous one.
"""

from __future__ import annotations

import numpy as np

from candlebench.patterns import pattern
from candlebench.patterns.context import Geometry, lag


@pattern("bullish_engulfing", bias="bull", bars_required=2, requires_trend=-1)
def bullish_engulfing(g: Geometry, t) -> np.ndarray:
    return (
        lag(g.is_bear)
        & g.is_bull
        & (g.body_bottom <= lag(g.body_bottom))
        & (g.body_top >= lag(g.body_top))
        & (g.body > 0)
    )


@pattern("bearish_engulfing", bias="bear", bars_required=2, requires_trend=+1)
def bearish_engulfing(g: Geometry, t) -> np.ndarray:
    return (
        lag(g.is_bull)
        & g.is_bear
        & (g.body_bottom <= lag(g.body_bottom))
        & (g.body_top >= lag(g.body_top))
        & (g.body > 0)
    )


@pattern("bullish_harami", bias="bull", bars_required=2, requires_trend=-1)
def bullish_harami(g: Geometry, t) -> np.ndarray:
    return (
        lag(g.is_bear)
        & (lag(g.body_ratio) >= t.long_body)
        & g.is_bull
        & (g.body_top <= lag(g.body_top))
        & (g.body_bottom >= lag(g.body_bottom))
    )


@pattern("bearish_harami", bias="bear", bars_required=2, requires_trend=+1)
def bearish_harami(g: Geometry, t) -> np.ndarray:
    return (
        lag(g.is_bull)
        & (lag(g.body_ratio) >= t.long_body)
        & g.is_bear
        & (g.body_top <= lag(g.body_top))
        & (g.body_bottom >= lag(g.body_bottom))
    )


@pattern("piercing_line", bias="bull", bars_required=2, requires_trend=-1)
def piercing_line(g: Geometry, t) -> np.ndarray:
    """Opens below the prior close, then closes back above its midpoint."""
    midpoint = lag(g.body_bottom) + 0.5 * lag(g.body)
    return (
        lag(g.is_bear)
        & (lag(g.body_ratio) >= t.long_body)
        & g.is_bull
        & (g.open < lag(g.close))
        & (g.close > midpoint)
        & (g.close < lag(g.open))
    )


@pattern("dark_cloud_cover", bias="bear", bars_required=2, requires_trend=+1)
def dark_cloud_cover(g: Geometry, t) -> np.ndarray:
    """Opens above the prior close, then closes back below its midpoint."""
    midpoint = lag(g.body_bottom) + 0.5 * lag(g.body)
    return (
        lag(g.is_bull)
        & (lag(g.body_ratio) >= t.long_body)
        & g.is_bear
        & (g.open > lag(g.close))
        & (g.close < midpoint)
        & (g.close > lag(g.open))
    )


@pattern("tweezer_bottom", bias="bull", bars_required=2, requires_trend=-1)
def tweezer_bottom(g: Geometry, t) -> np.ndarray:
    """Two bars rejecting from the same low."""
    return (
        lag(g.is_bear)
        & g.is_bull
        & (np.abs(g.low - lag(g.low)) <= t.near_equal * lag(g.low))
    )


@pattern("tweezer_top", bias="bear", bars_required=2, requires_trend=+1)
def tweezer_top(g: Geometry, t) -> np.ndarray:
    """Two bars rejecting from the same high."""
    return (
        lag(g.is_bull)
        & g.is_bear
        & (np.abs(g.high - lag(g.high)) <= t.near_equal * lag(g.high))
    )


# Kickers carry no trend requirement: the gap is the whole signal, and the
# classic descriptions do not condition one on a preceding trend.
@pattern("bullish_kicker", bias="bull", bars_required=2, requires_trend=0)
def bullish_kicker(g: Geometry, t) -> np.ndarray:
    """A down bar followed by an up bar gapping clear of its high."""
    return lag(g.is_bear) & g.is_bull & (g.open >= lag(g.high) * (1 + t.gap_min))


@pattern("bearish_kicker", bias="bear", bars_required=2, requires_trend=0)
def bearish_kicker(g: Geometry, t) -> np.ndarray:
    """An up bar followed by a down bar gapping clear of its low."""
    return lag(g.is_bull) & g.is_bear & (g.open <= lag(g.low) * (1 - t.gap_min))


def _inside_prior_body(g: Geometry) -> np.ndarray:
    return (g.body_top <= lag(g.body_top)) & (g.body_bottom >= lag(g.body_bottom))


@pattern("bullish_harami_cross", bias="bull", bars_required=2, requires_trend=-1)
def bullish_harami_cross(g: Geometry, t) -> np.ndarray:
    """A long down bar, then a doji inside its body."""
    return (
        lag(g.is_bear)
        & (lag(g.body_ratio) >= t.long_body)
        & (g.rng > 0)
        & (g.body_ratio <= t.doji_body)
        & _inside_prior_body(g)
    )


@pattern("bearish_harami_cross", bias="bear", bars_required=2, requires_trend=+1)
def bearish_harami_cross(g: Geometry, t) -> np.ndarray:
    """A long up bar, then a doji inside its body."""
    return (
        lag(g.is_bull)
        & (lag(g.body_ratio) >= t.long_body)
        & (g.rng > 0)
        & (g.body_ratio <= t.doji_body)
        & _inside_prior_body(g)
    )


@pattern("bullish_counterattack", bias="bull", bars_required=2, requires_trend=-1)
def bullish_counterattack(g: Geometry, t) -> np.ndarray:
    """A long down bar, then a long up bar opening lower and closing at its close."""
    return (
        lag(g.is_bear)
        & (lag(g.body_ratio) >= t.long_body)
        & g.is_bull
        & (g.body_ratio >= t.long_body)
        & (g.open < lag(g.close))
        & (np.abs(g.close - lag(g.close)) <= t.near_equal * lag(g.close))
    )


@pattern("bearish_counterattack", bias="bear", bars_required=2, requires_trend=+1)
def bearish_counterattack(g: Geometry, t) -> np.ndarray:
    """A long up bar, then a long down bar opening higher and closing at its close."""
    return (
        lag(g.is_bull)
        & (lag(g.body_ratio) >= t.long_body)
        & g.is_bear
        & (g.body_ratio >= t.long_body)
        & (g.open > lag(g.close))
        & (np.abs(g.close - lag(g.close)) <= t.near_equal * lag(g.close))
    )


# Homing pigeon and descending hawk are harami whose second bar keeps the first
# bar's colour: the move continues, but on a body too small to leave the last.
@pattern("homing_pigeon", bias="bull", bars_required=2, requires_trend=-1)
def homing_pigeon(g: Geometry, t) -> np.ndarray:
    """A long down bar, then a smaller down bar inside its body."""
    return lag(g.is_bear) & (lag(g.body_ratio) >= t.long_body) & g.is_bear & _inside_prior_body(g)


@pattern("descending_hawk", bias="bear", bars_required=2, requires_trend=+1)
def descending_hawk(g: Geometry, t) -> np.ndarray:
    """A long up bar, then a smaller up bar inside its body."""
    return lag(g.is_bull) & (lag(g.body_ratio) >= t.long_body) & g.is_bull & _inside_prior_body(g)


@pattern("bullish_separating_lines", bias="bull", bars_required=2, requires_trend=+1)
def bullish_separating_lines(g: Geometry, t) -> np.ndarray:
    """In an advance, a down bar, then a long up bar opening at its open."""
    return (
        lag(g.is_bear)
        & g.is_bull
        & (g.body_ratio >= t.long_body)
        & (np.abs(g.open - lag(g.open)) <= t.near_equal * lag(g.open))
    )


@pattern("bearish_separating_lines", bias="bear", bars_required=2, requires_trend=-1)
def bearish_separating_lines(g: Geometry, t) -> np.ndarray:
    """In a decline, an up bar, then a long down bar opening at its open."""
    return (
        lag(g.is_bull)
        & g.is_bear
        & (g.body_ratio >= t.long_body)
        & (np.abs(g.open - lag(g.open)) <= t.near_equal * lag(g.open))
    )


# On neck, in neck and thrusting are one shape at three depths: a long down bar,
# then an up bar opening below its low whose close stops at the low (on neck),
# just inside the body (in neck), or short of the midpoint (thrusting). Each is
# read as a failed rally, so all three are bearish continuations.
NECK_PENETRATION = 0.1  # how far into the prior body an in-neck close may reach


def _neck(g: Geometry, t) -> np.ndarray:
    return (
        lag(g.is_bear)
        & (lag(g.body_ratio) >= t.long_body)
        & g.is_bull
        & (g.open < lag(g.low))
    )


@pattern("on_neck", bias="bear", bars_required=2, requires_trend=-1)
def on_neck(g: Geometry, t) -> np.ndarray:
    """The rally closes at the prior bar's low."""
    return _neck(g, t) & (np.abs(g.close - lag(g.low)) <= t.near_equal * lag(g.low))


@pattern("in_neck", bias="bear", bars_required=2, requires_trend=-1)
def in_neck(g: Geometry, t) -> np.ndarray:
    """The rally closes just inside the prior body."""
    return (
        _neck(g, t)
        & (g.close >= lag(g.close))
        & (g.close <= lag(g.close) + NECK_PENETRATION * lag(g.body))
    )


@pattern("thrusting", bias="bear", bars_required=2, requires_trend=-1)
def thrusting(g: Geometry, t) -> np.ndarray:
    """The rally closes deeper into the prior body, but short of its midpoint."""
    midpoint = lag(g.body_bottom) + 0.5 * lag(g.body)
    return (
        _neck(g, t)
        & (g.close > lag(g.close) + NECK_PENETRATION * lag(g.body))
        & (g.close < midpoint)
    )


@pattern("matching_low", bias="bull", bars_required=2, requires_trend=-1)
def matching_low(g: Geometry, t) -> np.ndarray:
    """A long down bar, then a down bar closing at the same price."""
    return (
        lag(g.is_bear)
        & (lag(g.body_ratio) >= t.long_body)
        & g.is_bear
        & (np.abs(g.close - lag(g.close)) <= t.near_equal * lag(g.close))
    )
