"""Configuration tests.

A silently ignored key would change what a run measures with no visible sign,
so rejection is the behaviour under test rather than tolerance.
"""

from __future__ import annotations

import pytest

from candlebench import config as config_module


def write(tmp_path, text: str):
    path = tmp_path / "backtest.toml"
    path.write_text(text)
    return path


def test_defaults_load_with_no_file():
    cfg = config_module.load(None)
    assert cfg.run.intervals == ("1m", "5m", "15m", "30m", "1h")
    assert "random_long" in cfg.patterns


def test_the_shipped_config_is_valid():
    """The file a first-time user runs against must actually parse."""
    cfg = config_module.load("config/backtest.toml")
    assert len(cfg.patterns) == 22


def test_an_unknown_key_is_rejected_by_name(tmp_path):
    path = write(tmp_path, "[trade]\nreward_multiple = 2.0\nrewrad_multiple = 3.0\n")
    with pytest.raises(ValueError, match="rewrad_multiple"):
        config_module.load(path)


def test_an_unknown_section_is_rejected(tmp_path):
    path = write(tmp_path, "[trading]\nreward_multiple = 2.0\n")
    with pytest.raises(ValueError, match="trading"):
        config_module.load(path)


def test_a_sub_minute_interval_is_rejected_with_the_reason(tmp_path):
    """The limit is the source's, and the message has to name the one that can.

    A user asking yfinance for 1s bars needs to learn that this source has no
    interval below 1m and that Alpaca does, not that they typed something
    unrecognised.
    """
    path = write(tmp_path, '[run]\nintervals = ["1s", "1m"]\n')
    with pytest.raises(ValueError, match="alpaca serves them"):
        config_module.load(path)


def test_an_unknown_pattern_is_rejected_with_the_valid_names(tmp_path):
    path = write(tmp_path, "[patterns]\nhammer_time = true\n")
    with pytest.raises(ValueError, match="hammer_time"):
        config_module.load(path)


def test_disabling_every_pattern_is_rejected(tmp_path):
    """There would be nothing to measure, so failing beats an empty table."""
    path = write(tmp_path, "[patterns]\nhammer = false\n")
    with pytest.raises(ValueError, match="no patterns enabled"):
        config_module.load(path)


@pytest.mark.parametrize(
    "body,message",
    [
        ("[run]\ntrials = 0\n", "trials"),
        ("[trade]\nreward_multiple = 0\n", "reward_multiple"),
        ("[trade]\nmax_hold_bars = 0\n", "max_hold_bars"),
        ("[trade]\nrisk_per_trade_usd = 0\n", "risk_per_trade_usd"),
        ("[universe]\nsample_size = 0\n", "sample_size"),
        ('[stats]\nrank_by = "vibes"\n', "rank_by"),
        ("[stats]\nbootstrap_samples = 0\n", "bootstrap_samples"),
        ('[run]\nintervals = []\n', "intervals"),
    ],
)
def test_out_of_range_values_are_rejected(tmp_path, body, message):
    with pytest.raises(ValueError, match=message):
        config_module.load(write(tmp_path, body))


def test_command_line_overrides_outrank_the_file(tmp_path):
    path = write(tmp_path, '[run]\ntrials = 200\nseed = 42\nintervals = ["1m", "5m"]\n')
    cfg = config_module.override(
        config_module.load(path), trials=7, seed=None, intervals=("5m",), patterns=("hammer",)
    )
    assert cfg.run.trials == 7
    assert cfg.run.seed == 42  # not overridden, so the file's value survives
    assert cfg.run.intervals == ("5m",)
    assert cfg.patterns == ("hammer",)


def test_an_override_is_validated_too():
    with pytest.raises(ValueError, match="does not serve"):
        config_module.override(config_module.load(None), intervals=("10s",))


def test_patterns_omitted_entirely_enables_everything(tmp_path):
    path = write(tmp_path, "[run]\ntrials = 5\n")
    assert len(config_module.load(path).patterns) == 22
