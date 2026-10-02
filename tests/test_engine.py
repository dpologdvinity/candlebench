"""Trade simulation tests.

The exit rules are the part of this system most able to produce a flattering
result quietly, so each one is pinned with exact prices rather than a sign
check.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import numpy as np
import pytest

from candlebench import engine, patterns
from tests.conftest import geometry

SESSION = date(2026, 9, 15)

BULL = "hammer"
BEAR = "hanging_man"

# A signal bar with a low of 99.0, which sets the stop for a long.
SIGNAL = (100.0, 100.25, 99.0, 100.2)
# A signal bar with a high of 101.0, which sets the stop for a short.
SIGNAL_SHORT = (100.0, 101.0, 99.95, 100.2)

QUIET = (100.0, 100.5, 99.5, 100.2)


def mask_at(n: int, *indices: int) -> np.ndarray:
    mask = np.zeros(n, dtype=bool)
    for i in indices:
        mask[i] = True
    return mask


def run(rows, index, spec_name, trade, costs, **overrides):
    geom = geometry(rows, lookback=2)
    spec = patterns.get(spec_name)
    return engine.simulate(
        geom,
        mask_at(len(rows), index),
        spec,
        replace(trade, **overrides),
        costs,
        symbol="TEST",
        interval="1m",
        session=SESSION,
        trial_index=0,
    )


def test_entry_is_the_next_bar_open(trade, free):
    """Never the signal bar's close, which the signal did not yet know."""
    rows = [SIGNAL, (99.8, 100.5, 99.5, 100.2), QUIET]
    (t,) = run(rows, 0, BULL, trade, free)
    assert t.entry_price == pytest.approx(99.8)
    assert t.entry_index == 1


def test_stop_is_the_pattern_extreme_padded_by_the_buffer(trade, free):
    rows = [SIGNAL, QUIET, QUIET]
    (t,) = run(rows, 0, BULL, trade, free, stop_buffer=0.01)
    assert t.stop_price == pytest.approx(99.0 * 0.99)


def test_target_is_risk_times_the_reward_multiple(trade, free):
    rows = [SIGNAL, QUIET, QUIET]
    (t,) = run(rows, 0, BULL, trade, free, stop_buffer=0.0, reward_multiple=3.0)
    risk = 100.0 - 99.0
    assert t.target_price == pytest.approx(100.0 + 3.0 * risk)


def test_stop_exit_fills_at_the_stop(trade, free):
    rows = [SIGNAL, QUIET, (100.0, 100.3, 98.0, 98.5)]
    (t,) = run(rows, 0, BULL, trade, free, stop_buffer=0.0)
    assert t.exit_reason == "stop"
    assert t.exit_price == pytest.approx(99.0)
    assert t.net_r == pytest.approx(-1.0)


def test_target_exit_fills_at_the_target(trade, free):
    rows = [SIGNAL, QUIET, (100.2, 102.5, 100.1, 102.4)]
    (t,) = run(rows, 0, BULL, trade, free, stop_buffer=0.0, reward_multiple=2.0)
    assert t.exit_reason == "target"
    assert t.exit_price == pytest.approx(102.0)
    assert t.net_r == pytest.approx(2.0)


def test_a_bar_touching_both_levels_is_recorded_as_a_stop(trade, free):
    """A bar cannot say which level came first, so assume the worse one.

    Resolving the ambiguity favourably is the single most common way a backtest
    reports an edge that does not exist.
    """
    rows = [SIGNAL, (100.0, 102.5, 98.0, 100.2), QUIET]
    (t,) = run(rows, 0, BULL, trade, free, stop_buffer=0.0)
    assert t.exit_reason == "stop"
    assert t.net_r == pytest.approx(-1.0)


def test_a_gap_through_the_stop_fills_at_the_open(trade, free):
    """Worse than the stop, which is what happens to a real order."""
    rows = [SIGNAL, (100.0, 100.4, 99.6, 100.1), (97.0, 97.2, 96.0, 96.5)]
    (t,) = run(rows, 0, BULL, trade, free, stop_buffer=0.0)
    assert t.exit_reason == "stop"
    assert t.exit_price == pytest.approx(97.0)
    assert t.net_r < -1.0


def test_a_gap_through_the_target_fills_at_the_open(trade, free):
    """Better than the target, which also happens."""
    rows = [SIGNAL, (100.0, 100.4, 99.6, 100.1), (104.0, 104.5, 103.8, 104.2)]
    (t,) = run(rows, 0, BULL, trade, free, stop_buffer=0.0, reward_multiple=2.0)
    assert t.exit_reason == "target"
    assert t.exit_price == pytest.approx(104.0)
    assert t.net_r > 2.0


def test_timeout_closes_at_the_last_held_bar(trade, free):
    rows = [SIGNAL, QUIET, (100.0, 100.4, 99.6, 100.35), QUIET]
    (t,) = run(rows, 0, BULL, trade, free, stop_buffer=0.0, max_hold_bars=2)
    assert t.exit_reason == "timeout"
    assert t.exit_index == 2
    assert t.exit_price == pytest.approx(100.35)


def test_running_out_of_session_closes_at_the_final_bar(trade, free):
    """Distinguished from a timeout, because the cause is different."""
    rows = [SIGNAL, QUIET, (100.0, 100.4, 99.6, 100.3)]
    (t,) = run(rows, 0, BULL, trade, free, stop_buffer=0.0, max_hold_bars=50)
    assert t.exit_reason == "session_end"
    assert t.exit_index == 2


def test_a_signal_on_the_last_bar_produces_no_trade(trade, free):
    """There is no next bar to enter on, and the close is not available to it."""
    rows = [QUIET, QUIET, SIGNAL]
    assert run(rows, 2, BULL, trade, free) == []


def test_short_side_mirrors_the_long_side(trade, free):
    """Stop above the pattern high, profit when price falls."""
    rows = [SIGNAL_SHORT, QUIET, (100.0, 100.3, 97.5, 97.6)]
    (t,) = run(rows, 0, BEAR, trade, free, stop_buffer=0.0, reward_multiple=2.0)
    assert t.direction == -1
    assert t.stop_price == pytest.approx(101.0)
    risk = 101.0 - 100.0
    assert t.target_price == pytest.approx(100.0 - 2 * risk)
    assert t.exit_reason == "target"
    assert t.net_r == pytest.approx(2.0)


def test_a_stop_too_close_to_entry_is_discarded(trade, free):
    """Such a signal would produce an R multiple that swamps every statistic."""
    tight = (100.0, 100.02, 99.999, 100.01)
    rows = [tight, (100.0, 100.5, 99.5, 100.2), QUIET]
    assert run(rows, 0, BULL, trade, free, stop_buffer=0.0, min_risk_pct=0.001) == []


def test_overlapping_signals_are_skipped_while_a_trade_is_open(trade, free):
    """Otherwise one clustered burst of signals dominates the sample."""
    rows = [SIGNAL, QUIET, QUIET, QUIET, (100.0, 100.4, 99.6, 100.3)]
    geom = geometry(rows, lookback=2)
    spec = patterns.get(BULL)
    both = mask_at(len(rows), 0, 1)

    exclusive = engine.simulate(
        geom, both, spec, replace(trade, allow_overlapping_trades=False, max_hold_bars=3),
        free, symbol="TEST", interval="1m", session=SESSION, trial_index=0,
    )
    overlapping = engine.simulate(
        geom, both, spec, replace(trade, allow_overlapping_trades=True, max_hold_bars=3),
        free, symbol="TEST", interval="1m", session=SESSION, trial_index=0,
    )
    assert len(exclusive) == 1
    assert len(overlapping) == 2


def test_slippage_is_charged_against_the_trader_on_both_legs(trade, free):
    """A winner nets less than its gross; costs never improve a fill."""
    from candlebench.config import CostConfig

    rows = [SIGNAL, QUIET, (100.2, 102.5, 100.1, 102.4)]
    costly = CostConfig(slippage_bps=10.0, commission_per_trade=0.0)
    (t,) = run(rows, 0, BULL, trade, costly, stop_buffer=0.0)
    assert t.net_r < t.gross_r
    assert t.entry_price > 100.0
    assert t.exit_price < 102.0


def test_commission_converts_to_r_through_the_position_size(trade, free):
    from candlebench.config import CostConfig

    rows = [SIGNAL, QUIET, (100.0, 100.3, 98.0, 98.5)]
    costs = CostConfig(slippage_bps=0.0, commission_per_trade=5.0)
    (t,) = run(rows, 0, BULL, trade, costs, stop_buffer=0.0, risk_per_trade_usd=100.0)
    assert t.net_r == pytest.approx(-1.05)
    assert t.gross_r == pytest.approx(-1.0)


def test_a_three_bar_pattern_stops_below_the_whole_formation(trade, free):
    """Not merely below its final bar, which would place the stop too close."""
    rows = [
        (101, 101.1, 99.4, 99.5),
        (99.2, 99.3, 98.0, 99.1),  # the formation's low
        (99.4, 100.7, 99.35, 100.6),
        (100.6, 101.0, 100.3, 100.9),
    ]
    geom = geometry(rows, lookback=2)
    (t,) = engine.simulate(
        geom, mask_at(len(rows), 2), patterns.get("morning_star"),
        replace(trade, stop_buffer=0.0), free,
        symbol="TEST", interval="1m", session=SESSION, trial_index=0,
    )
    assert t.stop_price == pytest.approx(98.0)
