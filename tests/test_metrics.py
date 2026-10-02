"""Statistics tests.

The verdict a pattern receives is the whole output of this tool, so the
boundary between EDGE, NOISE, NEGATIVE and INSUFFICIENT is pinned explicitly.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import numpy as np
import pytest

from candlebench import metrics, patterns
from candlebench.config import StatsConfig
from candlebench.engine import Trade


def trade(
    net_r: float,
    trial: int = 0,
    pattern: str = "hammer",
    reason: str = "stop",
    session: date = date(2026, 9, 15),
    entry_index: int = 1,
) -> Trade:
    return Trade(
        pattern=pattern, interval="1m", symbol="TEST", session=session,
        trial_index=trial, direction=1,
        entry_index=entry_index, exit_index=entry_index + 2,
        entry_price=100.0, exit_price=100.0,
        stop_price=99.0, target_price=102.0, risk_per_share=1.0,
        exit_reason=reason, gross_r=net_r, net_r=net_r, return_pct=net_r / 100,
    )


def summarise(trades, stats_cfg=None, name="hammer", signals=None):
    cfg = stats_cfg or replace(StatsConfig(), bootstrap_samples=200, min_trades=2)
    rng = np.random.default_rng(0)
    return metrics.summarise(
        patterns.get(name), "1m", trades, signals if signals is not None else len(trades), cfg, rng
    )


def test_expectancy_is_the_mean_r():
    stats = summarise([trade(2.0), trade(-1.0), trade(-1.0), trade(2.0)])
    assert stats.expectancy_r == pytest.approx(0.5)
    assert stats.win_rate == pytest.approx(0.5)


def test_win_rate_and_expectancy_can_disagree():
    """The reason expectancy is the headline and win rate is not.

    Nine small wins and one large loss is a 90% win rate that loses money.
    """
    stats = summarise([trade(0.5)] * 9 + [trade(-9.0)])
    assert stats.win_rate == pytest.approx(0.9)
    assert stats.expectancy_r < 0


def test_profit_factor_is_gross_wins_over_gross_losses():
    stats = summarise([trade(3.0), trade(-1.0), trade(-2.0)])
    assert stats.profit_factor == pytest.approx(1.0)


def test_profit_factor_is_infinite_with_no_losses():
    stats = summarise([trade(1.0), trade(2.0)])
    assert stats.profit_factor == float("inf")


def test_max_drawdown_is_the_deepest_decline_of_the_r_curve():
    # cumulative: 2, 1, -1, 1  -> peak 2, trough -1
    stats = summarise([trade(2.0), trade(-1.0), trade(-2.0), trade(2.0)])
    assert stats.max_drawdown_r == pytest.approx(3.0)


def test_max_drawdown_orders_trades_chronologically():
    """Trials are drawn in random session order, not time order.

    Measuring drawdown over the accumulation order would describe the draw
    rather than the pattern: the same trades in a different shuffle would
    report a different drawdown.
    """
    sep = date(2026, 9, 1)
    oct_ = date(2026, 10, 1)
    # Chronologically: +2 (Sep), then -1, -2 (Oct) -> peak 2, trough -1, so 3.
    shuffled = [
        trade(-1.0, session=oct_, entry_index=1),
        trade(2.0, session=sep, entry_index=1),
        trade(-2.0, session=oct_, entry_index=2),
    ]
    assert summarise(shuffled).max_drawdown_r == pytest.approx(3.0)

    # The reverse input order must give the identical answer.
    assert summarise(list(reversed(shuffled))).max_drawdown_r == pytest.approx(3.0)


def test_a_single_trade_reports_sharpe_as_unavailable():
    """Unavailable is not zero.

    Reporting 0 for a pattern with one trade invents a number, and the existing
    stock.py already distinguishes the two cases.
    """
    stats = summarise([trade(1.0)], replace(StatsConfig(), min_trades=1, bootstrap_samples=50))
    assert stats.trades == 1
    assert stats.sharpe_per_trade is None
    assert stats.ci_low is None


def test_no_trades_reports_insufficient_rather_than_zero():
    stats = summarise([])
    assert stats.trades == 0
    assert stats.expectancy_r is None
    assert stats.verdict == "INSUFFICIENT"


def test_signals_are_counted_separately_from_trades():
    """Signals rejected for a too-close stop still happened."""
    stats = summarise([trade(1.0), trade(-1.0)], signals=9)
    assert stats.signals == 9
    assert stats.trades == 2


def test_bootstrap_is_reproducible_under_a_fixed_seed():
    r = np.array([2.0, -1.0, -1.0, 2.0, -1.0, 3.0])
    a = metrics.bootstrap_ci(r, 500, np.random.default_rng(7))
    b = metrics.bootstrap_ci(r, 500, np.random.default_rng(7))
    assert a == b


def test_bootstrap_interval_brackets_the_mean():
    r = np.array([2.0, -1.0, -1.0, 2.0, -1.0, 3.0])
    low, high = metrics.bootstrap_ci(r, 2000, np.random.default_rng(1))
    assert low < r.mean() < high


def test_consistency_counts_only_trials_with_enough_trades():
    """A trial with one lucky trade is not a consistent trial."""
    trades = (
        [trade(1.0, trial=0), trade(1.0, trial=0), trade(1.0, trial=0)]
        + [trade(-1.0, trial=1), trade(-1.0, trial=1), trade(-1.0, trial=1)]
        + [trade(5.0, trial=2)]  # below min_trades_per_trial, so excluded
    )
    cfg = replace(StatsConfig(), min_trades=2, min_trades_per_trial=3, bootstrap_samples=200)
    assert summarise(trades, cfg).consistency == pytest.approx(0.5)


def test_consistency_is_unavailable_when_no_trial_qualifies():
    cfg = replace(StatsConfig(), min_trades=1, min_trades_per_trial=5, bootstrap_samples=200)
    assert summarise([trade(1.0)], cfg).consistency is None


def _with_baseline(pattern_r: list[float], control_r: list[float], min_trades: int = 2):
    cfg = replace(StatsConfig(), min_trades=min_trades, bootstrap_samples=400)
    stats = [
        summarise([trade(v) for v in pattern_r], cfg, name="hammer"),
        summarise([trade(v, pattern="random_long") for v in control_r], cfg, name="random_long"),
    ]
    return {s.pattern: s for s in metrics.attach_baselines(stats, cfg)}


def test_a_pattern_beating_its_control_with_a_positive_interval_is_an_edge():
    out = _with_baseline([2.0] * 40, [-0.1] * 40)
    assert out["hammer"].verdict == "EDGE"
    assert out["hammer"].baseline_delta_r == pytest.approx(2.1)


def test_a_pattern_whose_interval_straddles_zero_is_noise():
    """Most patterns at most timeframes land here, and saying so is the point."""
    # Mean exactly zero at a 2:1 reward and a one-in-three win rate: the
    # break-even case, which is where a pattern with no edge actually sits.
    out = _with_baseline([2.0, -1.0, -1.0] * 20, [-0.1] * 40)
    assert out["hammer"].ci_low < 0 < out["hammer"].ci_high
    assert out["hammer"].verdict == "NOISE"


def test_a_reliably_losing_pattern_is_negative():
    out = _with_baseline([-1.0] * 40, [0.0] * 40)
    assert out["hammer"].verdict == "NEGATIVE"


def test_a_pattern_that_cannot_beat_its_control_is_not_an_edge():
    """A positive interval is not enough; the control is the reference line."""
    out = _with_baseline([0.5] * 40, [0.9] * 40)
    assert out["hammer"].ci_low > 0
    assert out["hammer"].baseline_delta_r < 0
    assert out["hammer"].verdict == "NOISE"


def test_too_few_trades_is_insufficient_regardless_of_how_good_they_look():
    out = _with_baseline([3.0] * 5, [0.0] * 40, min_trades=30)
    assert out["hammer"].verdict == "INSUFFICIENT"


def test_a_control_has_no_baseline_of_its_own():
    out = _with_baseline([1.0] * 40, [0.0] * 40)
    assert out["random_long"].baseline_delta_r is None


def test_missing_controls_are_detected():
    cfg = replace(StatsConfig(), min_trades=1, bootstrap_samples=100)
    only_patterns = [summarise([trade(1.0), trade(2.0)], cfg)]
    assert metrics.controls_missing(only_patterns)
