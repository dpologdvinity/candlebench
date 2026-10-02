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

from dataclasses import dataclass
from operator import attrgetter

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
    peak = np.maximum.accumulate(equity)
    return float(np.max(peak - equity))


def bootstrap_ci(
    r: np.ndarray, samples: int, rng: np.random.Generator, level: float = 0.95
) -> tuple[float, float] | tuple[None, None]:
    """Percentile bootstrap interval for the mean.

    Resampling the trades themselves, rather than assuming a normal
    distribution, matters here because R multiples are sharply bimodal: most
    trades land near -1 or near +reward_multiple.
    """
    if len(r) < 2:
        return None, None
    draws = rng.choice(r, size=(samples, len(r)), replace=True).mean(axis=1)
    tail = (1 - level) / 2
    low, high = np.quantile(draws, [tail, 1 - tail])
    return float(low), float(high)


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


def _verdict(
    trades: int,
    min_trades: int,
    ci_low: float | None,
    ci_high: float | None,
    baseline_delta: float | None,
) -> str:
    if trades < min_trades or ci_low is None or ci_high is None:
        return "INSUFFICIENT"
    if ci_high < 0:
        return "NEGATIVE"
    if ci_low > 0 and (baseline_delta is None or baseline_delta > 0):
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
    ci_low, ci_high = bootstrap_ci(r, stats_cfg.bootstrap_samples, rng)
    window_expectancy, stability = _window_stats(trades, stats_cfg.min_trades_per_trial)
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
        verdict="NOISE",
    )


def attach_baselines(stats: list[PatternStats], stats_cfg) -> list[PatternStats]:
    """Compare each pattern against the control matching its bias and interval.

    Without a control the leaderboard cannot tell a real edge from a
    timeframe-wide directional drift, so the absence is surfaced rather than
    silently treated as a zero baseline.
    """
    from dataclasses import replace

    controls = {
        (s.interval, s.pattern): s.expectancy_r for s in stats if s.kind == "control"
    }

    out = []
    for item in stats:
        control_name = CONTROLS.get(item.bias)
        baseline = controls.get((item.interval, control_name))
        delta = (
            None
            if item.kind == "control" or baseline is None or item.expectancy_r is None
            else item.expectancy_r - baseline
        )
        out.append(
            replace(
                item,
                baseline_delta_r=delta,
                verdict=_verdict(
                    item.trades, stats_cfg.min_trades, item.ci_low, item.ci_high, delta
                ),
            )
        )
    return out


def controls_missing(stats: list[PatternStats]) -> bool:
    return not any(s.kind == "control" for s in stats)
