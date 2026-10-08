"""How the matched controls' entry window trades bias against power.

Matched controls enter one to five bars after their pattern
(`engine.MATCH_ENTRY_BARS`). An edge lasting longer than that is partly shared
with the controls, so later windows should capture more of it, at the price of
controls drawn from a market further from the signal. This measures both sides
on the power study's markets (`detection_power.py`): pure noise, two slow
5-bar edges and one 1-bar edge. Each market is generated once and run under
every window, so the windows are compared on identical trades.

Rows append to the output CSV as markets finish, and a rerun skips markets
already recorded.

    python scripts/matched_window.py run --out docs/experiments/matched-window.csv --replicates 16
    python scripts/matched_window.py summary docs/experiments/matched-window.csv
"""

from __future__ import annotations

import argparse
import csv
import shutil
import sys
import tempfile
from pathlib import Path

import detection_power as power

WINDOWS = ((1, 5), (1, 15), (1, 30), (6, 30), (11, 30))
# (persistence in bars, drift per bar in units of volatility); 0 drift is noise.
SETTINGS = ((0, 0.0), (5, 0.1), (5, 0.15), (1, 0.3))


def run_market(persistence: int, drift: float, replicate: int, scratch: str) -> list[dict]:
    """One market under every window: one row per pattern and window."""
    from candlebench import bars, engine, runner, synthetic

    prefix = f"window-H{persistence}-k{drift}-r{replicate}-"
    cache = Path(tempfile.mkdtemp(prefix=prefix, dir=scratch))
    default = engine.MATCH_ENTRY_BARS
    rows = []
    try:
        cfg = power._config(cache, replicate)  # pylint: disable=protected-access
        planted = synthetic.Planted(power.PATTERN, drift=drift, bars=persistence) if drift else None
        report = bars.warm_cache(
            power.market_symbols(replicate), cfg.run.intervals, cfg.cache_path, 0.0,
            source="synthetic", lookback_days=power.LOOKBACK_DAYS,
            download=synthetic.downloader(planted),
        )
        if report.failures:
            raise RuntimeError(report.summary())
        for first, last in WINDOWS:
            engine.MATCH_ENTRY_BARS = (first, last)
            result = runner.run(cfg)
            for s in result.stats:
                if s.interval != "1m" or s.kind == "control":
                    continue
                rows.append({
                    "persistence": persistence, "drift": drift, "replicate": replicate,
                    "lo": first, "hi": last, "pattern": s.pattern, "trades": s.trades,
                    "gross_r": s.expectancy_r_gross, "delta_r": s.baseline_delta_r,
                    "p_delta_adjusted": s.p_delta_adjusted, "paired_low": s.baseline_ci_low,
                    "mde_delta_r": s.mde_delta_r,
                    "beats": int(power._beats(s)),  # pylint: disable=protected-access
                })
    finally:
        engine.MATCH_ENTRY_BARS = default
        shutil.rmtree(cache, ignore_errors=True)
    return rows


def _done(path: Path) -> set[tuple[int, float, int]]:
    if not path.exists():
        return set()
    with path.open() as f:
        return {(int(r["persistence"]), float(r["drift"]), int(r["replicate"]))
                for r in csv.DictReader(f)}


def run(args) -> int:
    done = _done(args.out)
    for replicate in range(args.first, args.replicates + 1):
        for persistence, drift in SETTINGS:
            if (persistence, drift, replicate) in done:
                continue
            rows = run_market(persistence, drift, replicate, str(args.scratch))
            new = not args.out.exists()
            with args.out.open("a", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=list(rows[0]))
                if new:
                    writer.writeheader()
                writer.writerows(rows)
            print(f"H{persistence} k{drift} replicate {replicate} done", flush=True)
    return 0


def summary(args) -> int:
    import pandas as pd

    df = pd.read_csv(args.csv)
    df["window"] = df["lo"].astype(str) + "-" + df["hi"].astype(str)
    order = [f"{a}-{b}" for a, b in WINDOWS]

    null = df[df["drift"] == 0]
    bias = null.groupby(["window", "pattern"])["delta_r"].mean()
    print(f"Noise ({null['replicate'].nunique()} markets)")
    print(pd.DataFrame({
        "rows": null.groupby("window").size(),
        "beat_control": null.groupby("window")["beats"].sum(),
        "mean_abs_pattern_bias": bias.abs().groupby("window").mean(),
        "mean_mde": null.groupby("window")["mde_delta_r"].mean(),
    }).reindex(order).round(3).to_string())

    planted = df[(df["drift"] > 0) & (df["pattern"] == power.PATTERN)]
    table = planted.groupby(["persistence", "drift", "window"]).agg(
        markets=("replicate", "nunique"), gross_r=("gross_r", "mean"),
        delta_r=("delta_r", "mean"), detected=("beats", "mean"), mde=("mde_delta_r", "mean"),
    ).reset_index()
    table["window"] = pd.Categorical(table["window"], order, ordered=True)
    print(f"\nPlanted {power.PATTERN}")
    print(table.sort_values(["persistence", "drift", "window"]).round(3).to_string(index=False))

    others = df[(df["drift"] > 0) & (df["pattern"] != power.PATTERN)]
    print("\nOther patterns beating control in planted markets")
    print(others.groupby("window")["beats"].agg(["sum", "count"]).reindex(order).to_string())
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    go = sub.add_parser("run")
    go.add_argument("--out", type=Path, required=True)
    go.add_argument("--replicates", type=int, default=16)
    go.add_argument("--first", type=int, default=1, help="first replicate, to split work")
    go.add_argument("--scratch", type=Path, default=Path(tempfile.gettempdir()))
    show = sub.add_parser("summary")
    show.add_argument("csv", type=Path)
    args = parser.parse_args(argv)
    return run(args) if args.command == "run" else summary(args)


if __name__ == "__main__":
    sys.exit(main())
