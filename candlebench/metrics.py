"""Per-pattern aggregation.

Expectancy in R is the headline number rather than win rate. Win rate alone is
not a measure of profitability: 70% wins at a 1:1 reward and 35% wins at 3:1
have the same expectancy, and a high win rate with poor expectancy is the most
common way a pattern looks good and loses money.

Unavailable is kept distinct from zero throughout, the same convention the
existing `stock.py` uses for a missing metric. Reporting a Sharpe of 0 for a
pattern with one trade would be a fabricated number.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from operator import attrgetter

import math

import numpy as np

from candlebench.engine import CHRONOLOGICAL, Trade
from candlebench.patterns import PatternSpec
from candlebench.patterns.control import CONTROLS

VERDICTS = ("EDGE", "NOISE", "NEGATIVE", "INSUFFICIENT")


@dataclass(frozen=True)
class PatternStats:
    pattern: str
    interval: str
    bias: str
    kind: str
    signals: int
    trades: int
    win_rate: float | None
    expectancy_r: float | None
    expectancy_r_gross: float | None
    # The sum of per-trade percentage returns, net of all costs. A ranking aid,
    # not a portfolio return: it ignores position overlap, capital and
    # compounding.
    total_return_pct: float
    profit_factor: float | None
    sharpe_per_trade: float | None
    max_drawdown_r: float | None
    avg_bars_held: float | None
    exit_mix: dict[str, float]
    consistency: float | None
    # Expectancy per walk-forward window, for windows with enough trades to
    # mean anything, and the share of those windows that were positive.
    window_expectancy_r: dict[int, float]
    stability: float | None
    ci_low: float | None
    ci_high: float | None
    baseline_delta_r: float | None
    verdict: str
    # Intervals are pointwise 95%; corrected significance is reported separately.
    baseline_ci_low: float | None = None
    baseline_ci_high: float | None = None
    sessions: int = 0
    baseline_sessions: int = 0
    paired_sessions: int = 0
    p_expectancy: float | None = None
    p_delta: float | None = None
    p_expectancy_adjusted: float | None = None
    p_delta_adjusted: float | None = None
    discovery_verdict: str | None = None
    validation: PatternStats | None = None


def _profit_factor(r: np.ndarray) -> float | None:
    wins = r[r > 0].sum()
    losses = -r[r < 0].sum()
    if losses == 0:
        return float("inf") if wins > 0 else None
    return float(wins / losses)


def _max_drawdown_r(trades: list[Trade]) -> float:
    """Deepest peak-to-trough decline of the cumulative R curve.

    Trades are sorted into `engine.CHRONOLOGICAL` order first, the same rule
    `trades.chronological` applies to the frame, so this number and the equity
    curve the browser draws cannot disagree.
    """
    ordered = sorted(trades, key=attrgetter(*CHRONOLOGICAL))
    equity = np.cumsum([t.net_r for t in ordered])
    if not len(equity):
        return 0.0
    # Starting equity is zero: a first loss is already a drawdown.
    peak = np.maximum(0.0, np.maximum.accumulate(equity))
    return float(np.max(peak - equity))


# Bound the temporary resampling matrix, independent of trade count. The
# retained output is only bootstrap_samples x report_rows, never samples x trades.
_BOOTSTRAP_MAX_CELLS = 262_144
_BOOTSTRAP_BATCH = 256


def _bootstrap_means(
    sums: np.ndarray, counts: np.ndarray, samples: int, rng: np.random.Generator
) -> np.ndarray:
    """Resample whole dates; every report column receives identical date weights."""
    dates, columns = sums.shape
    means = np.full((samples, columns), np.nan)
    if not dates or not samples:
        return means
    batch = max(1, min(_BOOTSTRAP_BATCH, _BOOTSTRAP_MAX_CELLS // dates))
    probabilities = np.full(dates, 1.0 / dates)
    for start in range(0, samples, batch):
        stop = min(start + batch, samples)
        weights = rng.multinomial(dates, probabilities, size=stop - start)
        numerators = weights @ sums
        denominators = weights @ counts
        np.divide(numerators, denominators, out=means[start:stop], where=denominators > 0)
    return means


def _date_totals(
    trades: Sequence[Trade], dates: Sequence[date], field: str = "net_r"
) -> tuple[np.ndarray, np.ndarray]:
    positions = {session: index for index, session in enumerate(dates)}
    sums = np.zeros(len(dates))
    counts = np.zeros(len(dates))
    for trade in trades:
        index = positions[trade.session]
        sums[index] += getattr(trade, field)
        counts[index] += 1
    return sums, counts


def _bootstrap_evidence(
    draws: np.ndarray, observed: float, *, two_sided: bool = True, level: float = 0.95
) -> tuple[float | None, float | None, float | None]:
    """Pointwise percentile interval and centered-bootstrap null-tail p-value.

    Undefined draws (zero trades in a resampled sample) count against significance.
    If over 5% are undefined, inference itself is unavailable; silently dropping
    frequent undefined draws would condition on favorable sample availability.
    """
    finite = np.isfinite(draws)
    valid = draws[finite]
    if not len(valid) or finite.mean() < 0.95:
        return None, None, None
    tail = (1 - level) / 2
    low, high = np.quantile(valid, [tail, 1 - tail])
    centered = valid - observed
    null_tail = (np.abs(centered) >= abs(observed) if two_sided
                 else centered >= observed)
    # The +1 correction avoids a fabricated zero p-value from finite simulation.
    p = (int(null_tail.sum()) + int((~finite).sum()) + 1) / (len(draws) + 1)
    return float(low), float(high), float(p)


def bootstrap_ci(
    r: np.ndarray, samples: int, rng: np.random.Generator, level: float = 0.95
) -> tuple[float, float] | tuple[None, None]:
    """Compatibility wrapper treating each value as an independent cluster.

    Production inference uses market dates, not this trade-array interface.
    """
    if len(r) < 2:
        return None, None
    draws = _bootstrap_means(np.asarray(r)[:, None], np.ones((len(r), 1)), samples, rng)
    low, high, _ = _bootstrap_evidence(draws[:, 0], float(np.mean(r)), level=level)
    return low, high


# Familywise significance level every corrected p-value is compared against.
ALPHA = 0.05


def discovery_family_size(kinds: list[str], intervals: int) -> int:
    """Hypotheses in one discovery report's Holm family.

    Every row tests its expectancy; a pattern row also tests its advantage over
    its control. There is a row per pattern for each interval and for the pooled
    view, so five intervals and twenty patterns with two controls make 252.
    """
    per_row_set = sum(1 if kind == "control" else 2 for kind in kinds)
    return per_row_set * (intervals + 1)


def resolution_warning(
    family: int, bootstrap_samples: int, experiment_count: int, alpha: float = ALPHA
) -> str | None:
    """Why no row can pass, when bootstrap resolution makes passing impossible.

    A bootstrap p-value cannot fall below 1 / (B + 1), and Holm multiplies the
    smallest by the family size before the declared experiment count multiplies
    it again. If that floor already exceeds alpha, every verdict is decided
    before the data is read. Clearing the floor makes a pass possible, not
    likely: it says nothing about power.
    """
    floor = min(1.0, family * experiment_count / (bootstrap_samples + 1))
    if floor <= alpha:
        return None
    needed = math.ceil(family * experiment_count / alpha) - 1
    return (
        f"no row can pass the corrected {alpha:g} threshold: with {family} hypotheses, "
        f"{bootstrap_samples:,} bootstrap samples and {experiment_count} declared "
        f"experiment(s), the smallest attainable corrected p-value is {floor:.4f}. "
        f"set stats.bootstrap_samples to at least {needed:,}"
    )


def holm_adjust(p_values: list[float], experiment_count: int = 1) -> list[float]:
    """Holm familywise correction, followed by declared experiment correction."""
    if experiment_count < 1:
        raise ValueError("experiment_count must be at least 1")
    size = len(p_values)
    adjusted = [1.0] * size
    ceiling = 0.0
    for rank, index in enumerate(sorted(range(size), key=lambda i: p_values[i])):
        p = p_values[index]
        if not np.isfinite(p) or not 0 <= p <= 1:
            raise ValueError("p-values must be finite and between 0 and 1")
        ceiling = max(ceiling, (size - rank) * p)
        adjusted[index] = min(1.0, ceiling * experiment_count)
    return adjusted


def _consistency(trades: list[Trade], min_per_trial: int) -> float | None:
    """Share of trials whose own expectancy was positive.

    Only trials with enough trades to mean anything are counted; a trial with a
    single lucky trade would otherwise register as a fully consistent one.
    """
    by_trial: dict[int, list[float]] = {}
    for trade in trades:
        by_trial.setdefault(trade.trial_index, []).append(trade.net_r)

    qualifying = [rs for rs in by_trial.values() if len(rs) >= min_per_trial]
    if not qualifying:
        return None
    return sum(1 for rs in qualifying if float(np.mean(rs)) > 0) / len(qualifying)


def _window_stats(
    trades: list[Trade], min_per_window: int
) -> tuple[dict[int, float], float | None]:
    """Expectancy per walk-forward window, and the share that were positive.

    This is a different question from `consistency`, which counts individual
    trials. A trial is one symbol on one day, so consistency measures whether the
    pattern works on a typical day. Stability measures whether its sign survives
    from one stretch of calendar time to the next, which is what distinguishes an
    edge from a streak.

    The two also need different floors, which is why `min_per_window` is
    `stats_cfg.min_trades` and not the far lower `min_trades_per_trial`.
    Consistency is a proportion over 200 trials, so one thin trial barely moves
    it. Stability has only a handful of windows, and it takes the *sign* of each
    window's mean — so a window counted on three trades would put that sign
    straight into the headline figure, and four such windows would read
    `stab 100%` on twelve trades. Each window's mean is the same kind of claim
    the verdict makes, so it answers to the same minimum.

    With fewer than two qualifying windows the answer is unavailable rather than
    1.0: a single period cannot show that anything persists.
    """
    by_window: dict[int, list[float]] = {}
    for trade in trades:
        by_window.setdefault(trade.window, []).append(trade.net_r)

    expectancy = {
        window: float(np.mean(rs))
        for window, rs in sorted(by_window.items())
        if len(rs) >= min_per_window
    }
    if len(expectancy) < 2:
        return expectancy, None
    positive = sum(1 for value in expectancy.values() if value > 0)
    return expectancy, positive / len(expectancy)


def _verdict(item: PatternStats, stats_cfg, baseline_usable: bool) -> str:
    if (item.trades < stats_cfg.min_trades
            or item.sessions < getattr(stats_cfg, "min_sessions", 10)
            or item.ci_low is None or item.ci_high is None
            or item.p_expectancy_adjusted is None):
        return "INSUFFICIENT"
    if item.ci_high < 0 and item.p_expectancy_adjusted <= ALPHA:
        return "NEGATIVE"
    if item.kind == "control":
        return "NOISE"
    if not baseline_usable:
        return "INSUFFICIENT"
    if (item.ci_low > 0 and item.baseline_ci_low is not None
            and item.baseline_ci_low > 0 and item.p_expectancy_adjusted <= ALPHA
            and item.p_delta_adjusted is not None and item.p_delta_adjusted <= ALPHA):
        return "EDGE"
    return "NOISE"


def summarise(
    spec: PatternSpec,
    interval: str,
    trades: list[Trade],
    signals: int,
    stats_cfg,
    rng: np.random.Generator,
) -> PatternStats:
    """Aggregate one pattern's trades at one interval."""
    r = np.array([t.net_r for t in trades], dtype=np.float64)
    gross = np.array([t.gross_r for t in trades], dtype=np.float64)

    if len(r) == 0:
        return PatternStats(
            pattern=spec.name, interval=interval, bias=spec.bias, kind=spec.kind,
            signals=signals, trades=0, win_rate=None, expectancy_r=None,
            expectancy_r_gross=None, total_return_pct=0.0, profit_factor=None,
            sharpe_per_trade=None, max_drawdown_r=None, avg_bars_held=None,
            exit_mix={}, consistency=None, window_expectancy_r={}, stability=None,
            ci_low=None, ci_high=None,
            baseline_delta_r=None, verdict="INSUFFICIENT",
        )

    reasons = [t.exit_reason for t in trades]
    exit_mix = {reason: reasons.count(reason) / len(reasons) for reason in sorted(set(reasons))}
    dates = sorted({trade.session for trade in trades})
    ci_low = ci_high = p_expectancy = None
    if len(dates) >= max(2, getattr(stats_cfg, "min_sessions", 10)):
        sums, counts = _date_totals(trades, dates)
        draws = _bootstrap_means(sums[:, None], counts[:, None], stats_cfg.bootstrap_samples, rng)
        ci_low, ci_high, p_expectancy = _bootstrap_evidence(draws[:, 0], float(r.mean()))
    window_expectancy, stability = _window_stats(trades, stats_cfg.min_trades)
    deviation = float(r.std(ddof=1)) if len(r) > 1 else 0.0

    return PatternStats(
        pattern=spec.name,
        interval=interval,
        bias=spec.bias,
        kind=spec.kind,
        signals=signals,
        trades=len(r),
        win_rate=float((r > 0).mean()),
        expectancy_r=float(r.mean()),
        expectancy_r_gross=float(gross.mean()),
        total_return_pct=float(sum(t.return_pct for t in trades)),
        profit_factor=_profit_factor(r),
        sharpe_per_trade=float(r.mean() / deviation) if deviation > 0 else None,
        max_drawdown_r=_max_drawdown_r(trades),
        avg_bars_held=float(np.mean([t.bars_held for t in trades])),
        exit_mix=exit_mix,
        consistency=_consistency(trades, stats_cfg.min_trades_per_trial),
        window_expectancy_r=window_expectancy,
        stability=stability,
        ci_low=ci_low,
        ci_high=ci_high,
        baseline_delta_r=None,  # filled by attach_baselines once controls are known
        verdict="NOISE" if len(r) >= stats_cfg.min_trades and ci_low is not None else "INSUFFICIENT",
        sessions=len(dates),
        p_expectancy=p_expectancy if len(r) >= stats_cfg.min_trades else None,
    )


def attach_baselines(
    stats: list[PatternStats], stats_cfg, *,
    trades_by_key: Mapping[tuple[str, str], Sequence[Trade]] | None = None,
    rng: np.random.Generator | None = None,
    sessions: Sequence[date] | None = None,
    family_hypotheses: int | None = None,
    matched_by_key: Mapping[tuple[str, str], Sequence[Trade]] | None = None,
) -> list[PatternStats]:
    """Date-clustered expectancy and paired direction-matched control inference.

    The session universe includes explicit sampled dates with no trades. Identical
    date multiplicities are applied to every symbol, trial and timeframe column.
    Both expectancy and every noncontrol delta count in the Holm family, even
    when unavailable. Validation can preserve the discovery family size by
    passing family_hypotheses; additional hypotheses are conservatively p=1.
    Without underlying trades, display legacy point deltas but never infer EDGE.

    Expectancy is tested net of costs, because that is the profitability claim.
    The control comparison is tested on gross R, because that is the signal
    claim, and net R confounds it with stop width: costs are a fixed number of
    bps, so a pattern whose stop sits further away pays fewer R for the same
    spread. On a synthetic random walk that alone let engulfing patterns beat
    random entry at a corrected p of 0.042; without costs the same rows read
    p = 1.0. Both columns are resampled with the same date weights.

    With `matched_by_key`, each pattern is compared with its own matched
    controls (`engine.simulate_matched`): one random entry per pattern trade,
    with the same stop distance, direction, session and time of day. Gross R
    still depends on stop width through the pessimistic tie rule, and on time
    of day through volatility, so only a control matched on both isolates the
    entry decision. Without it, the shared direction control is used.
    """
    controls = {(s.interval, s.pattern): s for s in stats if s.kind == "control"}
    trade_lists = [list((trades_by_key or {}).get((s.pattern, s.interval), [])) for s in stats]
    dates = sorted(set(sessions or ()) | {t.session for ts in trade_lists for t in ts})
    size = len(stats)
    matched_lists = [
        list((matched_by_key or {}).get((s.pattern, s.interval), [])) if s.kind != "control" else []
        for s in stats
    ]
    dates = sorted(set(dates) | {t.session for ts in matched_lists for t in ts})
    sums = np.zeros((len(dates), 3 * size))
    counts = np.zeros_like(sums)
    for column, ts in enumerate(trade_lists):
        sums[:, column], counts[:, column] = _date_totals(ts, dates)
        sums[:, size + column], counts[:, size + column] = _date_totals(ts, dates, "gross_r")
        sums[:, 2 * size + column], counts[:, 2 * size + column] = _date_totals(
            matched_lists[column], dates, "gross_r")
    every = _bootstrap_means(sums, counts, stats_cfg.bootstrap_samples,
                             rng if rng is not None else np.random.default_rng(0))
    draws, gross_draws = every[:, :size], every[:, size:2 * size]
    matched_draws = every[:, 2 * size:]
    indices = {(s.pattern, s.interval): i for i, s in enumerate(stats)}
    min_sessions = max(2, getattr(stats_cfg, "min_sessions", 10))
    out = []
    usable = []
    hypotheses = []
    locations = []
    for index, item in enumerate(stats):
        control_name = CONTROLS.get(item.bias)
        control = controls.get((item.interval, control_name))
        matched = matched_lists[index]
        if matched_by_key is not None:
            baseline = (float(np.mean([t.gross_r for t in matched]))
                        if item.kind != "control" and matched else None)
        else:
            baseline = None if control is None else control.expectancy_r_gross
        delta = (None if item.kind == "control" or baseline is None
                 or item.expectancy_r_gross is None
                 else item.expectancy_r_gross - baseline)
        ci_low, ci_high = item.ci_low, item.ci_high
        p_expectancy = p_delta = low = high = None
        own_sessions = len({t.session for t in trade_lists[index]})
        control_sessions = common_sessions = 0
        valid_baseline = False
        if trades_by_key is not None:
            ci_low = ci_high = None
            if own_sessions >= min_sessions and item.expectancy_r is not None:
                ci_low, ci_high, p_expectancy = _bootstrap_evidence(draws[:, index], item.expectancy_r)
                if item.trades < stats_cfg.min_trades:
                    p_expectancy = None
            if matched_by_key is not None and item.kind != "control":
                control_sessions = len({t.session for t in matched})
                common_sessions = int(np.count_nonzero(
                    (counts[:, index] > 0) & (counts[:, 2 * size + index] > 0)))
                valid_baseline = (item.trades >= stats_cfg.min_trades
                                  and len(matched) >= stats_cfg.min_trades
                                  and own_sessions >= min_sessions and control_sessions >= min_sessions
                                  and common_sessions >= min_sessions and delta is not None)
                if valid_baseline:
                    low, high, p_delta = _bootstrap_evidence(
                        gross_draws[:, index] - matched_draws[:, index], delta,
                        two_sided=False)
                    valid_baseline = p_delta is not None
            elif control is not None and item.kind != "control":
                control_index = indices[(control.pattern, control.interval)]
                control_sessions = len({t.session for t in trade_lists[control_index]})
                common_sessions = int(np.count_nonzero((counts[:, index] > 0) & (counts[:, control_index] > 0)))
                valid_baseline = (item.trades >= stats_cfg.min_trades
                                  and control.trades >= stats_cfg.min_trades
                                  and own_sessions >= min_sessions and control_sessions >= min_sessions
                                  and common_sessions >= min_sessions and delta is not None)
                if valid_baseline:
                    low, high, p_delta = _bootstrap_evidence(
                        gross_draws[:, index] - gross_draws[:, control_index], delta,
                        two_sided=False)
                    valid_baseline = p_delta is not None
        out.append(replace(item, ci_low=ci_low, ci_high=ci_high,
                           sessions=own_sessions if trades_by_key is not None else item.sessions,
                           baseline_delta_r=delta, baseline_ci_low=low, baseline_ci_high=high,
                           baseline_sessions=control_sessions, paired_sessions=common_sessions,
                           p_expectancy=p_expectancy, p_delta=p_delta,
                           p_expectancy_adjusted=None, p_delta_adjusted=None))
        usable.append(valid_baseline)
        hypotheses.append(p_expectancy if p_expectancy is not None else 1.0)
        locations.append((index, "p_expectancy_adjusted"))
        if item.kind != "control":
            hypotheses.append(p_delta if p_delta is not None else 1.0)
            locations.append((index, "p_delta_adjusted"))
    if family_hypotheses is not None:
        if family_hypotheses < len(hypotheses):
            raise ValueError("family_hypotheses cannot be smaller than the report family")
        hypotheses.extend([1.0] * (family_hypotheses - len(hypotheses)))
    adjusted = holm_adjust(hypotheses, getattr(stats_cfg, "experiment_count", 1))
    for (index, field), p in zip(locations, adjusted):
        out[index] = replace(out[index], **{field: p})
    return [replace(item, verdict=_verdict(item, stats_cfg, baseline_usable))
            for item, baseline_usable in zip(out, usable)]


def controls_missing(stats: list[PatternStats]) -> bool:
    return not any(s.kind == "control" for s in stats)
