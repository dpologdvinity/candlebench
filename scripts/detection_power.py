"""How large an edge candlebench can detect, measured by planting one.

Each task builds a synthetic market (`candlebench.synthetic`) in which every
signal of one pattern is followed by a known drift, runs the full pipeline over
it with the real-data design (1m bars, 200 trials, 20% holdout, 10,000
bootstrap draws, every pattern in the family), and records whether the planted
pattern was found. Repeating over independent markets turns that into a
detection rate for each drift size: a power curve.

Tasks append to the output CSV as they finish, and a rerun skips tasks already
recorded, so a long sweep can be stopped and resumed.

    python scripts/detection_power.py run --out docs/experiments/detection-power.csv
    python scripts/detection_power.py summary docs/experiments/detection-power.csv
"""

from __future__ import annotations

import argparse
import csv
import math
import shutil
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import replace
from pathlib import Path

PATTERN = "bullish_engulfing"
SYMBOLS_PER_MARKET = 10
LOOKBACK_DAYS = 420  # about 300 weekday sessions
TRIALS = 200
ALPHA = 0.05

# Drift per bar, in units of that bar's volatility, for each persistence. A
# one-bar edge must be larger per bar to matter; the grids span "nothing" to
# "detected almost always" for the real-data design.
GRIDS = {
    1: (0.0, 0.1, 0.2, 0.3, 0.45, 0.6),
    5: (0.03, 0.06, 0.1, 0.15, 0.22),
}
# Drift 0 plants nothing, so it is the same market whatever the persistence:
# run once (under persistence 1) and shared by both curves in the summary.

FIELDS = [
    "persistence", "drift", "replicate", "trades", "delta_r", "paired_low", "paired_high",
    "p_delta_adjusted", "mde_delta_r", "expectancy_r", "expectancy_r_gross", "ci_low",
    "p_expectancy_adjusted", "mde_expectancy_r", "beats_control", "discovery_verdict",
    "verdict", "validation_verdict", "other_beats_control", "other_edges",
]


def market_symbols(replicate: int) -> tuple[str, ...]:
    """Independent markets: the generator seeds every session from its symbol."""
    return tuple(f"P{replicate:02d}{chr(65 + i)}" for i in range(SYMBOLS_PER_MARKET))


def _config(cache: Path, replicate: int):
    from candlebench import config as config_module

    base = config_module.load(None)
    return config_module.validate(replace(
        base,
        run=replace(base.run, source="synthetic", intervals=("1m",), trials=TRIALS,
                    seed=replicate, cache_dir=str(cache), throttle_s=0.0,
                    lookback_days=LOOKBACK_DAYS),
        universe=replace(base.universe, symbols=market_symbols(replicate),
                         sample_size=SYMBOLS_PER_MARKET),
    ))


def _beats(row) -> bool:
    return (row.p_delta_adjusted is not None and row.p_delta_adjusted <= ALPHA
            and row.baseline_ci_low is not None and row.baseline_ci_low > 0)


def run_task(persistence: int, drift: float, replicate: int, scratch: str,
             clustered: bool = False, pattern: str = PATTERN) -> dict:
    """One market, one planted edge, one full run. Returns the planted row's evidence."""
    from candlebench import bars, runner, synthetic

    cache = Path(tempfile.mkdtemp(prefix=f"power-H{persistence}-k{drift}-r{replicate}-", dir=scratch))
    try:
        cfg = _config(cache, replicate)
        planted = synthetic.Planted(pattern, drift=drift, bars=persistence) if drift else None
        report = bars.warm_cache(
            market_symbols(replicate), cfg.run.intervals, cfg.cache_path, 0.0,
            source="synthetic", lookback_days=LOOKBACK_DAYS,
            download=synthetic.downloader(planted, clustered),
        )
        if report.failures:
            raise RuntimeError(report.summary())
        result = runner.run(cfg)
    finally:
        shutil.rmtree(cache, ignore_errors=True)

    rows = {s.pattern: s for s in result.stats if s.interval == "1m"}
    row = rows[pattern]
    others = [s for name, s in rows.items() if name != pattern and s.kind != "control"]
    validation = row.validation
    return {
        "persistence": persistence, "drift": drift, "replicate": replicate,
        "trades": row.trades, "delta_r": row.baseline_delta_r,
        "paired_low": row.baseline_ci_low, "paired_high": row.baseline_ci_high,
        "p_delta_adjusted": row.p_delta_adjusted, "mde_delta_r": row.mde_delta_r,
        "expectancy_r": row.expectancy_r, "expectancy_r_gross": row.expectancy_r_gross,
        "ci_low": row.ci_low, "p_expectancy_adjusted": row.p_expectancy_adjusted,
        "mde_expectancy_r": row.mde_expectancy_r, "beats_control": int(_beats(row)),
        "discovery_verdict": row.discovery_verdict, "verdict": row.verdict,
        "validation_verdict": validation.verdict if validation is not None else None,
        "other_beats_control": sum(_beats(s) for s in others),
        "other_edges": sum(s.verdict == "EDGE" for s in others),
    }


def _done(path: Path) -> set[tuple[int, float, int]]:
    if not path.exists():
        return set()
    with path.open() as handle:
        return {(int(r["persistence"]), float(r["drift"]), int(r["replicate"]))
                for r in csv.DictReader(handle)}


def run(args) -> int:
    tasks = [(h, k, r) for h, grid in GRIDS.items() if h in args.persistence
             for k in (args.drifts or grid) for r in range(1, args.replicates + 1)]
    done = _done(args.out)
    todo = [t for t in tasks if t not in done]
    print(f"{len(tasks)} tasks, {len(done & set(tasks))} already recorded, {len(todo)} to run",
          file=sys.stderr)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    fresh = not args.out.exists()
    with args.out.open("a", newline="") as handle, \
            ProcessPoolExecutor(max_workers=args.workers) as pool:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, lineterminator="\n")
        if fresh:
            writer.writeheader()
        futures = {
            pool.submit(run_task, *task, str(args.scratch), args.clustered, args.pattern): task
            for task in todo
        }
        for count, future in enumerate(as_completed(futures), 1):
            row = future.result()
            writer.writerow(row)
            handle.flush()
            print(f"[{count}/{len(todo)}] H={row['persistence']} k={row['drift']} "
                  f"r={row['replicate']} delta={row['delta_r']} mde={row['mde_delta_r']} "
                  f"beats={row['beats_control']} verdict={row['verdict']}", file=sys.stderr)
    return 0


def _mean(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def _float(value):
    return None if value in ("", None) else float(value)


def summarise(path: Path) -> list[dict]:
    with path.open() as handle:
        rows = list(csv.DictReader(handle))
    groups: dict[tuple[int, float], list[dict]] = {}
    for r in rows:
        if float(r["drift"]) == 0.0:
            for h in GRIDS:  # the unplanted baseline belongs to every curve
                groups.setdefault((h, 0.0), []).append(r)
        else:
            groups.setdefault((int(r["persistence"]), float(r["drift"])), []).append(r)
    out = []
    for (h, k), group in sorted(groups.items()):
        n = len(group)
        deltas = [_float(r["delta_r"]) for r in group]
        known = [d for d in deltas if d is not None]
        spread = (math.sqrt(sum((d - _mean(known)) ** 2 for d in known) / (len(known) - 1))
                  if len(known) > 1 else None)
        out.append({
            "persistence": h, "drift": k, "markets": n,
            "delta_r": _mean(deltas),
            "delta_se": spread / math.sqrt(len(known)) if spread is not None else None,
            "mde_delta_r": _mean(_float(r["mde_delta_r"]) for r in group),
            "gross_r": _mean(_float(r["expectancy_r_gross"]) for r in group),
            "net_r": _mean(_float(r["expectancy_r"]) for r in group),
            "beats_control": sum(int(r["beats_control"]) for r in group) / n,
            "discovery_edge": sum(r["discovery_verdict"] == "EDGE" for r in group) / n,
            "confirmed_edge": sum(r["verdict"] == "EDGE" for r in group) / n,
            "false_positive_rows": sum(int(r["other_beats_control"]) for r in group) / (19 * n),
        })
    return out


def summary(args) -> int:
    fmt = lambda v, p=3: "n/a" if v is None else f"{v:+.{p}f}"
    print(f"{'H':>2} {'drift':>6} {'mkts':>4} {'delta R':>9} {'±se':>6} {'mde':>7} "
          f"{'gross':>7} {'net':>7} {'beats':>6} {'disc':>5} {'conf':>5} {'fp/row':>7}")
    for s in summarise(args.csv):
        print(f"{s['persistence']:>2} {s['drift']:>6} {s['markets']:>4} {fmt(s['delta_r']):>9} "
              f"{fmt(s['delta_se']):>6} {fmt(s['mde_delta_r']):>7} {fmt(s['gross_r']):>7} "
              f"{fmt(s['net_r']):>7} {s['beats_control']:>6.2f} {s['discovery_edge']:>5.2f} "
              f"{s['confirmed_edge']:>5.2f} {s['false_positive_rows']:>7.3f}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    go = sub.add_parser("run")
    go.add_argument("--out", type=Path, required=True)
    go.add_argument("--replicates", type=int, default=12)
    go.add_argument("--persistence", type=int, nargs="+", default=sorted(GRIDS))
    go.add_argument("--workers", type=int, default=4)
    go.add_argument("--scratch", type=Path, default=Path(tempfile.gettempdir()))
    go.add_argument("--clustered", action="store_true", help="cluster volatility in every market")
    go.add_argument("--pattern", default=PATTERN, help="the pattern to plant an edge after")
    go.add_argument("--drifts", type=float, nargs="+", default=None,
                    help="drift grid, replacing the default for every persistence")
    show = sub.add_parser("summary")
    show.add_argument("csv", type=Path)
    args = parser.parse_args(argv)
    return run(args) if args.command == "run" else summary(args)


if __name__ == "__main__":
    raise SystemExit(main())
