"""Ranking and rendering.

The default ranking key is the *lower bound* of the expectancy interval, which
prefers a modest well-evidenced edge over a large unreliable one. Control rows
stay in the table at their earned rank, so the noise floor is visible rather
than something the reader has to take on trust.
"""

from __future__ import annotations

import csv
import json
from dataclasses import asdict
from pathlib import Path

from candlebench.config import Config
from candlebench.metrics import PatternStats
from candlebench.runner import POOLED, RunResult

NA = "n/a"

_SORT_KEYS = {
    "ci_low": lambda s: s.ci_low,
    "expectancy_r": lambda s: s.expectancy_r,
    "win_rate": lambda s: s.win_rate,
    "profit_factor": lambda s: s.profit_factor,
    "total_return_pct": lambda s: s.total_return_pct,
}


def rank(stats: list[PatternStats], rank_by: str) -> list[PatternStats]:
    """Sort best to worst, with unmeasurable patterns last rather than dropped."""
    key = _SORT_KEYS[rank_by]

    def sort_key(s: PatternStats):
        value = key(s)
        measurable = value is not None and s.verdict != "INSUFFICIENT"
        return (
            0 if measurable else 1,
            -(value if value is not None else 0.0),
            -(s.win_rate or 0.0),
            s.pattern,
        )

    return sorted(stats, key=sort_key)


def _num(value: float | None, spec: str = "+.2f") -> str:
    if value is None:
        return NA
    if value == float("inf"):
        return "inf"
    return format(value, spec)


def _pct(value: float | None) -> str:
    return NA if value is None else f"{value * 100:.1f}"


def _ci(s: PatternStats) -> str:
    if s.ci_low is None or s.ci_high is None:
        return NA
    return f"[{s.ci_low:+.2f},{s.ci_high:+.2f}]"


def _table(stats: list[PatternStats], rank_by: str) -> list[str]:
    header = (
        f"  {'#':>2}  {'pattern':<22}{'trades':>7}{'win%':>7}{'exp R':>8}"
        f"{'95% CI':>16}{'vs ctrl':>9}{'PF':>7}{'consist':>9}  verdict"
    )
    lines = [header, "  " + "-" * (len(header) - 2)]
    for position, s in enumerate(rank(stats, rank_by), start=1):
        label = s.pattern + (" *" if s.kind == "control" else "")
        lines.append(
            f"  {position:>2}  {label:<22}{s.trades:>7}{_pct(s.win_rate):>7}"
            f"{_num(s.expectancy_r):>8}{_ci(s):>16}{_num(s.baseline_delta_r):>9}"
            f"{_num(s.profit_factor, '.2f'):>7}{_pct(s.consistency):>9}  {s.verdict}"
        )
    return lines


def cost_dominated(stats: list[PatternStats]) -> bool:
    """True when random entry itself reliably loses at this interval.

    When it does, trading costs exceed whatever edge any pattern could have,
    so every pattern lands on NEGATIVE and the verdict column stops
    discriminating between them. Saying so is necessary: a NEGATIVE pattern
    with a positive `vs ctrl` has real signal that the costs consumed, and a
    reader comparing verdicts alone would not see the difference.
    """
    return any(s.kind == "control" and s.verdict == "NEGATIVE" for s in stats)


def _verbose_table(stats: list[PatternStats], rank_by: str) -> list[str]:
    header = (
        f"  {'pattern':<22}{'gross R':>9}{'net R':>8}{'maxDD R':>9}"
        f"{'bars':>7}{'stab':>7}{'signals':>9}  exits"
    )
    lines = [header, "  " + "-" * (len(header) - 2)]
    for s in rank(stats, rank_by):
        mix = ", ".join(f"{k} {v*100:.0f}%" for k, v in s.exit_mix.items()) or NA
        lines.append(
            f"  {s.pattern:<22}{_num(s.expectancy_r_gross):>9}{_num(s.expectancy_r):>8}"
            f"{_num(s.max_drawdown_r, '.2f'):>9}"
            f"{_num(s.avg_bars_held, '.1f'):>7}{_pct(s.stability):>7}"
            f"{s.signals:>9}  {mix}"
        )
    return lines


def describe_costs(result: RunResult, config: Config) -> str:
    """One phrase naming the spread that was actually charged.

    Under the estimated model the figure differs per interval and is measured
    rather than configured, so printing the config value would misreport the
    run. Intervals whose spread could not be estimated fell back to the fixed
    cost, and are named as such instead of being silently folded in.
    """
    if config.costs.model == "fixed" or result.spread_bps is None:
        reason = (
            "" if config.costs.model == "fixed" else " (no spread could be estimated)"
        )
        return f"{config.costs.slippage_bps:g} bps fixed slippage{reason}"

    note = (
        f", {result.spread_fallbacks} session(s) fell back to "
        f"{config.costs.slippage_bps:g} bps"
        if result.spread_fallbacks
        else ""
    )
    return (
        f"an estimated {result.spread_bps / 2:.2f} bps per leg "
        f"({result.spread_bps:.2f} bps round trip, measured from "
        f"{result.spread_interval} bars){note}"
    )


# Below this span, windows cannot be separate regimes whatever the count.
ONE_REGIME_DAYS = 90


def describe_windows(result: RunResult, config: Config) -> str:
    """What the walk-forward windows actually cover.

    This used to be a fixed sentence about Yahoo's 28-day cap, which became
    wrong the moment a source with years of history existed: six windows over two
    calendar years are four-month periods, and calling them adjacent weeks of one
    regime understates the run's own evidence. A report that misdescribes its
    evidence is wrong in the same way as one that overstates it.
    """
    days = sorted({t.session for t in result.trials})
    if not days:
        return "no sessions were evaluated, so the windows measured nothing."

    span = (days[-1] - days[0]).days
    each = span // config.run.windows
    if span < ONE_REGIME_DAYS:
        return (
            f"the cache spans {span} days, so these windows are adjacent stretches "
            "of one market regime rather than independent regimes. stability within "
            "one regime is necessary for an edge and nowhere near sufficient."
        )
    return (
        f"the windows are about {each} days each, spanning {days[0]} to {days[-1]}, "
        "so they are distinct periods rather than adjacent weeks. they are still "
        "one asset class over one stretch of history, so a sign that survives here "
        "has not been tested against a regime this window set does not contain."
    )


def render(result: RunResult, config: Config, verbose: bool = False) -> str:
    """The full report: one table per interval, then the pooled view."""
    out: list[str] = []
    costs = (
        f"net of {describe_costs(result, config)}"
        f"{f' and ${config.costs.commission_per_trade:g}/trade' if config.costs.commission_per_trade else ''}"
    )
    out.append("")
    out.append(
        f"candlebench  -  {len(result.trials)} trials, {len(result.symbols)} symbols, "
        f"{result.sessions_evaluated} sessions evaluated, {costs}"
    )
    out.append(
        f"stop at pattern extreme -{config.trade.stop_buffer:.3%}, "
        f"target {config.trade.reward_multiple:g}R, "
        f"max hold {config.trade.max_hold_bars} bars, seed {config.run.seed}"
    )
    if config.run.windows > 1:
        out.append(
            f"trials split across {config.run.windows} walk-forward windows; "
            "'stab' is the share of windows whose expectancy was positive."
        )
        out.append("        " + describe_windows(result, config))

    for interval in (*config.run.intervals, POOLED):
        subset = [s for s in result.stats if s.interval == interval]
        if not subset:
            continue
        out.append("")
        out.append("all intervals pooled" if interval == POOLED else interval)
        out += _table(subset, config.stats.rank_by)
        if cost_dominated(subset):
            out.append(
                "  note: random entry itself loses here, so costs exceed any"
                " pattern edge at this interval."
            )
            out.append(
                "        read the 'vs ctrl' column, not the verdict: a positive"
                " delta is real signal the costs ate."
            )
        if verbose:
            out.append("")
            out += _verbose_table(subset, config.stats.rank_by)

    out.append("")
    out.append("* = random-entry control, the noise floor for its direction")
    out.append(
        "EDGE = interval excludes zero and beats its control; NOISE = "
        "indistinguishable from chance; NEGATIVE = reliably loses; "
        f"INSUFFICIENT = under {config.stats.min_trades} trades"
    )
    if result.skipped_sessions:
        out.append(
            f"skipped {result.skipped_sessions} trial/interval pairs with too few bars "
            "(holidays, half days, or no coverage at that interval)"
        )
    for warning in result.warnings:
        out.append(f"warning: {warning}")
    out.append("")
    return "\n".join(out)


def payload_config(config: Config) -> dict:
    """The config as plain data, for the JSON report and the web API."""
    return {
        "run": asdict(config.run),
        "universe": asdict(config.universe),
        "trade": asdict(config.trade),
        "costs": asdict(config.costs),
        "thresholds": asdict(config.thresholds),
        "stats": asdict(config.stats),
        "patterns": list(config.patterns),
    }


def payload(result: RunResult, config: Config) -> dict:
    """The run as plain data, including the config and seed so it reproduces.

    Shared by the JSON file output and the web API, so the browser and a saved
    report can never disagree about what a run produced.
    """
    return {
        "config": payload_config(config),
        "sessions_evaluated": result.sessions_evaluated,
        "skipped_sessions": result.skipped_sessions,
        "spread_bps": result.spread_bps,
        "spread_interval": result.spread_interval,
        "spread_fallbacks": result.spread_fallbacks,
        "costs_description": describe_costs(result, config),
        "trials": [
            {"index": t.index, "symbol": t.symbol, "session": t.session.isoformat()}
            for t in result.trials
        ],
        "stats": [asdict(s) for s in result.stats],
        "warnings": result.warnings,
        "cost_dominated": sorted(
            {s.interval for s in result.stats if cost_dominated(
                [x for x in result.stats if x.interval == s.interval]
            )}
        ),
    }


def write_json(result: RunResult, config: Config, path: Path) -> None:
    """Machine-readable output including the config and seed, so it reproduces."""
    Path(path).write_text(json.dumps(payload(result, config), indent=2, default=str))


def write_csv(result: RunResult, path: Path) -> None:
    """A flat table, one row per pattern and interval.

    Unlike the JSON output this carries no config echo, so pair it with the
    JSON when a result needs to be reproducible.
    """
    rows = [asdict(s) for s in result.stats]
    if not rows:
        return
    with Path(path).open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        for row in rows:
            row["exit_mix"] = ";".join(f"{k}={v:.3f}" for k, v in row["exit_mix"].items())
            row["window_expectancy_r"] = ";".join(
                f"{k}={v:+.4f}" for k, v in row["window_expectancy_r"].items()
            )
            writer.writerow(row)
