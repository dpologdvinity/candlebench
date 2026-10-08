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
from tests import conftest
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


@pytest.mark.parametrize("spec_name, signal, gap_bar, target_fill", [
    # Long: target 102; the bar opens at 103 and later trades down to the stop.
    (BULL, SIGNAL, (103.0, 103.5, 98.0, 98.5), 103.0),
    # Short: target 98; the bar opens at 97 and later trades up to the stop.
    (BEAR, SIGNAL_SHORT, (97.0, 101.5, 96.5, 101.2), 97.0),
])
def test_a_bar_opening_past_the_target_exits_at_the_target_even_if_it_later_hits_the_stop(
    trade, free, spec_name, signal, gap_bar, target_fill
):
    """The open is the one price whose order within the bar is known.

    The stop-wins rule exists because a bar cannot say which level it touched
    first. A bar that opens beyond the target has already said: the resting
    target order fills at the open, and a later trade at the stop is never
    reached. Recording it as a stop invented a loss the order could not take.
    """
    rows = [signal, (100.0, 100.4, 99.6, 100.1), gap_bar]
    (t,) = run(rows, 0, spec_name, trade, free, stop_buffer=0.0, reward_multiple=2.0)
    assert t.exit_reason == "target"
    assert t.exit_price == pytest.approx(target_fill)


def test_a_bar_opening_between_the_levels_and_touching_both_is_still_a_stop(trade, free):
    """Only the open settles the order; a bar opening inside the range stays pessimistic."""
    rows = [SIGNAL, (100.0, 100.4, 99.6, 100.1), (100.5, 102.5, 98.0, 100.2)]
    (t,) = run(rows, 0, BULL, trade, free, stop_buffer=0.0, reward_multiple=2.0)
    assert t.exit_reason == "stop"


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


def test_the_percentage_return_pays_the_same_commission_as_net_r(trade, free):
    """Net R included commission and the percentage return did not.

    A rank by total return then ignored a cost every R statistic charged. Both
    must describe the same trade: return = net R x risk / entry.
    """
    from candlebench.config import CostConfig

    rows = [SIGNAL, QUIET, (100.0, 100.3, 98.0, 98.5)]
    costs = CostConfig(slippage_bps=0.0, commission_per_trade=5.0)
    (t,) = run(rows, 0, BULL, trade, costs, stop_buffer=0.0, risk_per_trade_usd=100.0)
    assert t.return_pct == pytest.approx(t.net_r * t.risk_per_share / t.entry_price)


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


# ---------- a cost that varies within the session ----------


def test_each_leg_is_charged_at_the_bar_it_filled_on(trade, free):
    """The quoted spread at the open runs a median 4.0x midday, up to 7.7x.

    One cost per session would charge a 09:35 entry and a 13:00 exit the same
    thing. Measured, those differ severalfold, and the time-of-day breakdown
    already shows the open is where an apparent intraday edge usually lives, so
    pricing it flat prices exactly the interesting case wrong.

    The bars the trade actually fills on are read from a flat-cost run rather
    than assumed: an earlier version of this test guessed the exit landed on a
    later bar when it landed on the entry bar, and so asserted the wrong thing.
    """
    rows = conftest.prefixed("down", [
        (100.0, 100.2, 99.0, 99.2),
        (100.0, 106.0, 99.9, 105.0),
        (105.0, 112.0, 104.0, 111.0),
    ])
    geom = conftest.geometry(rows)
    mask = np.zeros(len(geom), dtype=bool)
    mask[len(conftest.falling())] = True
    kwargs = dict(symbol="T", interval="1m", session=date(2026, 9, 15), trial_index=0)

    cheap = 0.0001
    flat = engine.simulate(geom, mask, patterns.get("hammer"), trade, free,
                           one_way_cost=cheap, **kwargs)
    assert flat

    def with_dear_bar(index):
        per_bar = np.full(len(geom), cheap)
        per_bar[index] = 0.0010
        return engine.simulate(geom, mask, patterns.get("hammer"), trade, free,
                               one_way_cost=per_bar, **kwargs)[0]

    entry_bar, exit_bar = flat[0].entry_index, flat[0].exit_index

    dear_entry = with_dear_bar(entry_bar)
    assert dear_entry.entry_price > flat[0].entry_price
    assert dear_entry.net_r < flat[0].net_r

    if exit_bar != entry_bar:
        dear_exit = with_dear_bar(exit_bar)
        # Only the exit leg moves: a long sells into the bid, so a wider spread
        # there means a worse exit fill and an unchanged entry.
        assert dear_exit.entry_price == pytest.approx(flat[0].entry_price)
        assert dear_exit.exit_price < flat[0].exit_price
        assert dear_exit.net_r < flat[0].net_r


def test_a_scalar_cost_still_prices_every_bar_the_same(trade, free):
    """The array form must not change what a single number already meant."""
    rows = conftest.prefixed("down", [
        (100.0, 100.2, 99.0, 99.2),
        (100.0, 106.0, 99.9, 105.0),
        (105.0, 112.0, 104.0, 111.0),
    ])
    geom = conftest.geometry(rows)
    mask = np.zeros(len(geom), dtype=bool)
    mask[len(conftest.falling())] = True

    kwargs = dict(symbol="T", interval="1m", session=date(2026, 9, 15), trial_index=0)
    scalar = engine.simulate(geom, mask, patterns.get("hammer"), trade, free,
                             one_way_cost=0.0005, **kwargs)
    array = engine.simulate(geom, mask, patterns.get("hammer"), trade, free,
                            one_way_cost=np.full(len(geom), 0.0005), **kwargs)
    assert scalar[0].entry_price == pytest.approx(array[0].entry_price)
    assert scalar[0].exit_price == pytest.approx(array[0].exit_price)
    assert scalar[0].net_r == pytest.approx(array[0].net_r)


# ---------- matched controls ----------


def _walk(n: int = 40, seed: int = 3):
    rng = np.random.default_rng(seed)
    closes = 100 * np.exp(np.cumsum(rng.normal(0, 0.002, n)))
    opens = np.concatenate(([100.0], closes[:-1]))
    return [(o, max(o, c) + 0.05, min(o, c) - 0.05, c) for o, c in zip(opens, closes)]


def test_a_matched_control_enters_after_its_pattern_never_before(trade, free):
    """Bars before the entry were selected by the pattern itself.

    A control entering there trades through the formation, the trend gate and
    the previous trade's exit. On a random walk, controls entering up to five
    bars early lost 0.10R against the pattern's 0.01R, and five bars before a
    tweezer top 0.56R, which made every pattern look better than its control.
    """
    rows = _walk()
    geom = geometry(rows, lookback=2)
    template = run(rows, 10, BULL, trade, free)
    controls = engine.simulate_matched(geom, template * 50, trade, free, np.random.default_rng(0))
    offsets = {c.entry_index - template[0].entry_index for c in controls}
    first, last = engine.MATCH_ENTRY_BARS
    assert first >= 1
    assert offsets and offsets <= set(range(first, last + 1))


def test_a_later_entry_window_moves_every_control_into_it(trade, free):
    """The window experiment relies on this to compare windows on one market."""
    rows = _walk()
    geom = geometry(rows, lookback=2)
    template = run(rows, 10, BULL, trade, free)
    controls = engine.simulate_matched(geom, template * 50, trade, free,
                                       np.random.default_rng(0), entry_bars=(3, 4))
    offsets = {c.entry_index - template[0].entry_index for c in controls}
    assert offsets == {3, 4}


def test_a_matched_control_keeps_its_patterns_direction_and_stop_distance(trade, free):
    """Same risk from the last close before entry, so the entry gap counts the same way."""
    rows = _walk()
    geom = geometry(rows, lookback=2)
    (pattern,) = run(rows, 10, BULL, trade, free)
    (ctrl,) = engine.simulate_matched(geom, [pattern], trade, free, np.random.default_rng(1))
    reach = lambda t: t.direction * (geom.close[t.entry_index - 1] - t.stop_price)
    assert ctrl.direction == pattern.direction
    assert reach(ctrl) == pytest.approx(reach(pattern))
    assert ctrl.target_price == pytest.approx(
        ctrl.entry_price + ctrl.direction * ctrl.risk_per_share * trade.reward_multiple)


def test_a_control_on_its_patterns_own_bar_reproduces_the_pattern_exactly(trade, free):
    """The control and the pattern share one exit path, so they can differ only by entry."""
    rows = _walk()
    geom = geometry(rows, lookback=2)
    (pattern,) = run(rows, 10, BULL, trade, free)
    replay = engine._execute(
        geom, pattern.entry_index, pattern.direction, float(geom.open[pattern.entry_index]),
        pattern.stop_price, pattern.risk_per_share, trade, engine._slippage(0.0, free), 0.0,
        pattern=BULL, interval="1m", symbol="TEST", session=SESSION, trial_index=0,
        window=0, bar_minutes=None,
    )
    assert replay.gross_r == pytest.approx(pattern.gross_r)
    assert replay.exit_index == pattern.exit_index


def test_a_pattern_entry_on_the_last_bar_has_no_control():
    """With no later bar to enter on, a control is missing rather than invented."""
    from candlebench.config import CostConfig, TradeConfig

    rows = _walk(12)
    geom = geometry(rows, lookback=2)
    cfg = replace(TradeConfig(), allow_overlapping_trades=True)
    trades = run(rows, 10, BULL, cfg, CostConfig(slippage_bps=0.0))
    assert trades and trades[0].entry_index == 11
    assert engine.simulate_matched(geom, trades, cfg, CostConfig(), np.random.default_rng(0)) == []
