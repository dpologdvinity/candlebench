"""Test a signal of your own the way every candlestick pattern was tested.

The 66 patterns are only the first thing this method was pointed at. Its value
is the comparison, not the catalogue: every trade is paired with a random entry
on the same session, in the same direction, at the same stop distance, so a
signal is credited only for choosing *when* to enter. Most backtests compare a
strategy with doing nothing, and doing nothing is the comparison a strategy
wins by accident: the market drifted, the stops were wide, the costs were left
out.

A signal is a Python file that registers one or more detectors with the same
decorator the built-in patterns use:

    from candlebench.patterns import pattern
    from candlebench.patterns.context import lag, rolling_max

    @pattern("breakout_20", bias="bull", bars_required=21)
    def breakout_20(geom, thresholds):
        return geom.close > lag(rolling_max(geom.high, 20))

`candlebench test FILE.py` measures only what the file registers, beside the
random-entry control for each direction it trades, and ends with one verdict
per signal and timeframe in plain words. A NOISE verdict comes with the size of
edge that could still be hiding: "no edge" means little until it says how large
an edge the test would have caught.
"""

from __future__ import annotations

import hashlib
import importlib.util
import sys
import traceback
from dataclasses import replace
from pathlib import Path

import numpy as np

from candlebench import metrics, patterns
from candlebench.config import Config, validate
from candlebench.metrics import POWER, PatternStats
from candlebench.patterns.control import CONTROLS
from candlebench.runner import POOLED, RunResult


class SignalError(ValueError):
    """A signal file, or a detector in it, did something the test cannot use.

    Raised instead of whatever the user's code raised, so the command can
    report it in a line rather than a traceback through the runner.
    """


def _where(exc: BaseException, path: Path) -> str:
    """The line of the user's file a traceback passed through, if any."""
    frame = next((f for f in reversed(traceback.extract_tb(exc.__traceback__))
                  if Path(f.filename).resolve() == path.resolve()), None)
    return f" (line {frame.lineno})" if frame else ""


def _guarded(spec: patterns.PatternSpec, path: Path) -> patterns.PatternSpec:
    """`spec` with a detector whose failures name the signal and its file."""
    detector = spec.fn

    def detect(geom, thresholds):
        try:
            mask = np.asarray(detector(geom, thresholds), dtype=bool)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            raise SignalError(f"{spec.name} in {path} raised {type(exc).__name__}: "
                              f"{exc}{_where(exc, path)}") from exc
        if mask.shape != geom.close.shape:
            raise SignalError(f"{spec.name} in {path} returned {mask.shape[0] if mask.ndim else 1}"
                              f" values for {len(geom.close)} bars; return one boolean per bar")
        return mask

    return replace(spec, fn=detect)


def _check(spec: patterns.PatternSpec, path: Path) -> None:
    if spec.kind == "control":
        raise SignalError(f"{path} registers a control ({spec.name}); "
                          "a signal file registers patterns only")
    if spec.bias not in CONTROLS:
        raise SignalError(f"{spec.name} in {path} has bias {spec.bias!r}; "
                          f"use {' or '.join(repr(b) for b in CONTROLS)}")
    if not isinstance(spec.bars_required, int) or spec.bars_required < 1:
        raise SignalError(f"{spec.name} in {path} has bars_required "
                          f"{spec.bars_required!r}; it must be a whole number of at least 1")


def load(path: str | Path) -> tuple[str, ...]:
    """Import a signal file and return the names it registered, in order.

    All or nothing: if anything about the file is unusable, every pattern it
    registered is removed again, so fixing the file and loading it a second
    time in the same process does not collide with its own first attempt.
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"no signal file at {path}")
    # Named by the file's full path, so two signal files called signal.py in
    # different directories do not replace each other in sys.modules.
    digest = hashlib.sha1(str(path.resolve()).encode()).hexdigest()[:12]
    module_name = f"candlebench_signal_{digest}"
    if module_name in sys.modules:
        raise SignalError(f"{path} is already loaded; list each signal file once")
    before = patterns.registry()
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise SignalError(f"{path} cannot be imported as a Python module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        try:
            spec.loader.exec_module(module)
        except SignalError:
            raise
        except Exception as exc:  # pylint: disable=broad-exception-caught
            raise SignalError(f"{path} raised {type(exc).__name__} while loading: "
                              f"{exc}{_where(exc, path)}") from exc
        added = tuple(name for name in patterns.registry() if name not in before)
        if not added:
            raise SignalError(
                f"{path} registers no signal; decorate a detector with "
                "@candlebench.patterns.pattern(name, bias=..., bars_required=...)")
        for name in added:
            _check(patterns.get(name), path)
            patterns._REGISTRY[name] = _guarded(patterns.get(name), path)  # pylint: disable=protected-access
        return added
    except BaseException:
        sys.modules.pop(module_name, None)
        for name in set(patterns.registry()) - set(before):
            patterns._REGISTRY.pop(name, None)  # pylint: disable=protected-access
        raise


def configure(base: Config, names: tuple[str, ...]) -> Config:
    """`base` measuring only `names`, beside the random-entry control for each
    direction they trade.

    The controls are what a row of the leaderboard is read against: what random
    entry earns or loses at each timeframe after the same costs. The matched
    controls each signal is tested against are made for it regardless.
    """
    biases = dict.fromkeys(patterns.get(name).bias for name in names)  # signal order
    return validate(replace(base, patterns=(*names, *(CONTROLS[bias] for bias in biases))))


def _r(value: float | None) -> str:
    # Rounded before formatting, so -0.004 reads +0.00R rather than -0.00R.
    return "unavailable" if value is None else f"{round(value, 2) + 0.0:+.2f}R"


def _p(value: float | None) -> str:
    return "unavailable" if value is None else f"{value:.3f}"


def _explain(s: PatternStats, config: Config) -> str:
    seen = f"{s.trades:,} trades on {s.sessions:,} dates"
    if s.verdict == "EDGE":
        return (f"beat its matched random entries by {_r(s.baseline_delta_r)} a trade "
                f"(corrected p {_p(s.p_delta_adjusted)}) and earned {_r(s.expectancy_r)} "
                f"after costs over {seen}, and the holdout confirmed it.")
    if s.verdict == "NEGATIVE":
        # Random entry loses after costs too, so a losing signal is not by that
        # alone worse than chance; the matched comparison says whether it is.
        return (f"averages {_r(s.expectancy_r)} a trade after costs over {seen} "
                f"(corrected p {_p(s.p_expectancy_adjusted)}), and "
                f"{_r(s.baseline_delta_r)} a trade against its matched random entries.")
    if s.verdict == "INSUFFICIENT":
        return (f"{seen}; a verdict needs at least {config.stats.min_trades} trades on "
                f"{config.stats.min_sessions} dates with usable matched controls.")
    # The limit is the discovery test's power. With a holdout an edge must also
    # pass there, so the chance of a confirmed EDGE at that size is lower.
    limit = (
        "the size of edge this test could detect is unavailable"
        if s.mde_delta_r is None
        else f"the discovery test would have caught an advantage of {s.mde_delta_r:.2f}R "
             f"or more over random entry {POWER:.0%} of the time"
    )
    held_out = ""
    if s.discovery_verdict == "EDGE":
        # The runner downgrades a discovery EDGE whenever the holdout did not
        # confirm it, which includes having no holdout evidence at all.
        if s.validation is None:
            held_out = (" It passed discovery, but there was no holdout to confirm it "
                        "(an exploratory run, or no holdout dates for this row).")
        elif s.validation.verdict == "INSUFFICIENT":
            held_out = " It passed discovery, but the holdout had too few trades to confirm it."
        else:
            held_out = f" It passed discovery but failed the holdout ({s.validation.verdict})."
    return (f"no confirmed edge over {seen}: {_r(s.baseline_delta_r)} a trade against its "
            f"matched random entries, {_r(s.expectancy_r)} after costs. "
            f"{limit[0].upper()}{limit[1:]}.{held_out}")


def caveats(config: Config) -> list[str]:
    """What the verdicts were corrected for, and what would void them.

    The built-in study corrected each row for 804 hypotheses across five
    declared experiments; a file of two signals faces a family of a dozen, and
    nothing counts the variants tried before it. Saying so is the difference
    between the same method and the same scrutiny.
    """
    family = metrics.discovery_family_size(
        [patterns.get(name).kind for name in config.patterns], len(config.run.intervals))
    return [
        f"Holm correction covered the {family} hypotheses in this run and "
        f"{config.stats.experiment_count} declared experiment(s). Each signal or variant "
        "tried before this one is another experiment: raise stats.experiment_count to "
        "match, or a corrected p-value overstates the evidence.",
        "A holdout confirms an edge only on dates that played no part in choosing the "
        "signal. Re-running after tuning on the same dates uses them up.",
    ]


def verdicts(result: RunResult, config: Config, names: tuple[str, ...]) -> list[str]:
    """One plain sentence per signal and timeframe, pooled last."""
    order = {interval: i for i, interval in enumerate((*config.run.intervals, POOLED))}
    lines = []
    for name in names:
        rows = sorted((s for s in result.stats if s.pattern == name),
                      key=lambda s: order.get(s.interval, len(order)))
        for s in rows:
            where = "all intervals pooled" if s.interval == POOLED else s.interval
            lines.append(f"{name} at {where}: {s.verdict}: {_explain(s, config)}")
    return lines
