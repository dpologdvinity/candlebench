"""Walk-forward windows.

Every trial used to be drawn from one pooled set of sessions, so a result could
not be split by period at all. A pattern that made money in the first half of
the cache and lost it in the second was indistinguishable from one that made it
steadily, and only the second has an edge worth the name.

`consistency` already reports the share of individual trials that were positive.
`stability` is the different and harder question: whether the sign survives from
one stretch of calendar time to the next.
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from candlebench import bars, runner, sampling
from candlebench.config import RunConfig, StatsConfig, validate
from tests.test_end_to_end import SESSIONS, SYMBOLS, config, write_cache
from tests.test_metrics import summarise, trade


def draw(cache_dir, trials=6, windows=1, seed=11):
    return sampling.draw_trials(
        SYMBOLS, ("1m",), cache_dir, trials, np.random.default_rng(seed), windows=windows
    )


# ---------- drawing ----------


def test_one_window_draws_exactly_what_it_drew_before(tmp_path):
    """A changed draw would silently move every result recorded until now."""
    write_cache(tmp_path)
    rng = np.random.default_rng(11)
    available = bars.available_sessions(SYMBOLS, "1m", tmp_path)
    pool = [(symbol, day) for symbol, days in sorted(available.items()) for day in days]
    chosen = rng.choice(len(pool), size=6, replace=6 > len(pool))
    seeds = rng.integers(0, 2**32 - 1, size=6)
    expected = [(pool[int(k)][0], pool[int(k)][1], int(s)) for k, s in zip(chosen, seeds)]

    drawn = draw(tmp_path, trials=6, windows=1)
    assert [(t.symbol, t.session, t.seed) for t in drawn] == expected
    assert {t.window for t in drawn} == {0}


def test_every_trial_is_tagged_with_its_window(tmp_path):
    write_cache(tmp_path)
    drawn = draw(tmp_path, trials=6, windows=3)
    assert len(drawn) == 6
    assert {t.window for t in drawn} == {0, 1, 2}
    assert [t.index for t in drawn] == list(range(6))


def test_the_trials_are_split_evenly_across_windows(tmp_path):
    write_cache(tmp_path)
    counts = {}
    for t in draw(tmp_path, trials=7, windows=3):
        counts[t.window] = counts.get(t.window, 0) + 1
    # Seven into three: the remainder goes to the earliest windows.
    assert counts == {0: 3, 1: 2, 2: 2}


def test_windows_are_chronological_and_do_not_share_a_session(tmp_path):
    """Overlapping windows would make a held-out period not held out."""
    write_cache(tmp_path)
    drawn = draw(tmp_path, trials=6, windows=3)
    sessions_by_window: dict[int, set] = {}
    for t in drawn:
        sessions_by_window.setdefault(t.window, set()).add(t.session)

    ordered = [sessions_by_window[w] for w in sorted(sessions_by_window)]
    for earlier, later in zip(ordered, ordered[1:]):
        assert earlier.isdisjoint(later)
        assert max(earlier) < min(later)


def test_the_cache_must_hold_a_session_for_every_window(tmp_path):
    """Three sessions cannot be split into four windows."""
    write_cache(tmp_path)
    with pytest.raises(RuntimeError, match="fewer cached sessions"):
        draw(tmp_path, trials=8, windows=len(SESSIONS) + 1)


def test_a_run_carries_the_window_onto_every_trade(tmp_path):
    write_cache(tmp_path)
    cfg = config(tmp_path, trials=6, windows=3)
    result = runner.run(cfg)
    assert {t.window for t in result.trials} == {0, 1, 2}

    window_of = {t.index: t.window for t in result.trials}
    assert result.trades
    assert all(t.window == window_of[t.trial_index] for t in result.trades)


def test_a_multi_bar_pattern_records_its_window_not_its_bar_count(tmp_path):
    """Regression: `simulate` had a local `window = spec.bars_required`.

    It shadowed the new parameter, so every trade recorded the number of bars
    its pattern needed — 1, 2 or 3 — in place of the walk-forward window it was
    drawn from. Every window statistic was then grouping patterns by shape.
    """
    write_cache(tmp_path)
    cfg = config(tmp_path, trials=4, windows=2)
    result = runner.run(cfg)
    multi = [t for t in result.trades if t.pattern in ("bullish_engulfing", "bearish_engulfing")]
    assert multi
    assert {t.window for t in multi} <= {0, 1}


# ---------- configuration ----------


@pytest.mark.parametrize("windows,message", [(0, "at least 1"), (9, "more windows")])
def test_an_impossible_window_count_is_rejected(windows, message):
    from candlebench import config as config_module

    cfg = config_module.load()
    broken = replace(cfg, run=replace(RunConfig(), trials=8, windows=windows))
    with pytest.raises(ValueError, match=message):
        validate(broken)


# ---------- statistics ----------


def test_one_window_cannot_show_stability():
    """A single stretch of time says nothing about whether a sign persists."""
    stats = summarise([trade(1.0, trial=i) for i in range(10)])
    assert stats.stability is None


def test_a_sign_that_flips_between_windows_is_half_stable():
    rows = [trade(2.0, trial=i) for i in range(5)] + [
        replace(trade(-2.0, trial=5 + i), window=1) for i in range(5)
    ]
    stats = summarise(rows)
    assert stats.stability == pytest.approx(0.5)
    assert stats.window_expectancy_r[0] == pytest.approx(2.0)
    assert stats.window_expectancy_r[1] == pytest.approx(-2.0)


def test_every_window_positive_is_fully_stable():
    rows = [replace(trade(1.0, trial=i), window=i % 3) for i in range(12)]
    stats = summarise(rows)
    assert stats.stability == pytest.approx(1.0)
    assert set(stats.window_expectancy_r) == {0, 1, 2}


def test_a_window_with_too_few_trades_does_not_count():
    """Otherwise one lucky trade in a thin window reads as a stable period."""
    cfg = replace(StatsConfig(), bootstrap_samples=200, min_trades=2, min_trades_per_trial=3)
    rows = [replace(trade(-1.0, trial=i), window=0) for i in range(5)]
    rows += [replace(trade(5.0, trial=9), window=1)]  # one trade, below the floor
    stats = summarise(rows, stats_cfg=cfg)
    assert set(stats.window_expectancy_r) == {0}
    assert stats.stability is None


def test_the_report_says_the_windows_are_not_separate_regimes(tmp_path):
    """28-59 days of history is one regime however it is sliced."""
    from candlebench import leaderboard

    write_cache(tmp_path)
    cfg = config(tmp_path, trials=6, windows=3)
    text = leaderboard.render(runner.run(cfg), cfg, verbose=True)
    assert "3 walk-forward windows" in text
    assert "one market regime" in text
    assert "stab" in text


def test_a_single_window_run_says_nothing_about_windows(tmp_path):
    from candlebench import leaderboard

    write_cache(tmp_path)
    cfg = config(tmp_path)
    assert "walk-forward" not in leaderboard.render(runner.run(cfg), cfg, verbose=True)


def test_a_window_below_the_trade_floor_does_not_get_a_sign():
    """Stability takes the sign of a window's mean, so each window is a claim.

    `consistency` can afford a low floor: it is a proportion over 200 trials, and
    one thin trial barely moves it. Stability has only N windows, so a window
    counted on three trades would put its sign directly into the headline figure.
    Four windows of three trades each would read `stab 100%` on twelve trades,
    which is the fabricated number this project exists to avoid.
    """
    cfg = replace(
        StatsConfig(), bootstrap_samples=200, min_trades=10, min_trades_per_trial=3
    )
    rows = [replace(trade(1.0, trial=i), window=i % 4) for i in range(12)]
    stats = summarise(rows, stats_cfg=cfg)
    assert stats.window_expectancy_r == {}
    assert stats.stability is None


def test_windows_at_or_above_the_floor_do_count():
    cfg = replace(
        StatsConfig(), bootstrap_samples=200, min_trades=4, min_trades_per_trial=3
    )
    rows = [replace(trade(1.0, trial=i), window=0) for i in range(4)]
    rows += [replace(trade(-1.0, trial=10 + i), window=1) for i in range(4)]
    stats = summarise(rows, stats_cfg=cfg)
    assert set(stats.window_expectancy_r) == {0, 1}
    assert stats.stability == pytest.approx(0.5)


def test_the_regime_note_reflects_how_much_history_was_actually_used(tmp_path):
    """The warning was hardcoded to Yahoo's 28-day cap.

    With a source that reaches back two years, six windows are four-month
    periods spanning two calendar years, and calling them "adjacent weeks of one
    market regime" understates the run's own evidence. A report that
    misdescribes its evidence is wrong in the same way as one that overstates
    it.
    """
    from candlebench import leaderboard

    write_cache(tmp_path, sessions=_two_years_of_sessions())
    cfg = config(tmp_path, trials=12, windows=3)
    text = leaderboard.render(runner.run(cfg), cfg, verbose=True)
    assert "3 walk-forward windows" in text
    assert "one market regime" not in text
    assert "spanning" in text


def test_a_short_history_still_warns_that_it_is_one_regime(tmp_path):
    from candlebench import leaderboard

    write_cache(tmp_path)
    cfg = config(tmp_path, trials=6, windows=3)
    text = leaderboard.render(runner.run(cfg), cfg, verbose=True)
    assert "one market regime" in text


def _two_years_of_sessions():
    import pandas as pd

    days = pd.bdate_range("2024-10-01", "2026-09-30", freq="3W-MON")
    return tuple(d.strftime("%Y-%m-%d") for d in days)
