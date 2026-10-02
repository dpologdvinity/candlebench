"""Orchestration: trials in, per-pattern statistics out.

The loop order matters. For one trial and one interval, every pattern is
detected against the same `Geometry` object, which both guarantees the paired
comparison the sampler set up and means the shared bar measurements are
computed once rather than twenty times.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from candlebench import bars, engine, metrics, patterns, sampling, universe
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


def _signal_rate(mask: np.ndarray, first_valid: int) -> float:
    eligible = len(mask) - first_valid
    return float(mask.sum() / eligible) if eligible > 0 else 0.0


def run(config: Config) -> RunResult:
    """Execute every trial against every enabled pattern and interval."""
    registry = patterns.registry()
    enabled = [registry[name] for name in config.patterns]
    real = [s for s in enabled if s.kind == "pattern"]
    controls = [s for s in enabled if s.kind == "control"]

    symbols = universe.resolve(config.universe.symbols, config.universe.sample_size)
    root = np.random.default_rng(config.run.seed)
    trials = sampling.draw_trials(
        symbols, config.run.intervals, config.cache_path, config.run.trials, root
    )

    collected: dict[tuple[str, str], list[engine.Trade]] = {}
    signal_counts: dict[tuple[str, str], int] = {}
    warnings: list[str] = []
    evaluated = 0
    skipped = 0

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

            frame = cached[trial.symbol].get(trial.session)
            # A holiday, a half day, or a symbol with no coverage at this
            # interval yields too few bars to measure. Skipping is counted and
            # reported rather than passed off as a zero result.
            if frame is None or len(frame) < config.thresholds.trend_lookback + 4:
                skipped += 1
                continue

            geom = context.geometry(
                bars.to_arrays(frame),
                config.thresholds.trend_lookback,
                config.thresholds.trend_min_slope,
            )
            evaluated += 1
            rng = np.random.default_rng(trial.seed)
            rates: list[float] = []

            for spec in real:
                mask = patterns.detect(spec, geom, config.thresholds)
                first_valid = patterns.first_valid_index(spec, geom.trend_lookback)
                rates.append(_signal_rate(mask, first_valid))
                _accumulate(collected, signal_counts, spec, interval, mask, geom,
                            config, trial)

            rate = control.matched_rate(rates)
            for spec in controls:
                first_valid = patterns.first_valid_index(spec, geom.trend_lookback)
                mask = control.control_mask(len(geom), rate, first_valid, rng)
                mask = patterns.apply_gates(mask, spec, geom)
                _accumulate(collected, signal_counts, spec, interval, mask, geom,
                            config, trial)

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
    )


def _accumulate(collected, signal_counts, spec, interval, mask, geom, config, trial) -> None:
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
        )
    )
