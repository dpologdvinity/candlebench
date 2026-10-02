"""Detector tests.

Each pattern gets a known positive and a near miss that fails exactly one
condition, so a passing test pins the specific rule rather than the general
shape.

Several pairs share one geometry and differ only in the prior trend. For those
the near miss is the opposite trend, which is the condition the surveyed
reference library omits entirely.
"""

from __future__ import annotations

import pytest

from candlebench import patterns
from tests.conftest import geometry, prefixed

# name -> (trend, hit rows, miss rows)
CASES: dict[str, tuple[str, list, list]] = {
    "hammer": (
        "down",
        [(100, 100.25, 99.0, 100.2)],
        [(100, 101.0, 99.0, 100.9)],  # body too large for a hammer
    ),
    "inverted_hammer": (
        "down",
        [(100, 101.0, 99.95, 100.2)],
        [(100, 101.0, 99.0, 100.2)],  # lower shadow too long
    ),
    "hanging_man": (
        "up",
        [(100, 100.25, 99.0, 100.2)],
        [(100, 101.0, 99.0, 100.9)],
    ),
    "shooting_star": (
        "up",
        [(100, 101.0, 99.95, 100.2)],
        [(100, 101.0, 99.0, 100.2)],
    ),
    "dragonfly_doji": (
        "down",
        [(100, 100.05, 99.0, 100.0)],
        [(100, 100.6, 99.0, 100.0)],  # upper shadow too long
    ),
    "gravestone_doji": (
        "up",
        [(100, 101.0, 99.95, 100.0)],
        [(100, 101.0, 99.4, 100.0)],  # lower shadow too long
    ),
    "bullish_engulfing": (
        "down",
        [(100, 100.1, 99.4, 99.5), (99.3, 100.4, 99.2, 100.3)],
        [(100, 100.1, 99.4, 99.5), (99.6, 100.0, 99.5, 99.9)],  # body not engulfed
    ),
    "bearish_engulfing": (
        "up",
        [(100, 100.6, 99.9, 100.5), (100.7, 100.8, 99.8, 99.9)],
        [(100, 100.6, 99.9, 100.5), (100.4, 100.5, 100.0, 100.1)],
    ),
    "bullish_harami": (
        "down",
        [(101, 101.1, 99.4, 99.5), (99.8, 100.6, 99.7, 100.4)],
        [(101, 101.1, 99.4, 99.5), (99.8, 101.5, 99.7, 101.2)],  # body not contained
    ),
    "bearish_harami": (
        "up",
        [(99.5, 101.1, 99.4, 101.0), (100.4, 100.6, 99.7, 99.8)],
        [(99.5, 101.1, 99.4, 101.0), (100.4, 100.6, 99.0, 99.2)],
    ),
    "piercing_line": (
        "down",
        [(101, 101.1, 99.4, 99.5), (99.2, 100.6, 99.1, 100.5)],
        [(101, 101.1, 99.4, 99.5), (99.2, 100.0, 99.1, 99.9)],  # closes below midpoint
    ),
    "dark_cloud_cover": (
        "up",
        [(99.5, 101.1, 99.4, 101.0), (101.3, 101.4, 99.7, 99.8)],
        [(99.5, 101.1, 99.4, 101.0), (101.3, 101.4, 100.5, 100.6)],
    ),
    "tweezer_bottom": (
        "down",
        [(100, 100.1, 99.0, 99.2), (99.3, 100.0, 99.0, 99.9)],
        [(100, 100.1, 99.0, 99.2), (99.3, 100.0, 98.0, 99.9)],  # lows not equal
    ),
    "tweezer_top": (
        "up",
        [(99.9, 101.0, 99.8, 100.8), (100.7, 101.0, 100.0, 100.1)],
        [(99.9, 101.0, 99.8, 100.8), (100.7, 102.0, 100.0, 100.1)],
    ),
    "bullish_kicker": (
        "down",
        [(100, 100.1, 99.4, 99.5), (100.2, 100.8, 100.15, 100.7)],
        [(100, 100.1, 99.4, 99.5), (100.0, 100.8, 99.95, 100.7)],  # no gap clear of the high
    ),
    "bearish_kicker": (
        "up",
        [(99.5, 100.6, 99.4, 100.5), (99.3, 99.35, 98.8, 98.9)],
        [(99.5, 100.6, 99.4, 100.5), (99.5, 99.55, 98.8, 98.9)],
    ),
    "morning_star": (
        "down",
        [(101, 101.1, 99.4, 99.5), (99.2, 99.3, 98.9, 99.1), (99.4, 100.7, 99.35, 100.6)],
        [(101, 101.1, 99.4, 99.5), (99.2, 99.3, 98.9, 99.1), (99.4, 100.1, 99.35, 100.0)],
    ),
    "evening_star": (
        "up",
        [(99.5, 101.1, 99.4, 101.0), (101.3, 101.6, 101.2, 101.4), (101.1, 101.15, 99.9, 100.0)],
        [(99.5, 101.1, 99.4, 101.0), (101.3, 101.6, 101.2, 101.4), (101.1, 101.15, 100.4, 100.5)],
    ),
    "three_white_soldiers": (
        "down",
        [
            (100, 100.9, 99.95, 100.8),
            (100.5, 101.5, 100.45, 101.4),
            (101.0, 102.1, 100.95, 102.0),
        ],
        [
            (100, 100.9, 99.95, 100.8),
            (100.5, 101.5, 100.45, 101.4),
            (101.5, 102.1, 101.45, 102.0),  # opens above the previous body
        ],
    ),
    "three_black_crows": (
        "up",
        [
            (100.8, 100.85, 99.9, 100.0),
            (100.3, 100.35, 99.35, 99.4),
            (99.8, 99.85, 98.75, 98.8),
        ],
        [
            (100.8, 100.85, 99.9, 100.0),
            (100.3, 100.35, 99.35, 99.4),
            (99.3, 99.35, 98.75, 98.8),  # opens below the previous body
        ],
    ),
}


def test_every_registered_pattern_has_a_case():
    """A new detector without a test is a detector nobody has checked."""
    registered = {n for n, s in patterns.registry().items() if s.kind == "pattern"}
    assert registered == set(CASES)
    assert len(registered) == 20


@pytest.mark.parametrize("name", sorted(CASES))
def test_pattern_fires_on_its_own_shape(name, thresholds):
    trend, hit, _ = CASES[name]
    spec = patterns.get(name)
    mask = patterns.detect(spec, geometry(prefixed(trend, hit)), thresholds)
    assert mask[-1], f"{name} did not fire on its own shape"


@pytest.mark.parametrize("name", sorted(CASES))
def test_pattern_rejects_a_near_miss(name, thresholds):
    trend, _, miss = CASES[name]
    spec = patterns.get(name)
    mask = patterns.detect(spec, geometry(prefixed(trend, miss)), thresholds)
    assert not mask[-1], f"{name} fired on a shape that should fail one condition"


@pytest.mark.parametrize(
    "name,wrong_trend",
    [
        ("hammer", "up"),
        ("hanging_man", "down"),
        ("inverted_hammer", "up"),
        ("shooting_star", "down"),
        ("dragonfly_doji", "up"),
        ("gravestone_doji", "down"),
        ("bullish_engulfing", "up"),
        ("bearish_engulfing", "down"),
        ("morning_star", "up"),
        ("evening_star", "down"),
    ],
)
def test_prior_trend_is_required(name, wrong_trend, thresholds):
    """The same geometry after the opposite trend must not fire.

    Hammer and hanging man are one shape. A library that ignores trend reports
    that shape twice under two names and treats them as two findings.
    """
    _, hit, _ = CASES[name]
    spec = patterns.get(name)
    mask = patterns.detect(spec, geometry(prefixed(wrong_trend, hit)), thresholds)
    assert not mask[-1]


def test_hammer_geometry_is_a_hanging_man_in_an_uptrend(thresholds):
    """The pair must swap, not both go silent."""
    _, hit, _ = CASES["hammer"]
    up = geometry(prefixed("up", hit))
    assert not patterns.detect(patterns.get("hammer"), up, thresholds)[-1]
    assert patterns.detect(patterns.get("hanging_man"), up, thresholds)[-1]


@pytest.mark.parametrize("name", sorted(CASES))
def test_no_pattern_fires_without_enough_history(name, thresholds):
    """Bars that cannot have the required history must be masked off.

    The surveyed reference library loops over every row including those with
    fewer preceding bars than its own pattern needs.
    """
    trend, hit, _ = CASES[name]
    spec = patterns.get(name)
    geom = geometry(prefixed(trend, hit))
    cutoff = patterns.first_valid_index(spec, geom.trend_lookback)
    mask = patterns.detect(spec, geom, thresholds)
    assert not mask[:cutoff].any()


@pytest.mark.parametrize("name", sorted(CASES))
def test_zero_range_bars_match_nothing(name, thresholds):
    """A bar where all four prices are equal is ordinary on thin 1m data.

    Its range is zero, so every geometric ratio divides by zero. It must match
    no pattern and raise nothing.
    """
    flat = [(100.0, 100.0, 100.0, 100.0)] * 6
    spec = patterns.get(name)
    mask = patterns.detect(spec, geometry(prefixed("down", flat)), thresholds)
    assert not mask.any()


@pytest.mark.parametrize("name", sorted(CASES))
def test_detection_uses_no_future_bars(name, thresholds):
    """The mask at bar i must not change when later bars change.

    This is the property that keeps every downstream number meaningful, so it
    is asserted directly rather than assumed from the code's shape.
    """
    trend, hit, _ = CASES[name]
    spec = patterns.get(name)
    base = prefixed(trend, hit)
    extended_up = base + [(200, 260, 199, 255)]
    extended_down = base + [(200, 201, 140, 145)]

    a = patterns.detect(spec, geometry(extended_up), thresholds)
    b = patterns.detect(spec, geometry(extended_down), thresholds)
    assert list(a[: len(base)]) == list(b[: len(base)])
