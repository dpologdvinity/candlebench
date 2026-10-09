"""Walk-forward confirmation: pool every fold's out-of-sample trades into one test.

The standard run confirms a discovery on one chronological holdout: the newest
20% of dates. The power study found that weak: with 40 validation trials, a
confirmed EDGE appeared in at most 38% of markets even where discovery succeeded
every time. A holdout cannot simply grow, because every date it takes is a date
discovery loses.

Walk-forward reuses dates instead. The history is cut into an initial window and
K later segments. Fold k discovers on everything before segment k, exactly as a
standard run would, and trades whatever it selects through segment k, which it
has not seen. Each date is out of sample for exactly one fold, and every fold's
choices are made before its test dates. Pooling the folds' out-of-sample trades
gives one question with far more data than a single holdout: does trading what
discovery selects make money, and beat its matched random entries, on dates it
was not chosen on?

That is one strategy-level hypothesis pair, not one per pattern, so there is no
per-pattern multiplicity on the out-of-sample side; the two tests (net
expectancy above zero, gross advantage over matched controls above zero) are
Holm-corrected together and multiplied by the declared experiment count. A fold
that selects nothing trades nothing, and if no fold selects anything the
strategy has no out-of-sample record at all, which is reported as unavailable
rather than as zero.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from datetime import date

import numpy as np

from candlebench import bars, metrics, runner, sampling, universe
from candlebench.config import Config, validate

INITIAL_FRACTION = 0.4


@dataclass(frozen=True)
class Fold:
    index: int
    end: date
    cutoff: date
    candidates: tuple[tuple[str, str], ...]
    oos_trades: int
    oos_dates: int


@dataclass
class WalkForward:
    folds: list[Fold] = field(default_factory=list)
    trades: int = 0
    dates: int = 0
    expectancy_r: float | None = None
    expectancy_ci: tuple[float | None, float | None] = (None, None)
    p_expectancy: float | None = None
    delta_r: float | None = None
    delta_ci: tuple[float | None, float | None] = (None, None)
    p_delta: float | None = None
    p_adjusted: tuple[float | None, float | None] = (None, None)
    confirmed: bool = False

    def as_dict(self) -> dict:
        return {
            "folds": [
                {"fold": f.index, "end": f.end.isoformat(), "cutoff": f.cutoff.isoformat(),
                 "candidates": [{"pattern": p, "interval": i} for p, i in f.candidates],
                 "oos_trades": f.oos_trades, "oos_dates": f.oos_dates}
                for f in self.folds
            ],
            "trades": self.trades, "dates": self.dates,
            "expectancy_r": self.expectancy_r, "expectancy_ci": list(self.expectancy_ci),
            "p_expectancy": self.p_expectancy,
            "delta_r": self.delta_r, "delta_ci": list(self.delta_ci), "p_delta": self.p_delta,
            "p_adjusted": list(self.p_adjusted), "confirmed": self.confirmed,
        }


def segments(dates: list[date], folds: int, initial: float = INITIAL_FRACTION) -> list[list[date]]:
    """The K test segments after the initial window, as lists of dates."""
    if folds < 1:
        raise ValueError("walk-forward needs at least one fold")
    if not 0 < initial < 1:
        raise ValueError("the initial window must be a fraction in (0, 1)")
    start = math.ceil(len(dates) * initial)
    rest = dates[start:]
    if start < 2 or len(rest) < folds:
        raise ValueError(f"{len(dates)} dates are too few for {folds} folds")
    bounds = np.linspace(0, len(rest), folds + 1).round().astype(int)
    return [rest[a:b] for a, b in zip(bounds[:-1], bounds[1:])]


def _matches(trade, candidates: set[tuple[str, str]]) -> bool:
    return (trade.pattern, trade.interval) in candidates or (trade.pattern, "all") in candidates


def run(config: Config, folds: int = 3, initial: float = INITIAL_FRACTION,
        on_fold=None) -> WalkForward:
    """Run every fold and test the pooled out-of-sample trades."""
    symbols = universe.resolve(config.universe.symbols, config.universe.sample_size)
    available = bars.available_sessions(
        symbols, sampling.narrowest_interval(config.run.intervals), config.cache_path)
    dates = sorted({d for days in available.values() for d in days
                    if not config.run.end_date or d <= date.fromisoformat(config.run.end_date)})
    out = WalkForward()
    pooled, matched = [], []
    for k, segment in enumerate(segments(dates, folds, initial)):
        seen = [d for d in dates if d <= segment[-1]]
        # A hair under the segment's share, so the sampler's ceil reserves
        # exactly the segment and not one extra date.
        fraction = (len(segment) - 0.25) / len(seen)
        fold_cfg = validate(replace(config, run=replace(
            config.run, end_date=segment[-1].isoformat(), holdout_fraction=fraction,
            seed=config.run.seed + k)))
        result = runner.run(fold_cfg)
        candidates = {(c["pattern"], c["interval"]) for c in result.validation["candidates"]}
        oos = [t for t in result.trades if t.sample == "validation" and _matches(t, candidates)]
        oos_matched = [t for t in result.matched_trades
                       if t.sample == "validation" and _matches(t, candidates)]
        pooled.extend(oos)
        matched.extend(oos_matched)
        fold = Fold(k, segment[-1], segment[0], tuple(sorted(candidates)), len(oos),
                    len({t.session for t in oos}))
        out.folds.append(fold)
        if on_fold:
            on_fold(fold)
    _test(out, pooled, matched, config)
    return out


def _test(out: WalkForward, trades, matched, config: Config) -> None:
    out.trades = len(trades)
    dates = sorted({t.session for t in trades} | {t.session for t in matched})
    out.dates = len({t.session for t in trades})
    if out.dates < config.stats.min_sessions or not matched:
        return
    rng = np.random.default_rng([config.run.seed, 0x5746])
    samples = config.stats.bootstrap_samples
    net_sums, counts = metrics._date_totals(trades, dates)  # pylint: disable=protected-access
    gross_sums, _ = metrics._date_totals(trades, dates, "gross_r")  # pylint: disable=protected-access
    m_sums, m_counts = metrics._date_totals(matched, dates, "gross_r")  # pylint: disable=protected-access
    draws = metrics._bootstrap_means(  # pylint: disable=protected-access
        np.column_stack([net_sums, gross_sums, m_sums]),
        np.column_stack([counts, counts, m_counts]), samples, rng)
    expectancy = float(net_sums.sum() / counts.sum())
    delta = float(gross_sums.sum() / counts.sum() - m_sums.sum() / m_counts.sum())
    low, high, p_e = metrics._bootstrap_evidence(  # pylint: disable=protected-access
        draws[:, 0], expectancy, two_sided=False)
    d_low, d_high, p_d = metrics._bootstrap_evidence(  # pylint: disable=protected-access
        draws[:, 1] - draws[:, 2], delta, two_sided=False)
    out.expectancy_r, out.expectancy_ci, out.p_expectancy = expectancy, (low, high), p_e
    out.delta_r, out.delta_ci, out.p_delta = delta, (d_low, d_high), p_d
    if p_e is None or p_d is None:
        return
    adjusted = metrics.holm_adjust([p_e, p_d], config.stats.experiment_count)
    out.p_adjusted = (adjusted[0], adjusted[1])
    out.confirmed = (expectancy > 0 and delta > 0
                     and all(p <= metrics.ALPHA for p in adjusted))
