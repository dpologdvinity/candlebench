"""Chronological validation is independent evidence, never selection data."""

from dataclasses import fields, replace
from datetime import date

import numpy as np
import pandas as pd
import pytest

from candlebench import bars, engine, runner
from candlebench.patterns import control
from tests.test_end_to_end import SYMBOLS, config, write_cache


DAYS = tuple(f"2026-09-{day:02d}" for day in range(14, 22))
CUTOFF = date.fromisoformat(DAYS[-2])


@pytest.fixture
def validation_config(tmp_path, monkeypatch):
    def no_fetch(*args, **kwargs):
        pytest.fail("validation tests must use cached local data")

    monkeypatch.setattr(bars, "warm_cache", no_fetch)
    write_cache(tmp_path, sessions=DAYS)
    cfg = config(tmp_path, trials=16, holdout_fraction=0.25)
    return replace(
        cfg,
        patterns=("hammer", "bullish_engulfing", "random_long"),
        stats=replace(cfg.stats, min_trades=2, min_sessions=2),
    )


def discovery_rows(result):
    """Strip only final confirmation fields; discovery estimates stay intact."""
    return {
        (row.pattern, row.interval): replace(
            row, verdict=row.discovery_verdict, validation=None
        )
        for row in result.stats
    }


def candidate_keys(result):
    return {
        (row["pattern"], row["interval"])
        for row in result.validation["candidates"]
    }


@pytest.fixture
def deterministic_outcomes(monkeypatch):
    """Fix signals/fills while preserving sampling and corrected inference.

    Two separated eligible entries per trial give every date usable evidence.
    Discovery selects hammer only. A profitable unselected pattern in holdout
    must never be evaluated or promoted by the runner.
    """
    outcomes = {
        "discovery": {"hammer": 2.0, "bullish_engulfing": -1.0, "random_long": 0.0},
        "validation": {"hammer": 2.0, "bullish_engulfing": 4.0, "random_long": 0.0},
        "simulated": [],
    }

    def mask_for(length):
        mask = np.zeros(length, dtype=bool)
        mask[[20, 50]] = True
        return mask

    monkeypatch.setattr(runner.patterns, "detect", lambda spec, geom, thresholds: mask_for(len(geom)))
    monkeypatch.setattr(control, "control_mask", lambda length, rate, first_valid, rng: mask_for(length))

    def simulate(geom, mask, spec, trade_cfg, cost_cfg, **kwargs):
        phase = "validation" if kwargs["session"] >= CUTOFF else "discovery"
        outcomes["simulated"].append((phase, spec.name))
        value = outcomes[phase][spec.name]
        rows = []
        for index in np.flatnonzero(mask):
            price = float(geom.close[index])
            values = dict(
                pattern=spec.name, interval=kwargs["interval"], symbol=kwargs["symbol"],
                session=kwargs["session"], trial_index=kwargs["trial_index"],
                direction=spec.direction, entry_index=int(index), exit_index=int(index) + 2,
                entry_price=price, exit_price=price + value * spec.direction,
                stop_price=price - spec.direction, target_price=price + 2 * spec.direction,
                risk_per_share=1.0, exit_reason="timeout", gross_r=value, net_r=value,
                return_pct=value / price, window=kwargs.get("window", 0),
                entry_minute=int(kwargs["bar_minutes"][index]),
                sample=kwargs.get("sample", "discovery"),
            )
            supported = {field.name for field in fields(engine.Trade)}
            rows.append(engine.Trade(**{key: value for key, value in values.items() if key in supported}))
        return rows

    monkeypatch.setattr(engine, "simulate", simulate)
    return outcomes


def test_run_reserves_full_cache_dates_and_records_both_phases(validation_config):
    result = runner.run(validation_config)
    metadata = result.validation
    assert metadata["enabled"] is True
    assert metadata["cutoff"] == CUTOFF.isoformat()
    assert metadata["discovery_trials"] == 12
    assert metadata["validation_trials"] == 4
    assert metadata["warning"]
    discovery = [trial for trial in result.trials if trial.sample == "discovery"]
    validation = [trial for trial in result.trials if trial.sample == "validation"]
    assert len(discovery) == 12
    assert len(validation) == 4
    assert max(trial.session for trial in discovery) < min(trial.session for trial in validation)
    assert {trial.seed for trial in discovery}.isdisjoint(trial.seed for trial in validation)
    assert all(trade.sample in {"discovery", "validation"} for trade in result.trades)


def test_changed_holdout_prices_cannot_change_discovery_evidence_or_selection(validation_config):
    first = runner.run(validation_config)
    for symbol in SYMBOLS:
        path = bars.cache_file(validation_config.cache_path, symbol, "1m")
        frame = pd.read_parquet(path)
        later = frame.index.date >= CUTOFF
        # Preserve usable OHLC ranges, but radically change holdout levels and drift.
        offsets = np.linspace(0, 50, int(later.sum()))
        for column in ("open", "high", "low", "close"):
            frame.loc[later, column] = frame.loc[later, column] * 10 + offsets
        frame.to_parquet(path)
    second = runner.run(validation_config)
    assert any(row.trades for row in first.stats)
    assert discovery_rows(first) == discovery_rows(second)
    assert candidate_keys(first) == candidate_keys(second)
    assert [trial for trial in first.trials if trial.sample == "discovery"] == [
        trial for trial in second.trials if trial.sample == "discovery"
    ]
    assert [trade for trade in first.trades if trade.sample == "discovery"] == [
        trade for trade in second.trades if trade.sample == "discovery"
    ]


def test_only_discovery_candidates_are_evaluated_in_holdout(validation_config, deterministic_outcomes):
    result = runner.run(validation_config)
    assert candidate_keys(result) == {("hammer", "1m"), ("hammer", runner.POOLED)}
    assert result.validation["evaluated"] is True
    for row in result.stats:
        if row.pattern == "bullish_engulfing":
            assert row.discovery_verdict == "NEGATIVE"
            assert row.validation is None
            assert row.verdict != "EDGE"
    validation_trades = [trade for trade in result.trades if trade.sample == "validation"]
    assert validation_trades
    assert {trade.pattern for trade in validation_trades} == {"hammer", "random_long"}
    assert {name for phase, name in deterministic_outcomes["simulated"] if phase == "validation"} == {
        "hammer", "random_long"
    }


def test_final_edge_requires_positive_independent_validation(validation_config, deterministic_outcomes):
    result = runner.run(validation_config)
    for row in result.stats:
        if row.pattern == "hammer":
            assert row.discovery_verdict == "EDGE"
            assert row.validation is not None
            assert row.validation.verdict == "EDGE"
            assert row.verdict == "EDGE"
            assert row.trades == 24
            assert row.validation.trades == 8


def test_failed_validation_preserves_discovery_candidate_but_removes_final_edge(
    validation_config, deterministic_outcomes
):
    deterministic_outcomes["validation"]["hammer"] = -2.0
    result = runner.run(validation_config)
    assert candidate_keys(result) == {("hammer", "1m"), ("hammer", runner.POOLED)}
    for row in result.stats:
        if row.pattern == "hammer":
            assert row.discovery_verdict == "EDGE"
            assert row.validation.verdict == "NEGATIVE"
            assert row.verdict != "EDGE"


def test_no_discovery_candidates_means_holdout_is_not_evaluated(validation_config, deterministic_outcomes):
    deterministic_outcomes["discovery"]["hammer"] = -2.0
    result = runner.run(validation_config)
    assert candidate_keys(result) == set()
    assert result.validation["enabled"] is True
    assert result.validation["evaluated"] is False
    assert all(row.validation is None for row in result.stats)
    assert all(trade.sample == "discovery" for trade in result.trades)
    assert not [name for phase, name in deterministic_outcomes["simulated"] if phase == "validation"]
    assert any("candidate" in warning.lower() for warning in result.warnings)


def test_zero_holdout_reports_exploratory_candidate_without_confirmed_edge(
    validation_config, deterministic_outcomes
):
    cfg = replace(validation_config, run=replace(validation_config.run, holdout_fraction=0))
    result = runner.run(cfg)
    assert result.validation["enabled"] is False
    assert result.validation["evaluated"] is False
    assert all(trial.sample == "discovery" for trial in result.trials)
    assert all(trade.sample == "discovery" for trade in result.trades)
    for row in result.stats:
        if row.pattern == "hammer":
            assert row.discovery_verdict == "EDGE"
            assert row.validation is None
            assert row.verdict == "NOISE"
    assert any("explor" in warning.lower() for warning in result.warnings)
