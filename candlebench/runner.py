"""Orchestration: trials in, per-pattern statistics out.

The loop order matters. For one trial and one interval, every pattern is
detected against the same `Geometry` object, which both guarantees the paired
comparison the sampler set up and means the shared bar measurements are
computed once rather than twenty times.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from candlebench import bars, costs, engine, metrics, patterns, sampling, universe
from candlebench.config import Config
from candlebench.patterns import context, control

# Interval label for the table that pools every interval together.
POOLED = "all"


@dataclass
class RunResult:
    stats: list[metrics.PatternStats]
    trials: list[sampling.Trial]
    symbols: tuple[str, ...]
    sessions_evaluated: int
    skipped_sessions: int
    warnings: list[str]
    # Every closed trade, flattened. Kept rather than discarded so a result can
    # be broken down by symbol, by time of day, or inspected one trade at a
    # time. ~43,000 frozen dataclasses on a full run, a few MiB of memory.
    # Persistence belongs to the caller; the runner writes nothing.
    trades: list[engine.Trade] = field(default_factory=list)
    # Mean estimated round-trip spread in basis points, the interval whose bars
    # it was measured from, and how many sessions fell back to the fixed cost.
    # One figure for the whole run, not one per timeframe: see `costs`.
    # Reported rather than left in the config, because "costs exceed the edge"
    # is the finding and this is the cost.
    spread_bps: float | None = None
    spread_interval: str | None = None
    spread_fallbacks: int = 0


def _signal_rate(mask: np.ndarray, first_valid: int) -> float:
    eligible = len(mask) - first_valid
    return float(mask.sum() / eligible) if eligible > 0 else 0.0


# Bars that must remain after the trend window and the longest pattern, so
# that signals can actually fire and their trades have room to resolve.
MIN_USABLE_BARS = 5
MIN_TREND_LOOKBACK = 2


def _progress_reporter(progress, config) -> Callable[[str, int], None]:
    """Wrap a progress callback so the runner reports absolute completion."""
    total = max(1, config.run.trials * len(config.run.intervals))

    def report(interval: str, done: int) -> None:
        if progress:
            progress(interval, done, total)

    return report


def trend_lookback_for_length(configured: int, bars_in_session: int, max_bars_required: int) -> int:
    """The trend window a session of this length can hold.

    Named and exported because two callers need the identical rule: the run
    itself, and the single-session chart endpoint. A chart computed with a
    different window would mark different bars as signals than the leaderboard
    counted.
    """
    return min(
        configured,
        max(MIN_TREND_LOOKBACK, bars_in_session - max_bars_required - MIN_USABLE_BARS),
    )


def _trend_lookback_for(
    interval: str,
    cached: dict[str, dict],
    trials,
    configured: int,
    max_bars_required: int,
) -> tuple[int, str | None]:
    """The trend window to use at one interval, reduced only if a session
    cannot hold it.

    `trend_lookback` is counted in bars, but a session holds far fewer bars at
    a coarse interval: regular hours give about 390 bars at 1m, 26 at 15m, 13
    at 30m and 7 at 1h. A fixed 10-bar window consumes a whole coarse session,
    and every session was then skipped for want of bars. That failure was
    silent and badly misleading: 30m and 1h reported zero trades for all 22
    patterns, which reads as "these patterns never fire" when the truth is
    "this interval was never measured".

    The reduction is the minimum necessary rather than a fixed share of the
    session. An earlier version capped at one third, which also shortened the
    window at 15m, where 26 bars comfortably hold the configured 10 and no
    reduction was warranted. Changing an interval that did not need changing
    silently moved its results.
    """
    lengths = [
        len(frame)
        for trial in trials
        for frame in [cached.get(trial.symbol, {}).get(trial.session)]
        if frame is not None
    ]
    if not lengths:
        return configured, None

    typical = int(np.median(lengths))
    allowed = trend_lookback_for_length(configured, typical, max_bars_required)
    if configured <= allowed:
        return configured, None

    return allowed, (
        f"{interval}: trend_lookback reduced from {configured} to {allowed} bars, "
        f"because a typical session holds only {typical} bars at this interval. "
        "Trend context is therefore shorter here than at finer intervals, so "
        "compare patterns within an interval rather than across them."
    )


def run(config: Config, progress: Callable[[str, int, int], None] | None = None) -> RunResult:
    """Execute every trial against every enabled pattern and interval.

    `progress` is called as (interval, done, total) after each trial, so a
    caller that is not a terminal — the web server — can report how far along a
    run is. A run over 200 trials and five intervals takes minutes, and a
    browser showing nothing for that long is indistinguishable from a hang.
    """
    report = _progress_reporter(progress, config)
    registry = patterns.registry()
    enabled = [registry[name] for name in config.patterns]
    real = [s for s in enabled if s.kind == "pattern"]
    controls = [s for s in enabled if s.kind == "control"]

    symbols = universe.resolve(config.universe.symbols, config.universe.sample_size)
    root = np.random.default_rng(config.run.seed)
    trials = sampling.draw_trials(
        symbols, config.run.intervals, config.cache_path, config.run.trials, root,
        windows=config.run.windows,
    )

    collected: dict[tuple[str, str], list[engine.Trade]] = {}
    signal_counts: dict[tuple[str, str], int] = {}
    spread_cost, estimates, fallbacks, spread_interval = _estimate_spreads(config, trials)
    fixed_one_way = config.costs.slippage_bps / 10_000.0
    warnings: list[str] = []
    evaluated = 0
    skipped = 0
    done = 0

    for interval in config.run.intervals:
        cached: dict[str, dict] = {}
        for trial in trials:
            if trial.symbol not in cached:
                try:
                    cached[trial.symbol] = bars.sessions(
                        bars.load(trial.symbol, interval, config.cache_path)
                    )
                except FileNotFoundError as exc:
                    cached[trial.symbol] = {}
                    warnings.append(str(exc))

        lookback, note = _trend_lookback_for(
            interval,
            cached,
            trials,
            config.thresholds.trend_lookback,
            max((s.bars_required for s in enabled), default=1),
        )
        if note:
            warnings.append(note)

        for trial in trials:
            done += 1
            report(interval, done)

            frame = cached[trial.symbol].get(trial.session)
            # A holiday, a half day, or a symbol with no coverage at this
            # interval yields too few bars to measure. Skipping is counted and
            # reported rather than passed off as a zero result.
            if frame is None or len(frame) < lookback + 4:
                skipped += 1
                continue

            geom = context.geometry(
                bars.to_arrays(frame),
                lookback,
                config.thresholds.trend_min_slope,
            )
            bar_minutes = bars.minutes_from_open(frame)
            # One cost per symbol and session, shared by every pattern below and
            # by every timeframe: the spread belongs to the market, not to the
            # detector that traded it or to the bar size used to look at it.
            one_way_cost = spread_cost.get(
                (trial.symbol, trial.session), fixed_one_way
            )
            evaluated += 1
            rng = np.random.default_rng(trial.seed)
            rates: list[float] = []

            for spec in real:
                mask = patterns.detect(spec, geom, config.thresholds)
                first_valid = patterns.first_valid_index(spec, geom.trend_lookback)
                rates.append(_signal_rate(mask, first_valid))
                _accumulate(collected, signal_counts, spec, interval, mask, geom,
                            config, trial, bar_minutes, one_way_cost)

            rate = control.matched_rate(rates)
            for spec in controls:
                first_valid = patterns.first_valid_index(spec, geom.trend_lookback)
                mask = control.control_mask(len(geom), rate, first_valid, rng)
                mask = patterns.apply_gates(mask, spec, geom)
                _accumulate(collected, signal_counts, spec, interval, mask, geom,
                            config, trial, bar_minutes, one_way_cost)

    stats_rng = np.random.default_rng(config.run.seed + 1)
    stats = [
        metrics.summarise(
            registry[name],
            interval,
            collected.get((name, interval), []),
            signal_counts.get((name, interval), 0),
            config.stats,
            stats_rng,
        )
        for interval in config.run.intervals
        for name in config.patterns
    ]

    # A pooled view across every interval. Patterns that are marginal at each
    # interval on its own can clear the trade minimum once pooled, which is
    # where a weak but real edge becomes visible.
    stats += [
        metrics.summarise(
            registry[name],
            POOLED,
            [t for iv in config.run.intervals for t in collected.get((name, iv), [])],
            sum(signal_counts.get((name, iv), 0) for iv in config.run.intervals),
            config.stats,
            stats_rng,
        )
        for name in config.patterns
    ]

    stats = metrics.attach_baselines(stats, config.stats)

    if metrics.controls_missing(stats):
        warnings.append(
            "no random-entry control is enabled, so EDGE verdicts rest on the "
            "confidence interval alone and cannot separate a pattern's edge "
            "from a timeframe-wide directional drift."
        )

    return RunResult(
        stats=stats,
        trials=trials,
        symbols=symbols,
        sessions_evaluated=evaluated,
        skipped_sessions=skipped,
        warnings=sorted(set(warnings)),
        trades=[t for key in sorted(collected) for t in collected[key]],
        spread_bps=(float(np.mean(estimates)) * 10_000 if estimates else None),
        spread_interval=spread_interval if estimates else None,
        spread_fallbacks=fallbacks,
    )


def _estimate_spreads(
    config: Config, trials
) -> tuple[dict[tuple[str, object], float], list[float], int, str]:
    """One one-way cost per symbol and session, measured before any simulation.

    The bars come from the narrowest enabled interval, the same authority the
    sampler draws its sessions against, and the resulting cost is then charged at
    every timeframe. `costs` explains why a per-timeframe estimate would be
    wrong; the short version is that the estimator's answer grows with bar length
    and a real spread does not.

    A separate pass over the cache rather than a step inside the main loop: the
    coarse intervals are simulated before the narrow one has been read in some
    interval orderings, and a cost that depended on config ordering would be a
    silent trap.
    """
    interval = sampling.narrowest_interval(config.run.intervals)
    fixed = config.costs.slippage_bps / 10_000.0
    if config.costs.model == "fixed":
        return {}, [], 0, interval

    wanted: dict[str, set] = {}
    for trial in trials:
        wanted.setdefault(trial.symbol, set()).add(trial.session)

    out: dict[tuple[str, object], float] = {}
    estimates: list[float] = []
    fallbacks = 0
    for symbol, days in sorted(wanted.items()):
        try:
            available = bars.sessions(bars.load(symbol, interval, config.cache_path))
        except FileNotFoundError:
            available = {}
        for day in sorted(days):
            frame = available.get(day)
            if frame is None:
                fallbacks += 1
                continue
            one_way, spread = costs.one_way_fraction(
                config.costs, frame["high"].to_numpy(), frame["low"].to_numpy()
            )
            out[(symbol, day)] = one_way
            if spread is None:
                fallbacks += 1
            else:
                estimates.append(spread)
    return out, estimates, fallbacks, interval


def _accumulate(
    collected, signal_counts, spec, interval, mask, geom, config, trial, bar_minutes,
    one_way_cost,
) -> None:
    key = (spec.name, interval)
    signal_counts[key] = signal_counts.get(key, 0) + int(mask.sum())
    collected.setdefault(key, []).extend(
        engine.simulate(
            geom,
            mask,
            spec,
            config.trade,
            config.costs,
            symbol=trial.symbol,
            interval=interval,
            session=trial.session,
            trial_index=trial.index,
            window=trial.window,
            bar_minutes=bar_minutes,
            one_way_cost=one_way_cost,
        )
    )
