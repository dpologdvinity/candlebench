"""Walk-forward confirmation on a real cache, or its power on planted markets.

    python scripts/walk_forward.py run --config tmp/runs/alpaca-1m-117.toml --folds 3 --out wf.json
    python scripts/walk_forward.py power --out docs/experiments/walk-forward.csv --replicates 8
    python scripts/walk_forward.py summary docs/experiments/walk-forward.csv

`power` builds the power study's markets (`detection_power.py`) and, on each,
compares three ways of confirming the planted pattern: the standard single
holdout at 200 trials, the single holdout at 600 trials (the same compute as
three folds), and three-fold walk-forward at 200 trials per fold. Rows append
as markets finish and a rerun skips recorded markets.
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

import detection_power as power

# (persistence, drift); zero drift is noise.
SETTINGS = ((0, 0.0), (1, 0.6), (5, 0.15), (5, 0.22))
FOLDS = 3


def _confirmed(result, pattern: str) -> int:
    return int(any(s.pattern == pattern and s.interval == "1m" and s.verdict == "EDGE"
                   for s in result.stats))


def run_market(persistence: int, drift: float, replicate: int, scratch: str) -> dict:
    from candlebench import bars, runner, synthetic, walkforward
    from candlebench.config import validate

    cache = Path(tempfile.mkdtemp(prefix=f"wf-H{persistence}-k{drift}-r{replicate}-", dir=scratch))
    try:
        cfg = power._config(cache, replicate)  # pylint: disable=protected-access
        planted = synthetic.Planted(power.PATTERN, drift=drift, bars=persistence) if drift else None
        report = bars.warm_cache(
            power.market_symbols(replicate), cfg.run.intervals, cfg.cache_path, 0.0,
            source="synthetic", lookback_days=power.LOOKBACK_DAYS,
            download=synthetic.downloader(planted))
        if report.failures:
            raise RuntimeError(report.summary())
        single = runner.run(cfg)
        triple = runner.run(validate(replace(cfg, run=replace(cfg.run, trials=3 * cfg.run.trials))))
        wf = walkforward.run(cfg, folds=FOLDS)
    finally:
        shutil.rmtree(cache, ignore_errors=True)
    selecting = sum((power.PATTERN, "1m") in f.candidates or (power.PATTERN, "all") in f.candidates
                    for f in wf.folds)
    others = sum(len([c for c in f.candidates if c[0] != power.PATTERN]) for f in wf.folds)
    return {
        "persistence": persistence, "drift": drift, "replicate": replicate,
        "single_200": _confirmed(single, power.PATTERN),
        "single_600": _confirmed(triple, power.PATTERN),
        "walk_forward": int(wf.confirmed),
        "folds_selecting": selecting, "other_selections": others,
        "wf_trades": wf.trades, "wf_dates": wf.dates,
        "wf_expectancy_r": wf.expectancy_r, "wf_delta_r": wf.delta_r,
        "wf_p_expectancy": wf.p_adjusted[0], "wf_p_delta": wf.p_adjusted[1],
    }


def _done(path: Path) -> set:
    if not path.exists():
        return set()
    with path.open() as f:
        return {(int(r["persistence"]), float(r["drift"]), int(r["replicate"]))
                for r in csv.DictReader(f)}


def power_cmd(args) -> int:
    done = _done(args.out)
    for replicate in range(args.first, args.replicates + 1):
        for persistence, drift in SETTINGS:
            if (persistence, drift, replicate) in done:
                continue
            row = run_market(persistence, drift, replicate, str(args.scratch))
            new = not args.out.exists()
            with args.out.open("a", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=list(row), lineterminator="\n")
                if new:
                    writer.writeheader()
                writer.writerow(row)
            print(f"H{persistence} k{drift} r{replicate}: single {row['single_200']}/"
                  f"{row['single_600']} wf {row['walk_forward']} "
                  f"(folds selecting {row['folds_selecting']}, oos trades {row['wf_trades']})",
                  flush=True)
    return 0


def summary_cmd(args) -> int:
    import pandas as pd

    df = pd.read_csv(args.csv)
    table = df.groupby(["persistence", "drift"]).agg(
        markets=("replicate", "nunique"),
        single_200=("single_200", "mean"), single_600=("single_600", "mean"),
        walk_forward=("walk_forward", "mean"), folds_selecting=("folds_selecting", "mean"),
        other_selections=("other_selections", "mean"), oos_trades=("wf_trades", "mean"),
    )
    print(table.round(3).to_string())
    return 0


def run_cmd(args) -> int:
    from candlebench import config as config_module, walkforward

    cfg = config_module.load(args.config)

    def report(fold):
        print(f"fold {fold.index}: test {fold.cutoff} .. {fold.end}, "
              f"{len(fold.candidates)} candidate(s), {fold.oos_trades} out-of-sample trades",
              flush=True)

    result = walkforward.run(cfg, folds=args.folds, on_fold=report)
    payload = result.as_dict()
    print(json.dumps({k: v for k, v in payload.items() if k != "folds"}, indent=2))
    if args.out:
        args.out.write_text(json.dumps(payload, indent=2))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    go = sub.add_parser("run")
    go.add_argument("--config", type=Path, required=True)
    go.add_argument("--folds", type=int, default=FOLDS)
    go.add_argument("--out", type=Path, default=None)
    pw = sub.add_parser("power")
    pw.add_argument("--out", type=Path, required=True)
    pw.add_argument("--replicates", type=int, default=8)
    pw.add_argument("--first", type=int, default=1, help="first replicate, to split work")
    pw.add_argument("--scratch", type=Path, default=Path(tempfile.gettempdir()))
    show = sub.add_parser("summary")
    show.add_argument("csv", type=Path)
    args = parser.parse_args(argv)
    return {"run": run_cmd, "power": power_cmd, "summary": summary_cmd}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
