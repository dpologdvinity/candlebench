"""Chronological validation must never feed discovery or tuning."""
from dataclasses import replace
from datetime import date, timedelta

import numpy as np
import pytest

from candlebench import sampling
from candlebench.config import Config, RunConfig, StatsConfig, validate


def pool(monkeypatch, days=20):
    dates = [date(2026, 1, 1) + timedelta(days=i) for i in range(days)]
    monkeypatch.setattr(sampling.bars, 'available_sessions',
                        lambda *args: {'AAA': dates, 'BBB': dates})
    return dates


def test_holdout_dates_are_strictly_later_and_not_sampled_for_discovery(monkeypatch, tmp_path):
    dates = pool(monkeypatch)
    trials = sampling.draw_trials(('AAA', 'BBB'), ('1m',), tmp_path, 20,
                                  np.random.default_rng(42), windows=2,
                                  holdout_fraction=0.2)
    discovery = [t for t in trials if t.sample == 'discovery']
    validation = [t for t in trials if t.sample == 'validation']
    assert len(discovery) == 16
    assert len(validation) == 4
    assert max(t.session for t in discovery) < min(t.session for t in validation)
    assert all(t.session in dates[-4:] for t in validation)
    assert {t.window for t in discovery} == {0, 1}
    assert len({t.index for t in trials}) == 20


def test_split_is_reproducible_and_zero_preserves_original_draw(monkeypatch, tmp_path):
    pool(monkeypatch)
    args = (('AAA', 'BBB'), ('1m',), tmp_path, 20)
    a = sampling.draw_trials(*args, np.random.default_rng(5), holdout_fraction=0.2)
    b = sampling.draw_trials(*args, np.random.default_rng(5), holdout_fraction=0.2)
    assert a == b
    original = sampling.draw_trials(*args, np.random.default_rng(5))
    explicit = sampling.draw_trials(*args, np.random.default_rng(5), holdout_fraction=0)
    assert original == explicit
    assert all(t.sample == 'discovery' for t in original)


def test_holdout_refuses_single_date_cache(monkeypatch, tmp_path):
    pool(monkeypatch, days=1)
    with pytest.raises(RuntimeError, match='holdout.*two|two.*holdout'):
        sampling.draw_trials(('AAA',), ('1m',), tmp_path, 10,
                             np.random.default_rng(1), holdout_fraction=0.2)


def test_holdout_windows_cannot_exceed_discovery_dates(monkeypatch, tmp_path):
    pool(monkeypatch, days=3)
    with pytest.raises(RuntimeError, match='windows|sessions'):
        sampling.draw_trials(('AAA',), ('1m',), tmp_path, 10,
                             np.random.default_rng(1), windows=3, holdout_fraction=0.2)


@pytest.mark.parametrize('fraction', [-0.1, 1, float('nan'), float('inf'), '0.2', True])
def test_holdout_fraction_validation(fraction):
    cfg = Config(run=replace(RunConfig(), holdout_fraction=fraction), patterns=('hammer',))
    with pytest.raises(ValueError, match='holdout_fraction'):
        validate(cfg)


def test_default_reserves_validation_and_declared_experiments_are_positive():
    assert RunConfig().holdout_fraction == 0.2
    for count in (0, -1, 1.5, True):
        with pytest.raises(ValueError, match='experiment_count'):
            validate(Config(stats=replace(StatsConfig(), experiment_count=count),
                            patterns=('hammer',)))


def test_an_end_date_hides_every_later_session_from_both_samples(monkeypatch, tmp_path):
    """A walk-forward fold must not see the future it is later judged on."""
    dates = pool(monkeypatch)
    end = dates[11]
    trials = sampling.draw_trials(('AAA', 'BBB'), ('1m',), tmp_path, 40,
                                  np.random.default_rng(3), holdout_fraction=0.25,
                                  end_date=end)
    assert max(t.session for t in trials) <= end
    validation = [t for t in trials if t.sample == 'validation']
    assert validation and min(t.session for t in validation) > dates[7]


@pytest.mark.parametrize('value', ['2026-13-01', 'yesterday'])
def test_an_end_date_that_is_not_an_iso_date_is_refused(value):
    with pytest.raises(ValueError, match='end_date'):
        validate(replace(Config(), run=replace(RunConfig(), end_date=value)))
