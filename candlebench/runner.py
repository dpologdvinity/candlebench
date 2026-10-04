"""Orchestration: trials in, per-pattern statistics out.

The loop order matters. For one trial and one interval, every pattern is
detected against the same `Geometry` object, which both guarantees the paired
comparison the sampler set up and means the shared bar measurements are
computed once rather than twenty times.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Callable

import numpy as np

from candlebench import bars, costs, engine, metrics, patterns, quotes, sampling, universe
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
    # Mean half-spread in bps actually charged per leg, and how many of the
    # charges came from observed quotes rather than from the estimator or the
    # flat fallback. Reported because the cost term is what the headline finding
    # rests on.
    charged_bps: float | None = None
    quoted_share: float | None = None
    # Share of bars per interval with open == high == low == close. Such a bar
    # is a perfect doji, and the doji, dragonfly, gravestone and hammer
    # detectors all read exactly that geometry.
    flat_bar_share: dict[str, float] = field(default_factory=dict)
    validation: dict = field(default_factory=dict)
    trend_lookbacks: dict[str, int] = field(default_factory=dict)


def _signal_rate(mask: np.ndarray, first_valid: int) -> float:
    eligible = len(mask) - first_valid
    return float(mask.sum() / eligible) if eligible > 0 else 0.0


# Bars that must remain after the trend window and the longest pattern, so
# that signals can actually fire and their trades have room to resolve.
MIN_USABLE_BARS = 5
MIN_TREND_LOOKBACK = 2

# Share of rangeless bars above which the geometry stops meaning what the
# detectors assume. Measured: 0.0% at 1m, 30% to 68% at 1s.
FLAT_BAR_WARNING = 0.05


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
        windows=config.run.windows, holdout_fraction=config.run.holdout_fraction,
    )

    collected: dict[tuple[str, str], list[engine.Trade]] = {}
    signal_counts: dict[tuple[str, str], int] = {}
    estimates, fallbacks, spread_interval = [], 0, None
    all_trades = []
    selected = set()
    discovery_stats = []
    lookbacks = {}
    phase_dates = {}
    validation_evaluated = False
    fixed_one_way = config.costs.slippage_bps / 10_000.0
    quote_table = (
        quotes.read_table(config.costs.quote_table)
        if config.costs.model == "quoted"
        else {}
    )
    charged: list[float] = []
    from_quotes = 0
    flat_bars: dict[str, list[int]] = {iv: [0, 0] for iv in config.run.intervals}
    warnings: list[str] = []
    evaluated = 0
    skipped = 0
    done = 0

    for sample in ("discovery", "validation"):
        phase_trials = [t for t in trials if t.sample == sample]
        if not phase_trials or (sample == "validation" and not selected):
            continue
        validation_evaluated = validation_evaluated or sample == "validation"
        collected = {}
        signal_counts = {}
        evaluated_dates = set()
        spread_cost, phase_estimates, phase_fallbacks, phase_interval = _estimate_spreads(config, phase_trials)
        estimates.extend(phase_estimates)
        fallbacks += phase_fallbacks
        spread_interval = spread_interval or phase_interval
        for interval in config.run.intervals:
            cached: dict[str, dict] = {}
            for trial in phase_trials:
                if trial.symbol not in cached:
                    try:
                        cached[trial.symbol] = bars.sessions(
                            bars.load(trial.symbol, interval, config.cache_path)
                        )
                    except FileNotFoundError as exc:
                        cached[trial.symbol] = {}
                        warnings.append(str(exc))

            if sample == "discovery":
                lookback, note = _trend_lookback_for(
                    interval, cached, phase_trials, config.thresholds.trend_lookback,
                    max((spec.bars_required for spec in enabled), default=1),
                )
                lookbacks[interval] = lookback
                if note:
                    warnings.append(note)
            else:
                lookback = lookbacks[interval]

            for trial in phase_trials:
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
                seen, flat = flat_bars[interval]
                flat_bars[interval] = [
                    seen + len(geom),
                    flat + int(
                        (
                            (geom.open == geom.high)
                            & (geom.high == geom.low)
                            & (geom.low == geom.close)
                        ).sum()
                    ),
                ]
                # One cost per symbol and session, shared by every pattern below and
                # by every timeframe: the spread belongs to the market, not to the
                # detector that traded it or to the bar size used to look at it.
                session_cost = spread_cost.get(
                    (trial.symbol, trial.session), fixed_one_way
                )
                # A per-bar cost when quotes are available for this symbol, because
                # the observed spread at the open runs a median 4.0x midday. The
                # session-wide figure is the fallback, not the first choice.
                one_way_cost = session_cost
                if quote_table:
                    per_bar = np.array(
                        [
                            (quotes.lookup(quote_table, trial.symbol, int(minute)) or 0.0)
                            / 10_000.0
                            for minute in bar_minutes
                        ]
                    )
                    if per_bar.any():
                        one_way_cost = np.where(per_bar > 0, per_bar, session_cost)
                        from_quotes += 1
                charged.append(float(np.mean(one_way_cost)) * 10_000)
                evaluated += 1
                evaluated_dates.add(trial.session)
                rng = np.random.default_rng(trial.seed)
                rates: list[float] = []

                for spec in real:
                    mask = patterns.detect(spec, geom, config.thresholds)
                    first_valid = patterns.first_valid_index(spec, geom.trend_lookback)
                    rates.append(_signal_rate(mask, first_valid))
                    if sample == "discovery" or (spec.name, interval) in selected or (spec.name, POOLED) in selected:
                        _accumulate(collected, signal_counts, spec, interval, mask, geom,
                                    config, trial, bar_minutes, one_way_cost)

                rate = control.matched_rate(rates)
                for spec in controls:
                    first_valid = patterns.first_valid_index(spec, geom.trend_lookback)
                    mask = control.control_mask(len(geom), rate, first_valid, rng)
                    mask = patterns.apply_gates(mask, spec, geom)
                    _accumulate(collected, signal_counts, spec, interval, mask, geom,
                                config, trial, bar_minutes, one_way_cost)


        phase_dates[sample] = sorted(evaluated_dates)
        all_trades.extend(t for ts in collected.values() for t in ts)
        phase_stats = _summarise_phase(config, registry, collected, signal_counts,
                                       sorted(evaluated_dates), sample)
        if sample == "discovery":
            discovery_stats = phase_stats
            selected = {(s.pattern, s.interval) for s in phase_stats if s.verdict == "EDGE"}
        else:
            validation_stats = {(s.pattern, s.interval): s for s in phase_stats}

    stats = []
    for item in discovery_stats:
        evidence = validation_stats.get((item.pattern, item.interval)) if validation_evaluated and (item.pattern, item.interval) in selected else None
        verdict = item.verdict
        if verdict == "EDGE" and (evidence is None or evidence.verdict != "EDGE"):
            verdict = "NOISE"
        stats.append(replace(item, discovery_verdict=item.verdict, validation=evidence, verdict=verdict))
    cutoff = next((t.holdout_start for t in trials if t.holdout_start is not None), None)
    validation_info = {
        "enabled": bool(config.run.holdout_fraction),
        "cutoff": cutoff.isoformat() if cutoff else None,
        "discovery_trials": sum(t.sample == "discovery" for t in trials),
        "validation_trials": sum(t.sample == "validation" for t in trials),
        "candidates": [{"pattern": name, "interval": iv} for name, iv in sorted(selected)],
        "evaluated": validation_evaluated,
        "discovery_sessions": len(phase_dates.get("discovery", [])),
        "validation_sessions": len(phase_dates.get("validation", [])),
        "warning": "Validation must stay unseen during tuning. Reusing it to choose settings invalidates confirmation.",
    }
    if not config.run.holdout_fraction:
        warnings.append("holdout validation is disabled; this run is exploratory and discovery candidates cannot establish confirmed EDGE")
    elif not selected:
        warnings.append("no discovery candidates passed corrected tests; the holdout was not evaluated")
    if validation_evaluated and len(phase_dates.get("validation", [])) < config.stats.min_sessions:
        warnings.append(f"validation covers fewer than {config.stats.min_sessions} independent sessions; confirmation is unavailable")
    if config.costs.model == "estimated" and spread_interval is None:
        warnings.append(
            "every enabled interval is sub-minute, so the spread cannot be "
            "estimated: the high-low estimator collapses toward zero when most "
            "bars have no range, and would charge almost nothing. the fixed "
            f"{config.costs.slippage_bps:g} bps slippage is charged instead. "
            "enable 1m alongside to price the run from bars that can carry the "
            "estimate."
        )

    flat_share = {
        interval: (flat / seen if seen else 0.0)
        for interval, (seen, flat) in flat_bars.items()
    }
    for interval, share in sorted(flat_share.items()):
        if share >= FLAT_BAR_WARNING:
            warnings.append(
                f"{interval}: {share:.1%} of bars have no range at all — open, "
                "high, low and close are identical. such a bar is real, not "
                "fabricated, but it is a perfect doji, and the doji, dragonfly, "
                "gravestone and hammer detectors read exactly that geometry. at "
                "this interval they are largely measuring how often a single "
                "eligible print lands in one bar rather than indecision between "
                "buyers and sellers. for comparison the same measure is 0.0% at "
                "1m."
            )

    if metrics.controls_missing(stats):
        warnings.append(
            "no random-entry control is enabled; a pattern cannot establish EDGE without a usable matched control."
        )

    return RunResult(
        stats=stats,
        trials=trials,
        symbols=symbols,
        sessions_evaluated=evaluated,
        skipped_sessions=skipped,
        warnings=sorted(set(warnings)),
        trades=sorted(all_trades, key=lambda t: (t.sample, t.pattern, t.interval, t.trial_index, t.entry_index)),
        validation=validation_info,
        trend_lookbacks=lookbacks,
        spread_bps=(float(np.mean(estimates)) * 10_000 if estimates else None),
        spread_interval=spread_interval if estimates else None,
        spread_fallbacks=fallbacks,
        flat_bar_share=flat_share,
        charged_bps=(float(np.mean(charged)) if charged else None),
        quoted_share=(from_quotes / len(charged) if charged else None),
    )



def _summarise_phase(config, registry, collected, signal_counts, dates, sample):
    rng = np.random.default_rng(config.run.seed + (1 if sample == "discovery" else 2))
    trades_by_key = dict(collected)
    for name in config.patterns:
        trades_by_key[(name, POOLED)] = [
            t for iv in config.run.intervals for t in collected.get((name, iv), [])
        ]
    stats = [
        metrics.summarise(registry[name], interval, trades_by_key.get((name, interval), []),
                          sum(signal_counts.get((name, iv), 0) for iv in config.run.intervals)
                          if interval == POOLED else signal_counts.get((name, interval), 0),
                          config.stats, rng)
        for interval in (*config.run.intervals, POOLED) for name in config.patterns
    ]
    return metrics.attach_baselines(stats, config.stats, trades_by_key=trades_by_key,
                                     rng=rng, sessions=dates)


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
    from candlebench import ticks

    # Never from a sub-minute interval. Corwin-Schultz collapses toward zero
    # when most bars have no range, and 30% to 68% of 1s bars have none:
    # measured on the same symbol and day it returns 2.028 bps round trip from
    # 1m bars and 0.074 from 1s. A 1s run pricing itself from 1s bars would
    # charge almost nothing and report cost-dominated patterns as merely NOISE.
    priceable = [i for i in config.run.intervals if not ticks.is_sub_minute(i)]
    fixed = config.costs.slippage_bps / 10_000.0
    if not priceable:
        return {}, [], 0, None
    interval = sampling.narrowest_interval(tuple(priceable))
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
        replace(trade, sample=trial.sample) for trade in engine.simulate(
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
