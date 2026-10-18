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
from dataclasses import replace
from pathlib import Path

from candlebench import patterns
from candlebench.config import Config, validate
from candlebench.metrics import POWER, PatternStats
from candlebench.patterns.control import CONTROLS
from candlebench.runner import POOLED, RunResult


def load(path: str | Path) -> tuple[str, ...]:
    """Import a signal file and return the names it registered, in order."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"no signal file at {path}")
    before = set(patterns.registry())
    # Named by the file's full path, so two signal files called signal.py in
    # different directories do not replace each other in sys.modules.
    digest = hashlib.sha1(str(path.resolve()).encode()).hexdigest()[:12]
    module_name = f"candlebench_signal_{digest}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"{path} cannot be imported as a Python module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(module_name, None)
        raise
    added = tuple(name for name in patterns.registry() if name not in before)
    if not added:
        raise ValueError(
            f"{path} registers no signal; decorate a detector with "
            "@candlebench.patterns.pattern(name, bias=..., bars_required=...)")
    controls = [name for name in added if patterns.get(name).kind == "control"]
    if controls:
        raise ValueError(f"{path} registers controls ({', '.join(controls)}); "
                         "a signal file registers patterns only")
    return added


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
    limit = (
        "the size of edge this test could detect is unavailable"
        if s.mde_delta_r is None
        else f"an advantage of {s.mde_delta_r:.2f}R or more over random entry would have "
             f"been caught {POWER:.0%} of the time"
    )
    held_out = (" It passed discovery but failed the holdout."
                if s.discovery_verdict == "EDGE" else "")
    return (f"no confirmed edge over {seen}: {_r(s.baseline_delta_r)} a trade against its "
            f"matched random entries, {_r(s.expectancy_r)} after costs. "
            f"{limit[0].upper()}{limit[1:]}.{held_out}")


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
