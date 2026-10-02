"""The cost model.

`slippage_bps = 1.0` was a flat guess, and the project's headline finding is
that costs exceed whatever edge the patterns have. That made the least evidenced
number in the system the one the conclusion rested on. The Corwin-Schultz
high-low estimator replaces it with a spread measured from the bars already
cached, so the claim becomes a measurement.

The expected values below were computed once from the published formula by hand
rather than from this implementation, so a change in the implementation cannot
quietly redefine what the test checks.
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from candlebench import costs, runner
from candlebench.config import CostConfig
from tests.test_end_to_end import config, write_cache

# One bar pair whose ranges overlap: H/L of 101/99 then 100.5/98.5.
OVERLAPPING = (np.array([101.0, 100.5]), np.array([99.0, 98.5]))
OVERLAPPING_SPREAD = 0.0079485334033943

# A pair with no overlap at all — the second bar's low is above the first bar's
# high. The two-bar range dwarfs both single-bar ranges, which drives alpha
# negative and the raw estimate to -0.228.
DISJOINT = (np.array([100.0, 110.0]), np.array([99.9, 109.9]))


# ---------- the estimator ----------


def test_the_estimator_matches_the_published_formula():
    high, low = OVERLAPPING
    assert costs.corwin_schultz(high, low) == pytest.approx(OVERLAPPING_SPREAD)


def test_the_estimate_is_the_mean_over_adjacent_pairs():
    """Three bars give two pairs; the session estimate averages them."""
    high = np.array([101.0, 100.5, 110.0])
    low = np.array([99.0, 98.5, 109.9])
    assert costs.corwin_schultz(high, low) == pytest.approx(0.00397426670169715)


def test_a_negative_estimate_is_clamped_rather_than_credited():
    """Corwin-Schultz can go negative. A negative spread would pay the trader."""
    high, low = DISJOINT
    assert costs.corwin_schultz(high, low) == 0.0


def test_bars_with_no_range_estimate_nothing():
    flat = np.array([100.0, 100.0, 100.0])
    assert costs.corwin_schultz(flat, flat) == 0.0


def test_a_single_bar_cannot_be_estimated():
    """The estimator compares one-bar ranges against two-bar ranges."""
    assert costs.corwin_schultz(np.array([101.0]), np.array([99.0])) is None


def test_non_finite_or_non_positive_bars_are_ignored():
    """A zero or NaN price would make the log undefined."""
    high = np.array([101.0, 100.5, np.nan, 0.0])
    low = np.array([99.0, 98.5, 98.0, 0.0])
    assert costs.corwin_schultz(high, low) == pytest.approx(OVERLAPPING_SPREAD)


def test_too_few_usable_bars_after_filtering_is_unavailable():
    assert costs.corwin_schultz(np.array([101.0, np.nan]), np.array([99.0, 98.0])) is None


# ---------- what gets charged ----------


def test_the_estimated_model_charges_half_the_round_trip_spread():
    """`slippage_bps` always meant a one-way cost, so the leg pays half."""
    high, low = OVERLAPPING
    fraction, estimate = costs.one_way_fraction(CostConfig(), high, low)
    assert estimate == pytest.approx(OVERLAPPING_SPREAD)
    assert fraction == pytest.approx(OVERLAPPING_SPREAD / 2)


def test_the_fixed_model_ignores_the_bars_entirely():
    high, low = OVERLAPPING
    cfg = CostConfig(model="fixed", slippage_bps=2.0)
    fraction, estimate = costs.one_way_fraction(cfg, high, low)
    assert estimate is None
    assert fraction == pytest.approx(2.0 / 10_000)


def test_an_unavailable_estimate_falls_back_to_the_fixed_cost():
    """Not to zero: an unmeasurable spread is not a free trade."""
    flat = np.array([100.0, 100.0])
    fraction, estimate = costs.one_way_fraction(CostConfig(slippage_bps=1.0), flat, flat)
    assert estimate is None
    assert fraction == pytest.approx(1.0 / 10_000)


def test_a_clamped_negative_estimate_also_falls_back():
    high, low = DISJOINT
    fraction, estimate = costs.one_way_fraction(CostConfig(slippage_bps=1.0), high, low)
    assert estimate is None
    assert fraction == pytest.approx(1.0 / 10_000)


def test_an_unknown_cost_model_is_rejected():
    from candlebench import config as config_module

    cfg = config_module.load()
    with pytest.raises(ValueError, match="costs.model"):
        config_module.validate(replace(cfg, costs=CostConfig(model="vibes")))


# ---------- what the run reports ----------


def test_the_run_reports_the_spread_it_charged(tmp_path):
    """The cost assumption has to be visible, not buried in a config default."""
    write_cache(tmp_path)
    result = runner.run(config(tmp_path))
    assert result.spread_bps > 0
    assert result.spread_interval == "1m"


def test_the_fixed_model_reports_no_estimate(tmp_path):
    write_cache(tmp_path)
    cfg = config(tmp_path)
    cfg = replace(cfg, costs=CostConfig(model="fixed", slippage_bps=1.0))
    assert runner.run(cfg).spread_bps is None


def test_the_same_spread_is_charged_at_every_timeframe(tmp_path):
    """The estimator's answer grows with bar length; a real spread does not.

    Measured over the same symbols and days, Corwin-Schultz returns about 1.8 bps
    at 1m and 15.1 bps at 1h. Letting each timeframe price itself would charge
    roughly ten times as much at 1h and make the coarse intervals
    cost-dominated by construction.
    """
    write_cache(tmp_path, intervals=("1m", "30m"))
    cfg = config(tmp_path)
    fine_first = replace(cfg, run=replace(cfg.run, intervals=("1m", "30m")))
    coarse_first = replace(cfg, run=replace(cfg.run, intervals=("30m", "1m")))

    results = [runner.run(fine_first), runner.run(coarse_first)]
    assert [r.spread_interval for r in results] == ["1m", "1m"]
    # The cost table is keyed by symbol and session only, so there is no key an
    # interval could vary, and listing the intervals in the other order cannot
    # change it either.
    tables = [
        runner._estimate_spreads(c, runner.sampling.draw_trials(
            r.symbols, c.run.intervals, c.cache_path, c.run.trials,
            np.random.default_rng(c.run.seed),
        ))[0]
        for c, r in zip((fine_first, coarse_first), results)
    ]
    assert tables[0] == tables[1]
    assert tables[0]


def test_the_report_names_the_measured_spread_rather_than_the_config(tmp_path):
    from candlebench import leaderboard

    write_cache(tmp_path)
    cfg = config(tmp_path)
    text = leaderboard.render(runner.run(cfg), cfg)
    assert "estimated" in text
    assert "measured from 1m bars" in text


def test_estimating_the_spread_changes_what_a_trade_nets(tmp_path):
    """If the measured cost made no difference, replacing the guess was pointless."""
    write_cache(tmp_path)
    fixed = replace(config(tmp_path), costs=CostConfig(model="fixed", slippage_bps=1.0))
    estimated = replace(config(tmp_path), costs=CostConfig(model="estimated"))

    by_pattern = {
        (s.pattern, s.interval): s.expectancy_r for s in runner.run(fixed).stats
    }
    moved = [
        s for s in runner.run(estimated).stats
        if s.expectancy_r is not None
        and by_pattern[(s.pattern, s.interval)] is not None
        and s.expectancy_r != by_pattern[(s.pattern, s.interval)]
    ]
    assert moved


def test_one_session_is_priced_the_same_way_for_every_pattern(tmp_path):
    """The spread is a property of the session, not of the pattern trading it.

    The runner estimates once per session and hands the same figure to all 22
    detectors, so two patterns entering on the same bar cannot pay different
    costs.
    """
    write_cache(tmp_path)
    result = runner.run(config(tmp_path))
    by_entry: dict[tuple, set[float]] = {}
    for trade in result.trades:
        # Direction is part of the key: a long pays the spread upward and a
        # short downward, so only same-direction fills are comparable.
        key = (
            trade.symbol, trade.session, trade.interval,
            trade.entry_index, trade.direction,
        )
        by_entry.setdefault(key, set()).add(round(trade.entry_price, 10))
    assert by_entry
    assert all(len(prices) == 1 for prices in by_entry.values())


# ---------- an implausible estimate is not a measurement ----------

# Two identical overlapping bars with ln(H/L) = r estimate
# S = 2(e^r - 1)/(1 + e^r) exactly, which is how these are constructed.
JUST_UNDER = (np.array([101.511335, 101.511335]), np.array([100.0, 100.0]))  # S = 1.5%
JUST_OVER = (np.array([103.045685, 103.045685]), np.array([100.0, 100.0]))   # S = 3.0%


def test_the_estimator_itself_reports_what_the_formula_says():
    """The plausibility judgement belongs to the caller, not to the formula.

    `corwin_schultz` stays a faithful implementation so the pinned expected
    values mean something; `one_way_fraction` is where the fallback already
    lives and where a nonsense figure is refused.
    """
    assert costs.corwin_schultz(*JUST_OVER) == pytest.approx(0.03)


def test_an_implausible_spread_falls_back_to_the_fixed_cost():
    """S asymptotes to 2.0 — 200% of price — on degenerate bars.

    Charging that would double the entry price and report it as measured. No
    top-50 liquid US equity has a 2% round-trip spread; above the ceiling the
    estimator has stopped measuring a spread and is reporting its own
    saturation, so the figure is unavailable rather than enormous.
    """
    fraction, estimate = costs.one_way_fraction(
        CostConfig(slippage_bps=1.0), *JUST_OVER
    )
    assert estimate is None
    assert fraction == pytest.approx(1.0 / 10_000)


def test_a_degenerate_bar_pair_cannot_charge_two_hundred_percent():
    saturating = (np.array([1e12, 1e12]), np.array([1e-12, 1e-12]))
    assert costs.corwin_schultz(*saturating) == pytest.approx(2.0)
    fraction, estimate = costs.one_way_fraction(
        CostConfig(slippage_bps=1.0), *saturating
    )
    assert estimate is None
    assert fraction == pytest.approx(1.0 / 10_000)


def test_a_wide_but_plausible_spread_is_still_used():
    """The ceiling must not quietly replace a real measurement with the guess."""
    fraction, estimate = costs.one_way_fraction(CostConfig(), *JUST_UNDER)
    assert estimate == pytest.approx(0.015)
    assert fraction == pytest.approx(0.015 / 2)


def test_the_ceiling_sits_well_above_anything_the_cache_produces():
    """Measured: the worst 1m estimate across 50 symbols is under 50 bps, and
    the worst at any interval is 103 bps. A tight ceiling would substitute the
    flat guess for a real if noisy measurement, which is the opposite of why
    the estimator exists."""
    assert costs.MAX_PLAUSIBLE_SPREAD >= 0.015


def test_a_fallback_from_an_implausible_estimate_is_counted(tmp_path):
    """It has to show up in the run's cost line, not vanish."""
    write_cache(tmp_path)
    cfg = config(tmp_path)
    monkey = costs.MAX_PLAUSIBLE_SPREAD
    try:
        costs.MAX_PLAUSIBLE_SPREAD = 1e-9  # nothing can be plausible
        result = runner.run(cfg)
        assert result.spread_bps is None
        assert result.spread_fallbacks == len(result.trials)
    finally:
        costs.MAX_PLAUSIBLE_SPREAD = monkey


# ---------- the estimator must not read sub-minute bars ----------


def test_the_spread_is_never_estimated_from_sub_minute_bars(tmp_path):
    """Corwin-Schultz collapses toward zero when most bars have no range.

    Measured on the same symbol and day: 2.028 bps round trip from 1m bars and
    0.074 from 1s, a 27-fold understatement, because 30% to 68% of 1s bars have
    open == high == low == close. A 1s run that priced itself from 1s bars would
    charge almost nothing and report every pattern as merely NOISE rather than
    cost-dominated — the finding inverted by a measurement artefact.
    """
    write_cache(tmp_path, intervals=("1s", "1m"))
    cfg = config(tmp_path, intervals=("1s", "1m"), trials=4)
    result = runner.run(cfg)
    assert result.spread_interval == "1m"


def test_a_sub_minute_only_run_falls_back_and_says_why(tmp_path):
    """With nothing but sub-minute intervals there is no honest estimate."""
    from candlebench import leaderboard

    write_cache(tmp_path, intervals=("1s",))
    cfg = config(tmp_path, intervals=("1s",), trials=4)
    result = runner.run(cfg)
    assert result.spread_bps is None
    assert result.spread_interval is None

    note = [w for w in result.warnings if "sub-minute" in w and "spread" in w]
    assert note, result.warnings
    assert "fixed" in note[0]
    assert "fixed slippage" in leaderboard.describe_costs(result, cfg)
