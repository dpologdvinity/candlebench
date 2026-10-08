"""How often the statistics report an edge where none can exist.

Runs the full pipeline over synthetic random-walk bars (see
`candlebench.synthetic`) at several seeds and counts rows that pass each test.
No pattern can predict a driftless random walk, so every pass is a false
positive. Writes one CSV row per seed and interval.

    python scripts/null_calibration.py --seeds 10 --out docs/experiments/null-calibration.csv
"""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import replace
from pathlib import Path

from candlebench import bars, cli, runner, synthetic, universe

ALPHA = 0.05


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seeds", type=int, default=10)
    parser.add_argument("--trials", type=int, default=150)
    parser.add_argument("--cache-dir", type=Path, default=Path(".cache/demo"))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--clustered", action="store_true",
                        help="cluster volatility (use a cache directory of its own)")
    args = parser.parse_args(argv)

    base = cli.demo_config(args.cache_dir, args.trials)
    symbols = universe.resolve(base.universe.symbols, base.universe.sample_size)
    bars.warm_cache(symbols, base.run.intervals, base.cache_path, 0.0, source=base.run.source,
                    download=synthetic.downloader(clustered=args.clustered))

    rows = []
    for seed in range(1, args.seeds + 1):
        config = replace(base, run=replace(base.run, seed=seed))
        result = runner.run(config)
        for interval in (*config.run.intervals, "all"):
            stats = [s for s in result.stats if s.interval == interval and s.kind != "control"]
            rows.append({
                "seed": seed,
                "interval": interval,
                "rows": len(stats),
                "beats_control": sum(
                    1 for s in stats
                    if s.p_delta_adjusted is not None and s.p_delta_adjusted <= ALPHA
                    and s.baseline_ci_low is not None and s.baseline_ci_low > 0
                ),
                "positive_expectancy": sum(
                    1 for s in stats
                    if s.p_expectancy_adjusted is not None and s.p_expectancy_adjusted <= ALPHA
                    and s.ci_low is not None and s.ci_low > 0
                ),
                "discovery_edge": sum(1 for s in stats if s.discovery_verdict == "EDGE"),
                "confirmed_edge": sum(1 for s in stats if s.verdict == "EDGE"),
            })
        print(f"seed {seed}: " + ", ".join(
            f"{r['interval']} {r['beats_control']}/{r['discovery_edge']}"
            for r in rows if r["seed"] == seed), file=sys.stderr)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    totals = {k: sum(r[k] for r in rows) for k in ("rows", "beats_control", "discovery_edge", "confirmed_edge")}
    print(totals)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
