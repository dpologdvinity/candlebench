"""Walk-forward confirmation: folds see no future, and pooling finds a real edge."""
from dataclasses import replace
from datetime import date, timedelta

import pytest

from candlebench import bars, synthetic, walkforward
from candlebench.config import load, validate


def _dates(n):
    return [date(2026, 1, 1) + timedelta(days=i) for i in range(n)]


def test_segments_follow_the_initial_window_in_order_without_overlap():
    dates = _dates(20)
    parts = walkforward.segments(dates, 3, initial=0.4)
    assert [len(p) for p in parts] == [4, 4, 4]
    flat = [d for p in parts for d in p]
    assert flat == dates[8:]
    assert all(a[-1] < b[0] for a, b in zip(parts, parts[1:]))


def test_too_few_dates_for_the_folds_is_refused():
    with pytest.raises(ValueError, match="too few"):
        walkforward.segments(_dates(4), 3)


def _market(tmp_path, planted):
    cfg = load(None)
    symbols = ("WFA", "WFB", "WFC", "WFD")
    cfg = validate(replace(
        cfg,
        run=replace(cfg.run, source="synthetic", intervals=("1m",), trials=120, seed=7,
                    cache_dir=str(tmp_path), throttle_s=0.0, lookback_days=120),
        universe=replace(cfg.universe, symbols=symbols, sample_size=len(symbols)),
        stats=replace(cfg.stats, bootstrap_samples=2000, min_sessions=5),
        patterns=("bullish_engulfing", "hammer", "random_long", "random_short"),
    ))
    bars.warm_cache(symbols, ("1m",), cfg.cache_path, 0.0, source="synthetic",
                    lookback_days=120, download=synthetic.downloader(planted))
    return cfg


def test_every_fold_selects_before_its_test_dates_and_a_strong_edge_is_confirmed(tmp_path):
    """A large planted edge must be selected in each fold and pass the pooled test."""
    cfg = _market(tmp_path, synthetic.Planted("bullish_engulfing", drift=1.0, bars=5))
    seen = []
    result = walkforward.run(cfg, folds=3, on_fold=seen.append)
    assert len(seen) == 3
    assert all(f.cutoff <= f.end for f in seen)
    assert all(a.end < b.cutoff for a, b in zip(seen, seen[1:]))
    assert any(("bullish_engulfing", "1m") in f.candidates for f in seen)
    assert result.trades > 0 and result.delta_r is not None and result.delta_r > 0
    assert result.confirmed


def test_a_strategy_that_never_selects_anything_has_no_record_not_a_zero(tmp_path):
    """With nothing planted, discovery should select nothing, so nothing is traded."""
    cfg = _market(tmp_path, None)
    result = walkforward.run(cfg, folds=2)
    assert all(not f.candidates for f in result.folds)
    assert result.trades == 0
    assert result.expectancy_r is None and result.p_expectancy is None
    assert not result.confirmed
