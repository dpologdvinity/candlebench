"""`candlebench test`: a user's own signal, measured the way the patterns were.

The point of the command is that a user's signal gets exactly the scrutiny the
built-in patterns got, and nothing else is measured beside it. Each rule here
is a way that could quietly fail: another file's patterns leaking in, a name
shadowing a built-in pattern, or a verdict sentence reporting a missing number
as zero.
"""

# pylint cannot see that `verdicts` returns one line per row here.
# pylint: disable=unbalanced-tuple-unpacking

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from candlebench import cli, config as config_module, patterns, signals
from candlebench.metrics import PatternStats

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "breakout_20.py"

TWO_SIGNALS = '''
from candlebench.patterns import pattern
from candlebench.patterns.context import lag, rolling_max, rolling_min


@pattern("test_breakout_up", bias="bull", bars_required=11)
def up(geom, thresholds):
    return geom.close > lag(rolling_max(geom.high, 10))


@pattern("test_breakout_down", bias="bear", bars_required=11)
def down(geom, thresholds):
    return geom.close < lag(rolling_min(geom.low, 10))
'''


@pytest.fixture(autouse=True)
def isolated_registry(monkeypatch):
    """Each test registers into its own copy, so a signal it loads cannot leak
    into another test's count of the built-in patterns."""
    monkeypatch.setattr(patterns, "_REGISTRY", dict(patterns.registry()))


def detector(decorator: str, body: str) -> str:
    """A one-detector signal file."""
    return (f"from candlebench.patterns import pattern\n\n{decorator}\n"
            f"def f(geom, thresholds):\n    {body}\n")


BROKEN = '@pattern("broken", bias="bull", bars_required=1)'


def write(tmp_path: Path, source: str, name: str = "signal.py") -> Path:
    path = tmp_path / name
    path.write_text(source)
    return path


def test_a_signal_file_yields_the_names_it_registers_in_order(tmp_path):
    names = signals.load(write(tmp_path, TWO_SIGNALS))
    assert names == ("test_breakout_up", "test_breakout_down")
    assert patterns.get("test_breakout_down").direction == -1


def test_two_files_with_the_same_name_in_different_folders_both_load(tmp_path):
    """Modules are named by full path; by file name, the second would replace
    the first in sys.modules."""
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    first = signals.load(write(tmp_path / "a", TWO_SIGNALS.replace("test_breakout", "a")))
    second = signals.load(write(tmp_path / "b", TWO_SIGNALS.replace("test_breakout", "b")))
    assert first == ("a_up", "a_down") and second == ("b_up", "b_down")


def test_a_file_that_registers_nothing_is_refused_with_how_to_write_one(tmp_path):
    with pytest.raises(ValueError, match=r"@candlebench\.patterns\.pattern"):
        signals.load(write(tmp_path, "x = 1\n"))


def test_a_signal_cannot_take_a_built_in_patterns_name(tmp_path):
    """Otherwise a user's detector could silently replace the one the
    published results were measured with."""
    source = TWO_SIGNALS.replace("test_breakout_up", "hammer")
    with pytest.raises(ValueError, match="already registered"):
        signals.load(write(tmp_path, source))


def test_a_missing_file_is_named_in_the_error(tmp_path):
    with pytest.raises(FileNotFoundError, match="nope.py"):
        signals.load(tmp_path / "nope.py")


def test_only_the_signals_are_measured_beside_a_control_for_each_direction(tmp_path):
    names = signals.load(write(tmp_path, TWO_SIGNALS))
    cfg = signals.configure(config_module.load(None), names)
    assert cfg.patterns == ("test_breakout_up", "test_breakout_down",
                            "random_long", "random_short")


def test_the_example_signal_loads():
    assert signals.load(EXAMPLE) == ("breakout_20",)


def stats(**overrides) -> PatternStats:
    values = {
        "pattern": "sig", "interval": "1m", "bias": "bull", "kind": "pattern",
        "signals": 400, "trades": 300, "win_rate": 0.4, "expectancy_r": -0.05,
        "expectancy_r_gross": 0.01, "total_return_pct": -1.0, "profit_factor": 0.9,
        "sharpe_per_trade": -0.03, "max_drawdown_r": 12.0, "avg_bars_held": 6.0,
        "exit_mix": {"stop": 1.0}, "consistency": None, "window_expectancy_r": {},
        "stability": None, "ci_low": -0.1, "ci_high": 0.0, "baseline_delta_r": -0.004,
        "verdict": "NOISE", "sessions": 40, "mde_delta_r": 0.071,
    }
    values.update(overrides)
    return PatternStats(**values)


class Result:  # pylint: disable=too-few-public-methods
    """The one part of a RunResult that `verdicts` reads."""

    def __init__(self, *rows):
        self.stats = list(rows)


CONFIG = config_module.load(None)


def test_noise_states_the_edge_size_the_test_could_have_caught():
    """A null result means little until it says how large an edge was ruled out."""
    (line,) = signals.verdicts(Result(stats()), CONFIG, ("sig",))
    assert line.startswith("sig at 1m: NOISE: no confirmed edge over 300 trades on 40 dates")
    assert "+0.00R a trade against its matched random entries" in line
    assert ("The discovery test would have caught an advantage of 0.07R or more over "
            "random entry 80% of the time") in line


def test_a_missing_number_reads_unavailable_never_zero():
    (line,) = signals.verdicts(
        Result(stats(mde_delta_r=None, baseline_delta_r=None)), CONFIG, ("sig",))
    assert "unavailable a trade against" in line
    assert "The size of edge this test could detect is unavailable" in line
    assert "0.00R" not in line


@pytest.mark.parametrize("validation, ending", [
    (None, "there was no holdout to confirm it (an exploratory run, or no holdout dates "
           "for this row)."),
    ("INSUFFICIENT", "the holdout had too few trades to confirm it."),
    ("NOISE", "It passed discovery but failed the holdout (NOISE)."),
])
def test_a_discovery_edge_says_why_the_holdout_did_not_confirm_it(validation, ending):
    """Regression: "failed the holdout" was printed for runs with no holdout at
    all, which the runner also downgrades from EDGE."""
    evidence = None if validation is None else stats(verdict=validation)
    (line,) = signals.verdicts(
        Result(stats(discovery_verdict="EDGE", validation=evidence)), CONFIG, ("sig",))
    assert line.endswith(ending)


def test_verdicts_list_each_interval_in_config_order_then_pooled():
    rows = [stats(interval="all"), stats(interval="5m"), stats(interval="1m"),
            stats(pattern="other")]
    cfg = replace(CONFIG, run=replace(CONFIG.run, intervals=("1m", "5m")))
    lines = signals.verdicts(Result(*rows), cfg, ("sig",))
    assert [line.split(":")[0] for line in lines] == [
        "sig at 1m", "sig at 5m", "sig at all intervals pooled"]


def test_the_command_measures_a_signal_end_to_end_on_the_synthetic_market(tmp_path, capsys):
    path = write(tmp_path, TWO_SIGNALS)
    code = cli.main(["test", str(path), "--synthetic", "--intervals", "1m", "--trials", "40",
                     "--cache-dir", str(tmp_path / "demo")])
    out = capsys.readouterr().out
    assert code == 0
    verdicts = out[out.index("\nverdicts\n"):]
    assert "test_breakout_up at 1m:" in verdicts and "test_breakout_down at 1m:" in verdicts
    # Nothing but the two signals and their direction controls was measured.
    assert "hammer" not in out and "random_long" in out
    # A driftless random walk has no edge to find.
    assert ": EDGE:" not in verdicts


def test_the_command_reports_a_bad_signal_file_without_a_traceback(tmp_path, capsys):
    code = cli.main(["test", str(write(tmp_path, "x = 1\n")), "--synthetic"])
    assert code == 2
    assert "could not load a signal" in capsys.readouterr().out


@pytest.mark.parametrize("decorator, named", [
    ('@pattern("bad_bias", bias="long", bars_required=2)', "bias 'long'; use 'bull' or 'bear'"),
    ('@pattern("bad_bars", bias="bull", bars_required=0)', "bars_required 0"),
])
def test_a_signal_with_an_unusable_setting_is_refused_by_name(tmp_path, decorator, named):
    """Regression: bias="long" passed the decorator and failed later as a bare
    KeyError from inside the configuration."""
    source = detector(decorator, "return geom.close > 0")
    with pytest.raises(signals.SignalError, match=named):
        signals.load(write(tmp_path, source))


def test_an_error_while_loading_names_the_file_and_line(tmp_path):
    path = write(tmp_path, "x = 1\nundefined_thing\n")
    with pytest.raises(signals.SignalError, match=r"raised NameError while loading: .* \(line 2\)"):
        signals.load(path)


def test_a_failed_load_leaves_nothing_registered(tmp_path):
    """Otherwise fixing the file and loading it again in the same process
    collides with the half that registered the first time."""
    broken = TWO_SIGNALS.replace("test_breakout_down", "hammer")
    with pytest.raises(ValueError, match="already registered"):
        signals.load(write(tmp_path, broken))
    assert "test_breakout_up" not in patterns.registry()
    assert signals.load(write(tmp_path, TWO_SIGNALS, "fixed.py")) == (
        "test_breakout_up", "test_breakout_down")


@pytest.mark.parametrize("body, named", [
    ("return geom.nosuch", "raised AttributeError"),
    ("return geom.close[:-1] > 0", "returned 79 values for 80 bars"),
])
def test_a_detector_that_fails_names_the_signal_and_its_file(tmp_path, body, named):
    from tests.conftest import geometry
    path = write(tmp_path, detector(BROKEN, body))
    signals.load(path)
    geom = geometry([(100.0, 101.0, 99.0, 100.5)] * 80, lookback=3)
    with pytest.raises(signals.SignalError, match=f"broken in {path}.*{named}"):
        patterns.get("broken").fn(geom, None)


def test_the_command_reports_a_detector_failure_without_a_traceback(tmp_path, capsys):
    source = detector(BROKEN, "return geom.nosuch")
    code = cli.main(["test", str(write(tmp_path, source)), "--synthetic", "--intervals", "1m",
                     "--trials", "5", "--cache-dir", str(tmp_path / "demo")])
    assert code == 2
    assert "the signal failed during the run: broken in" in capsys.readouterr().out


def test_a_file_listed_twice_is_refused(tmp_path, capsys):
    path = str(write(tmp_path, TWO_SIGNALS))
    assert cli.main(["test", path, path, "--synthetic"]) == 2
    assert "listed twice" in capsys.readouterr().out


def test_the_caveats_state_the_family_the_verdicts_were_corrected_for(tmp_path):
    names = signals.load(write(tmp_path, TWO_SIGNALS))
    cfg = signals.configure(CONFIG, names)
    family, holdout = signals.caveats(cfg)
    # Two signals (two tests each) and two controls, per interval and pooled.
    assert f"covered the {6 * (len(cfg.run.intervals) + 1)} hypotheses" in family
    assert "stats.experiment_count" in family
    assert "uses them up" in holdout


def test_testing_with_no_cached_bars_says_what_to_do(tmp_path, capsys):
    config = tmp_path / "empty.toml"
    config.write_text(f'[run]\ncache_dir = "{tmp_path / "nothing"}"\n')
    code = cli.main(["test", str(write(tmp_path, TWO_SIGNALS)), "--config", str(config)])
    out = capsys.readouterr().out
    assert code == 1
    assert "fetch" in out and "--synthetic" in out and "Traceback" not in out
