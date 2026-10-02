"""Rendering tests.

The report is the product. These pin the two things a reader could be misled
by: where unmeasurable rows sort, and what the table says when trading costs
swamp every pattern at an interval.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from candlebench import leaderboard
from candlebench.config import Config, RunConfig
from candlebench.metrics import PatternStats
from candlebench.runner import RunResult


def stat(pattern, **kw) -> PatternStats:
    base = dict(
        pattern=pattern, interval="1m", bias="bull", kind="pattern", signals=100,
        trades=100, win_rate=0.4, expectancy_r=0.1, expectancy_r_gross=0.12,
        total_return_pct=1.0, profit_factor=1.1, sharpe_per_trade=0.2,
        max_drawdown_r=3.0, avg_bars_held=4.0, exit_mix={"stop": 1.0},
        consistency=0.5, ci_low=0.01, ci_high=0.2, baseline_delta_r=0.05,
        verdict="EDGE",
    )
    return PatternStats(**{**base, **kw})


def _result(stats) -> tuple[RunResult, Config]:
    config = Config(
        run=replace(RunConfig(), intervals=("1m",), trials=5, seed=1),
        patterns=tuple(s.pattern for s in stats),
    )
    result = RunResult(
        stats=stats, trials=[], symbols=("AAA",),
        sessions_evaluated=5, skipped_sessions=0, warnings=[],
    )
    return result, config


@pytest.fixture
def cost_dominated_result():
    return _result(
        [
            stat("random_long", kind="control", verdict="NEGATIVE",
                 expectancy_r=-0.3, ci_low=-0.5, ci_high=-0.1, baseline_delta_r=None),
            stat("hammer", verdict="NEGATIVE", expectancy_r=-0.2,
                 ci_low=-0.4, ci_high=-0.05, baseline_delta_r=0.1),
        ]
    )


@pytest.fixture
def healthy_result():
    return _result(
        [
            stat("random_long", kind="control", verdict="NOISE",
                 expectancy_r=0.0, ci_low=-0.1, ci_high=0.1, baseline_delta_r=None),
            stat("hammer", verdict="EDGE", baseline_delta_r=0.1),
        ]
    )


def test_unmeasurable_rows_sort_last_rather_than_being_dropped():
    """A pattern that produced nothing is itself a finding."""
    rows = [
        stat("quiet", trades=0, verdict="INSUFFICIENT", ci_low=None, expectancy_r=None),
        stat("busy", ci_low=0.01),
    ]
    assert [s.pattern for s in leaderboard.rank(rows, "ci_low")] == ["busy", "quiet"]


def test_ranking_prefers_the_better_interval_lower_bound():
    """A modest well-evidenced edge outranks a large unreliable one."""
    rows = [
        stat("wild", expectancy_r=0.9, ci_low=-0.4, ci_high=2.2),
        stat("steady", expectancy_r=0.2, ci_low=0.1, ci_high=0.3),
    ]
    assert [s.pattern for s in leaderboard.rank(rows, "ci_low")] == ["steady", "wild"]


def test_a_negative_control_marks_the_interval_as_cost_dominated():
    rows = [
        stat("random_long", kind="control", verdict="NEGATIVE", ci_high=-0.1),
        stat("hammer", verdict="NEGATIVE", ci_high=-0.05),
    ]
    assert leaderboard.cost_dominated(rows)


def test_a_profitable_control_is_not_cost_dominated():
    rows = [stat("random_long", kind="control", verdict="NOISE")]
    assert not leaderboard.cost_dominated(rows)


def test_the_report_explains_a_cost_dominated_interval(cost_dominated_result):
    """Otherwise every pattern reads NEGATIVE and the verdict stops informing.

    A NEGATIVE pattern with a positive delta has signal the costs consumed,
    which a reader comparing verdicts alone would miss entirely.
    """
    result, config = cost_dominated_result
    text = leaderboard.render(result, config)
    assert "random entry itself loses here" in text
    assert "vs ctrl" in text


def test_the_report_omits_the_note_when_costs_do_not_dominate(healthy_result):
    result, config = healthy_result
    assert "random entry itself loses here" not in leaderboard.render(result, config)


def test_the_default_table_shows_the_control_delta(healthy_result):
    """The headline comparison belongs in the default view, not behind -v."""
    result, config = healthy_result
    assert "vs ctrl" in leaderboard.render(result, config)
