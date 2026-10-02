"""Geometry and trend tests."""

from __future__ import annotations

import numpy as np
import pytest

from candlebench.patterns import context
from tests.conftest import arrays, falling, geometry, rising


def test_geometry_decomposes_a_bar():
    g = geometry([(100.0, 101.0, 98.0, 100.5)], lookback=2)
    assert g.rng[0] == pytest.approx(3.0)
    assert g.body[0] == pytest.approx(0.5)
    assert g.body_top[0] == pytest.approx(100.5)
    assert g.body_bottom[0] == pytest.approx(100.0)
    assert g.upper_shadow[0] == pytest.approx(0.5)
    assert g.lower_shadow[0] == pytest.approx(2.0)
    assert g.body_ratio[0] == pytest.approx(0.5 / 3.0)
    assert g.is_bull[0]
    assert not g.is_bear[0]


def test_a_zero_range_bar_yields_a_zero_ratio_not_a_division_error():
    """All four prices equal is ordinary on a quiet one-minute bar."""
    g = geometry([(100.0, 100.0, 100.0, 100.0)], lookback=2)
    assert g.rng[0] == 0.0
    assert g.body_ratio[0] == 0.0
    assert not g.is_bull[0]
    assert not g.is_bear[0]


def test_trend_is_negative_while_falling_and_positive_while_rising():
    assert geometry(falling(12)).trend[-1] == -1
    assert geometry(rising(12)).trend[-1] == 1


def test_a_flat_series_has_no_trend():
    flat = [(100.0, 100.2, 99.8, 100.0)] * 12
    assert geometry(flat).trend[-1] == 0


def test_bars_without_a_complete_lookback_window_have_no_trend():
    """Rather than a trend inferred from two bars."""
    trend = geometry(falling(12), lookback=10).trend
    assert not trend[:9].any()
    assert trend[9] == -1


def test_the_trend_threshold_can_require_a_steeper_slope():
    """A shallow drift is not a trend if the config says it is not."""
    shallow = falling(12, step=-0.001)
    assert context.trend_series(arrays(shallow)["close"], 10, 0.0)[-1] == -1
    assert context.trend_series(arrays(shallow)["close"], 10, 0.01)[-1] == 0


def test_trend_is_scale_free():
    """A $5 stock and a $500 stock falling by the same percentage match."""
    cheap = context.trend_series(arrays(falling(12, start=5.0, step=-0.025))["close"], 10, 0.001)
    rich = context.trend_series(arrays(falling(12, start=500.0, step=-2.5))["close"], 10, 0.001)
    assert list(cheap) == list(rich)


def test_lag_pads_rather_than_wrapping():
    """A wrap would compare a session's first bar to its last."""
    floats = context.lag(np.array([1.0, 2.0, 3.0]))
    assert np.isnan(floats[0])
    assert list(floats[1:]) == [1.0, 2.0]

    flags = context.lag(np.array([True, True, True]))
    assert not flags[0]
    assert list(flags[1:]) == [True, True]


def test_rolling_min_and_max_span_the_window_ending_at_each_bar():
    values = np.array([5.0, 3.0, 4.0, 1.0])
    assert list(context.rolling_min(values, 2)) == [5.0, 3.0, 3.0, 1.0]
    assert list(context.rolling_max(values, 3)) == [5.0, 5.0, 5.0, 4.0]


def test_a_single_bar_window_is_the_bar_itself():
    values = np.array([5.0, 3.0, 4.0])
    assert list(context.rolling_min(values, 1)) == [5.0, 3.0, 4.0]
