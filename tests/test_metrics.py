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


@pytest.mark.parametrize(
    "returns,expected",
    [([-1.0], 1.0), ([-1.0, -2.0, -3.0], 6.0), ([-2.0, 1.0, 3.0], 2.0), ([1.0, 2.0], 0.0)],
)
def test_max_drawdown_includes_initial_equity_zero(returns, expected):
    rows = [trade(value, entry_index=i) for i, value in enumerate(returns)]
    assert summarise(rows).max_drawdown_r == pytest.approx(expected)


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
    grouped = {("hammer", "1m"): _dated(pattern_r),
               ("random_long", "1m"): _dated(control_r, "random_long")}
    stats = [summarise(ts, cfg, name=name) for (name, _), ts in grouped.items()]
    return {s.pattern: s for s in metrics.attach_baselines(
        stats, cfg, trades_by_key=grouped, rng=np.random.default_rng(0))}


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


def _dated(values, name="hammer", copies=1, offset=0):
    from datetime import timedelta

    return [
        trade(value, trial=trial, pattern=name,
              session=date(2026, 1, 1) + timedelta(days=day + offset))
        for day, value in enumerate(values)
        for trial in range(copies)
    ]


def _inference(pattern_trades, control_trades=None, cfg=None, extra=None, seed=17):
    cfg = cfg or replace(StatsConfig(), min_trades=10, bootstrap_samples=2000)
    grouped = {("hammer", "1m"): pattern_trades}
    if control_trades is not None:
        grouped[("random_long", "1m")] = control_trades
    if extra:
        grouped.update(extra)
    rows = [summarise(ts, cfg, name=name) for (name, interval), ts in grouped.items()]
    result = metrics.attach_baselines(rows, cfg, trades_by_key=grouped,
                                     rng=np.random.default_rng(seed))
    return {row.pattern: row for row in result}


def test_one_date_repeated_trials_cannot_establish_independent_evidence():
    cfg = replace(StatsConfig(), bootstrap_samples=400)
    stats = summarise([trade(2.0, trial=i) for i in range(100)], cfg)
    assert stats.ci_low is None
    assert stats.verdict == "INSUFFICIENT"
    assert stats.sessions == 1


def test_same_date_replication_does_not_artificially_narrow_interval():
    values = [-3.0, 4.0] * 10
    ordinary = summarise(_dated(values))
    replicated = summarise(_dated(values, copies=50))
    assert (replicated.ci_low, replicated.ci_high) == pytest.approx(
        (ordinary.ci_low, ordinary.ci_high))


def test_clustered_expectancy_is_trade_weighted_rather_than_daily_mean():
    ts = _dated([1.0] * 10) + _dated([-1.0], copies=90)
    stats = summarise(ts)
    assert stats.expectancy_r == pytest.approx(-0.8)
    # Reproduce the date draws independently, then divide sampled R totals by
    # sampled trade counts. Averaging the daily means would give different bounds.
    multiplicities = np.random.default_rng(0).multinomial(10, [0.1] * 10, size=200)
    sums = np.array([-89.0] + [1.0] * 9)
    counts = np.array([91.0] + [1.0] * 9)
    expected = np.quantile((multiplicities @ sums) / (multiplicities @ counts), [0.025, 0.975])
    assert (stats.ci_low, stats.ci_high) == pytest.approx(expected)


def test_paired_bootstrap_cancels_shared_date_shocks():
    shocks = [-10.0, 10.0] * 15
    out = _inference(_dated([s + 3 for s in shocks]),
                     _dated(shocks, "random_long"))["hammer"]
    assert out.baseline_delta_r == pytest.approx(3.0)
    assert out.baseline_ci_low == pytest.approx(3.0)
    assert out.baseline_ci_high == pytest.approx(3.0)
    assert out.p_delta < 0.01
    assert out.sessions == out.baseline_sessions == 30


def test_control_uncertainty_changes_paired_delta_interval():
    fixed = _inference(_dated([1.0] * 20),
                       _dated([0.0] * 20, "random_long"))["hammer"]
    varied = _inference(_dated([1.0] * 20),
                        _dated([-8.0, 8.0] * 10, "random_long"))["hammer"]
    assert fixed.baseline_delta_r == varied.baseline_delta_r == 1.0
    assert fixed.verdict == "EDGE"
    assert varied.baseline_ci_low < 0 < varied.baseline_ci_high
    assert varied.verdict == "NOISE"


def test_missing_and_thin_controls_cannot_establish_edge():
    ts = _dated([2.0] * 40)
    assert _inference(ts)["hammer"].verdict == "INSUFFICIENT"
    thin = _inference(ts, _dated([0.0] * 5, "random_long"))["hammer"]
    assert thin.verdict == "INSUFFICIENT"
    assert thin.baseline_sessions == 5


def test_unpaired_dates_cannot_meet_common_session_floor():
    out = _inference(_dated([2.0] * 20),
                     _dated([0.0] * 20, "random_long", offset=20))["hammer"]
    assert out.verdict == "INSUFFICIENT"


def test_positive_controls_never_have_edge_verdict():
    out = _inference(_dated([3.0] * 40), _dated([2.0] * 40, "random_long"))
    assert out["random_long"].verdict == "NOISE"
    assert out["random_long"].p_delta is None


def test_holm_correction_includes_unavailable_hypotheses_and_experiments():
    adjusted = metrics.holm_adjust([0.01, 0.02, 1.0], experiment_count=2)
    assert adjusted == pytest.approx([0.06, 0.08, 1.0])
    assert metrics.holm_adjust([0.02, 0.01]) == pytest.approx([0.02, 0.02])


def test_report_holm_family_contains_expectancy_and_pattern_delta():
    out = _inference(_dated([3.0] * 40), _dated([0.0] * 40, "random_long"))
    assert out["hammer"].p_expectancy_adjusted == pytest.approx(
        3 * out["hammer"].p_expectancy)
    assert out["hammer"].p_delta_adjusted == pytest.approx(3 * out["hammer"].p_delta)


def test_corrected_negative_requires_significance_not_only_pointwise_interval():
    cfg = replace(StatsConfig(), min_trades=10, bootstrap_samples=200,
                  experiment_count=100)
    out = _inference(_dated([-1.0] * 40), cfg=cfg)["hammer"]
    assert out.ci_high < 0
    assert out.p_expectancy_adjusted > 0.05
    assert out.verdict != "NEGATIVE"


def test_inference_reproducible_under_fixed_seed():
    ts = _dated([-1.0, 2.0] * 20)
    cs = _dated([-0.5, 0.5] * 20, "random_long")
    assert _inference(ts, cs, seed=8) == _inference(ts, cs, seed=8)


def test_legacy_attach_without_trade_evidence_cannot_establish_edge():
    cfg = replace(StatsConfig(), min_trades=10, bootstrap_samples=400)
    rows = [summarise(_dated([2.0] * 40), cfg),
            summarise(_dated([0.0] * 40, "random_long"), cfg, name="random_long")]
    out = metrics.attach_baselines(rows, cfg)
    assert out[0].baseline_delta_r == 2.0
    assert out[0].verdict != "EDGE"


def test_explicit_zero_trade_dates_remain_in_shared_resampling_universe():
    from datetime import timedelta

    cfg = replace(StatsConfig(), min_trades=10, bootstrap_samples=200)
    grouped = {("hammer", "1m"): _dated([1.0, 3.0] * 7 + [1.0]),
               ("random_long", "1m"): _dated([-1.0, 1.0] * 10, "random_long")}
    dates = [date(2026, 1, 1) + timedelta(days=d) for d in range(30)]
    rows = [summarise(ts, cfg, name=name) for (name, _), ts in grouped.items()]
    result = metrics.attach_baselines(rows, cfg, trades_by_key=grouped,
                                     sessions=dates, rng=np.random.default_rng(12))[0]
    multiplicities = np.random.default_rng(12).multinomial(30, [1 / 30] * 30, size=200)
    pattern_counts = np.array([1.0] * 15 + [0.0] * 15)
    control_counts = np.array([1.0] * 20 + [0.0] * 10)
    pattern_sums = np.array([1.0, 3.0] * 7 + [1.0] + [0.0] * 15)
    control_sums = np.array([-1.0, 1.0] * 10 + [0.0] * 10)
    pattern_draws = (multiplicities @ pattern_sums) / (multiplicities @ pattern_counts)
    control_draws = (multiplicities @ control_sums) / (multiplicities @ control_counts)
    assert (result.baseline_ci_low, result.baseline_ci_high) == pytest.approx(
        np.quantile(pattern_draws - control_draws, [0.025, 0.975]))
    assert (result.ci_low, result.ci_high) == pytest.approx(
        np.quantile(pattern_draws, [0.025, 0.975]))
    assert result.sessions == 15
    assert result.baseline_sessions == 20
    assert result.paired_sessions == 15


def test_unavailable_rows_still_increase_multiple_comparison_family():
    ts = _dated([2.0] * 40)
    cs = _dated([0.0] * 40, "random_long")
    regular = _inference(ts, cs)["hammer"]
    larger = _inference(ts, cs, extra={("dragonfly_doji", "1m"): []})["hammer"]
    assert larger.p_expectancy_adjusted == pytest.approx(5 * larger.p_expectancy)
    assert larger.p_expectancy_adjusted > regular.p_expectancy_adjusted


def test_validation_family_size_cannot_shrink_after_candidate_selection():
    cfg = replace(StatsConfig(), min_trades=10, bootstrap_samples=2000)
    grouped = {("hammer", "1m"): _dated([2.0] * 40),
               ("random_long", "1m"): _dated([0.0] * 40, "random_long")}
    rows = [summarise(ts, cfg, name=name) for (name, _), ts in grouped.items()]
    result = metrics.attach_baselines(rows, cfg, trades_by_key=grouped,
                                     family_hypotheses=10)[0]
    assert result.p_expectancy_adjusted == pytest.approx(10 * result.p_expectancy)
    with pytest.raises(ValueError, match="smaller"):
        metrics.attach_baselines(rows, cfg, trades_by_key=grouped, family_hypotheses=2)


def test_many_repeated_trades_use_bounded_date_batches():
    class RecordingGenerator:
        def __init__(self):
            self.rng = np.random.default_rng(0)
            self.calls = []

        def multinomial(self, n, pvals, size):
            self.calls.append((n, size))
            return self.rng.multinomial(n, pvals, size=size)

    rng = RecordingGenerator()
    cfg = replace(StatsConfig(), min_trades=10, bootstrap_samples=3000)
    result = metrics.summarise(patterns.get("hammer"), "1m",
                               _dated([-1.0, 2.0] * 10, copies=1000),
                               20000, cfg, rng)
    assert result.trades == 20000
    assert result.sessions == 20
    assert result.ci_low < result.expectancy_r < result.ci_high
    assert len(rng.calls) > 1
    assert all(dates == 20 and batch <= 256 for dates, batch in rng.calls)


def test_pooling_symbols_and_timeframes_preserves_market_date_clusters():
    base = _dated([-3.0, 4.0] * 10)
    pooled = base + [replace(t, symbol="OTHER", interval="5m", trial_index=9) for t in base]
    cfg = replace(StatsConfig(), min_trades=10, bootstrap_samples=400)
    rng = np.random.default_rng(44)
    ordinary = metrics.summarise(patterns.get("hammer"), "all", base, len(base), cfg, rng)
    combined = metrics.summarise(patterns.get("hammer"), "all", pooled, len(pooled), cfg,
                                 np.random.default_rng(44))
    assert combined.sessions == ordinary.sessions == 20
    assert (combined.ci_low, combined.ci_high) == (ordinary.ci_low, ordinary.ci_high)


def test_control_trade_floor_applies_even_with_enough_control_sessions():
    cfg = replace(StatsConfig(), min_trades=30, bootstrap_samples=400)
    out = _inference(_dated([2.0] * 40), _dated([0.0] * 20, "random_long"), cfg)["hammer"]
    assert out.sessions == 40
    assert out.baseline_sessions == 20
    assert out.verdict == "INSUFFICIENT"
    assert out.p_delta is None
    assert out.p_delta_adjusted == 1.0


def test_experiment_correction_changes_significance_without_changing_pointwise_intervals():
    cfg = replace(StatsConfig(), min_trades=10, bootstrap_samples=400)
    ts = _dated([2.0] * 40)
    cs = _dated([0.0] * 40, "random_long")
    ordinary = _inference(ts, cs, cfg)["hammer"]
    corrected = _inference(ts, cs, replace(cfg, experiment_count=10))["hammer"]
    assert (corrected.ci_low, corrected.ci_high, corrected.baseline_ci_low,
            corrected.baseline_ci_high) == (ordinary.ci_low, ordinary.ci_high,
                                            ordinary.baseline_ci_low, ordinary.baseline_ci_high)
    assert corrected.p_expectancy == ordinary.p_expectancy
    assert corrected.p_expectancy_adjusted == pytest.approx(10 * ordinary.p_expectancy_adjusted)
    assert ordinary.verdict == "EDGE"
    assert corrected.verdict == "NOISE"


def test_frequent_zero_denominator_draws_make_inference_unavailable():
    from datetime import timedelta

    cfg = replace(StatsConfig(), min_trades=30, min_sessions=2, bootstrap_samples=1000)
    ts = _dated([2.0, 2.0], copies=20)
    cs = _dated([0.0, 0.0], "random_long", copies=20)
    grouped = {("hammer", "1m"): ts, ("random_long", "1m"): cs}
    rows = [summarise(ts, cfg, name=name) for (name, _), ts in grouped.items()]
    empty_dates = [date(2026, 1, 1) + timedelta(days=i) for i in range(200)]
    out = metrics.attach_baselines(rows, cfg, trades_by_key=grouped,
                                   sessions=empty_dates, rng=np.random.default_rng(7))[0]
    assert out.expectancy_r == 2.0
    assert out.ci_low is None
    assert out.p_expectancy is None
    assert out.p_expectancy_adjusted == 1.0
    assert out.verdict == "INSUFFICIENT"


def test_the_default_family_has_252_hypotheses():
    """Twenty patterns test expectancy and control advantage, two controls test expectancy,
    at five intervals plus the pooled view."""
    from candlebench import patterns

    kinds = [spec.kind for spec in patterns.registry().values()]
    assert metrics.discovery_family_size(kinds, 5) == 252


def test_an_unreachable_threshold_is_reported_before_any_verdict():
    """At the defaults, declaring a second experiment makes every pass impossible.

    252 hypotheses x 2 experiments / 10,001 draws floors the corrected p-value
    at 0.0504, so the run would report NOISE everywhere for arithmetic reasons
    while appearing to have tested something.
    """
    assert metrics.resolution_warning(252, 10_000, 1) is None
    message = metrics.resolution_warning(252, 10_000, 2)
    assert "0.0504" in message and "10,079" in message
    assert metrics.resolution_warning(252, 10_079, 2) is None
