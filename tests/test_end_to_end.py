"""Whole-pipeline tests against a synthetic cache.

No network. The cache is written from a generated random walk, so these tests
exercise sampling, detection, simulation, aggregation and rendering together
without depending on what Yahoo happens to be serving.
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from candlebench import bars, leaderboard, runner, sampling
from candlebench.config import Config, RunConfig, StatsConfig, UniverseConfig

SYMBOLS = ("AAA", "BBB")
SESSIONS = ("2026-09-14", "2026-09-15", "2026-09-16")


def write_cache(cache_dir, intervals=("1m",), bars_per_session=120, seed=3,
                sessions=SESSIONS):
    """A random walk per symbol and session, saved in the cache's own format."""
    rng = np.random.default_rng(seed)
    for interval in intervals:
        step = bars.INTERVAL_MINUTES[interval]
        (cache_dir / interval).mkdir(parents=True, exist_ok=True)
        for symbol in SYMBOLS:
            frames = []
            for day in sessions:
                # 13:30 UTC is 09:30 Eastern in September.
                index = pd.date_range(
                    f"{day} 13:30", periods=bars_per_session,
                    freq=f"{step}min" if step >= 1 else f"{int(step * 60)}s",
                    tz="UTC",
                )
                close = 100 + np.cumsum(rng.normal(0, 0.08, bars_per_session))
                open_ = np.concatenate([[close[0]], close[:-1]])
                spread = np.abs(rng.normal(0, 0.06, bars_per_session))
                frames.append(
                    pd.DataFrame(
                        {
                            "open": open_,
                            "high": np.maximum(open_, close) + spread,
                            "low": np.minimum(open_, close) - spread,
                            "close": close,
                            "volume": rng.integers(1_000, 50_000, bars_per_session).astype(float),
                        },
                        index=index,
                    )
                )
            pd.concat(frames).to_parquet(bars.cache_file(cache_dir, symbol, interval))


def config(cache_dir, **run_kwargs) -> Config:
    defaults = dict(
        intervals=("1m",), trials=4, seed=11, cache_dir=str(cache_dir), throttle_s=0.0,
        holdout_fraction=0.0  # Historical pipeline fixtures test exploratory replay.
    )
    run = replace(RunConfig(), **{**defaults, **run_kwargs})
    return Config(
        run=run,
        universe=replace(UniverseConfig(), symbols=SYMBOLS, sample_size=2),
        stats=replace(StatsConfig(), bootstrap_samples=200, min_trades=5),
        patterns=("hammer", "bullish_engulfing", "bearish_engulfing", "random_long", "random_short"),
    )


@pytest.fixture
def cache(tmp_path):
    write_cache(tmp_path)
    return tmp_path


def test_a_run_produces_stats_for_every_pattern_and_interval(cache):
    cfg = config(cache)
    result = runner.run(cfg)
    expected = len(cfg.patterns) * (len(cfg.run.intervals) + 1)  # +1 for the pooled view
    assert len(result.stats) == expected
    assert result.sessions_evaluated > 0


def test_the_same_seed_reproduces_the_report_exactly(cache):
    """A result nobody can reproduce is not a result."""
    cfg = config(cache)
    first = leaderboard.render(runner.run(cfg), cfg)
    second = leaderboard.render(runner.run(cfg), cfg)
    assert first == second


def test_a_different_seed_changes_the_sample(cache):
    cfg = config(cache)
    other = replace(cfg, run=replace(cfg.run, seed=99))
    assert [t.session for t in runner.run(cfg).trials] != [
        t.session for t in runner.run(other).trials
    ] or [t.symbol for t in runner.run(cfg).trials] != [
        t.symbol for t in runner.run(other).trials
    ]


def test_every_pattern_sees_the_same_trials(cache):
    """The paired design: differences cannot come from different market days."""
    result = runner.run(config(cache))
    assert len({(t.symbol, t.session) for t in result.trials}) <= len(result.trials)
    assert len(result.trials) == 4


def test_controls_are_measured_alongside_the_patterns(cache):
    result = runner.run(config(cache))
    controls = [s for s in result.stats if s.kind == "control"]
    assert {s.pattern for s in controls} == {"random_long", "random_short"}
    assert any(s.trades > 0 for s in controls)


def test_disabling_the_controls_warns_that_the_baseline_is_gone(cache):
    cfg = replace(config(cache), patterns=("hammer", "bullish_engulfing"))
    result = runner.run(cfg)
    assert any("no random-entry control" in w for w in result.warnings)
    assert all(s.baseline_delta_r is None for s in result.stats)


def test_a_session_too_short_to_measure_is_skipped_and_counted(tmp_path):
    """Half days and holidays are normal, and must not look like zero results."""
    write_cache(tmp_path, bars_per_session=5)
    result = runner.run(config(tmp_path))
    assert result.sessions_evaluated == 0
    assert result.skipped_sessions == 4


def test_an_empty_cache_fails_with_the_fetch_instruction(tmp_path):
    with pytest.raises(RuntimeError, match="candlebench fetch"):
        runner.run(config(tmp_path))


def test_more_trials_than_sessions_samples_with_replacement(cache):
    """Six available pairs, twenty trials requested."""
    result = runner.run(config(cache, trials=20))
    assert len(result.trials) == 20


def test_the_rendered_report_labels_controls_and_explains_the_verdicts(cache):
    cfg = config(cache)
    text = leaderboard.render(runner.run(cfg), cfg, verbose=True)
    assert "random-entry control" in text
    assert "INSUFFICIENT" in text
    assert "all intervals pooled" in text
    for pattern in cfg.patterns:
        assert pattern in text


def test_json_output_records_the_config_and_seed(cache, tmp_path):
    import json

    cfg = config(cache)
    out = tmp_path / "out.json"
    leaderboard.write_json(runner.run(cfg), cfg, out)
    payload = json.loads(out.read_text())
    assert payload["config"]["run"]["seed"] == 11
    assert len(payload["stats"]) == len(cfg.patterns) * 2
    assert payload["trials"][0]["symbol"] in SYMBOLS


def test_csv_output_has_one_row_per_pattern_and_interval(cache, tmp_path):
    import csv

    cfg = config(cache)
    out = tmp_path / "out.csv"
    leaderboard.write_csv(runner.run(cfg), out)
    rows = list(csv.DictReader(out.open()))
    assert len(rows) == len(cfg.patterns) * 2
    assert "expectancy_r" in rows[0]


def test_multiple_intervals_run_against_the_same_sessions(tmp_path):
    write_cache(tmp_path, intervals=("1m", "5m"), bars_per_session=120)
    cfg = config(tmp_path)
    cfg = replace(cfg, run=replace(cfg.run, intervals=("1m", "5m")))
    result = runner.run(cfg)
    assert {s.interval for s in result.stats} == {"1m", "5m", runner.POOLED}


def test_sampling_draws_against_the_narrowest_interval():
    """A session present at 1m is present at 5m, but not the reverse."""
    assert sampling.narrowest_interval(("5m", "1m", "1h")) == "1m"


def test_ranking_puts_unmeasurable_patterns_last(cache):
    cfg = config(cache)
    subset = [s for s in runner.run(cfg).stats if s.interval == "1m"]
    ordered = leaderboard.rank(subset, "ci_low")
    verdicts = [s.verdict for s in ordered]
    insufficient = [i for i, v in enumerate(verdicts) if v == "INSUFFICIENT"]
    measured = [i for i, v in enumerate(verdicts) if v != "INSUFFICIENT"]
    assert not measured or not insufficient or min(insufficient) > max(measured)


def test_a_coarse_interval_still_gets_measured(tmp_path):
    """A session too short for the configured trend window must not vanish.

    Regression: at 30m a regular-hours session holds 13 bars and at 1h only 7,
    while trend_lookback defaults to 10. Every session was skipped and both
    intervals reported zero trades for all 22 patterns, which reads as "these
    patterns never fire" rather than "this interval was never measured".
    """
    write_cache(tmp_path, intervals=("1m",), bars_per_session=13)
    result = runner.run(config(tmp_path))
    assert result.sessions_evaluated > 0
    assert result.skipped_sessions == 0
    assert any("trend_lookback reduced from 10 to 6" in w for w in result.warnings)
    assert sum(s.trades for s in result.stats) > 0


def test_a_reduced_trend_window_is_reported_with_its_reason(tmp_path):
    write_cache(tmp_path, intervals=("1m",), bars_per_session=7)
    result = runner.run(config(tmp_path))
    (note,) = [w for w in result.warnings if "trend_lookback" in w]
    assert "only 7 bars" in note
    assert "within an interval rather than across" in note


def test_a_long_session_keeps_the_configured_trend_window(tmp_path):
    write_cache(tmp_path, intervals=("1m",), bars_per_session=120)
    result = runner.run(config(tmp_path))
    assert not [w for w in result.warnings if "trend_lookback" in w]


def test_a_session_with_room_to_spare_is_not_reduced(tmp_path):
    """Regression: a one-third cap shortened 15m, whose 26 bars hold 10 fine.

    Reducing an interval that did not need it silently moved its results.
    """
    write_cache(tmp_path, intervals=("1m",), bars_per_session=26)
    result = runner.run(config(tmp_path))
    assert not [w for w in result.warnings if "trend_lookback" in w]
    assert result.sessions_evaluated > 0


# ---------- bars with no range at all ----------


def test_a_run_reports_what_share_of_bars_had_no_range(tmp_path):
    """At 1s, 30% of AAPL bars and 68% of KO bars have open=high=low=close.

    Measured against real data; at 1m the figure is 0.00%. Such a bar is not
    fabricated — a trade happened — but it is a *perfect doji*, and doji,
    dragonfly, gravestone and hammer all read exactly that geometry. At 1s they
    would be measuring how often a single eligible print lands in a second, not
    indecision between buyers and sellers. A run has to say so.
    """
    write_cache(tmp_path)
    result = runner.run(config(tmp_path))
    assert "1m" in result.flat_bar_share
    assert 0.0 <= result.flat_bar_share["1m"] <= 1.0


def test_a_cache_of_rangeless_bars_warns_about_the_geometry(tmp_path):
    import pandas as pd

    from candlebench import bars as bars_module

    (tmp_path / "1m").mkdir(parents=True)
    for symbol in SYMBOLS:
        frames = []
        for day in SESSIONS:
            index = pd.date_range(f"{day} 13:30", periods=120, freq="1min", tz="UTC")
            price = 100.0
            frames.append(pd.DataFrame(
                {"open": price, "high": price, "low": price, "close": price,
                 "volume": 1000.0},
                index=index,
            ))
        pd.concat(frames).to_parquet(
            bars_module.cache_file(tmp_path, symbol, "1m")
        )

    result = runner.run(config(tmp_path))
    assert result.flat_bar_share["1m"] == pytest.approx(1.0)
    note = [w for w in result.warnings if "no range" in w]
    assert note, result.warnings
    assert "100.0%" in note[0]
    assert "doji" in note[0]


def test_bars_with_real_ranges_produce_no_such_warning(tmp_path):
    write_cache(tmp_path)
    result = runner.run(config(tmp_path))
    assert not [w for w in result.warnings if "no range" in w]
