"""Record what the trade engine produces, to pin it against later changes.

The native walk is checked against the Python one in tests/test_engine_core.py,
but both are today's code: a change that moved both the same way would pass.
This records, for a fixed set of sessions and settings, the trades and matched
controls an engine produces, so `tests/test_engine_golden.py` can hold every
later engine to the one recorded here.

The sessions are drawn with the standard library's `random` in whole cents, so
the inputs are exact and do not depend on the numpy version. Each case stores
the trade count and a SHA-256 of the trades' reprs; a repr round-trips every
float, so the hash changes if any value moves by one ulp.

    python scripts/engine_golden.py --source DIR --revision SHA --out tests/data/engine_golden.json
    python scripts/engine_golden.py --check tests/data/engine_golden.json

`--source` is a checkout to import candlebench from, so the record can be
made from an earlier revision, as the committed one was.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import sys
from dataclasses import replace
from datetime import date
from pathlib import Path

import numpy as np

SESSION = date(2026, 9, 15)
PATTERNS = ("hammer", "hanging_man", "bullish_engulfing", "bearish_engulfing")
COSTS = ("none", "flat", "per_bar")


def session_rows(seed: int, n: int) -> list[tuple[float, float, float, float]]:
    """A random walk in cents with gaps, flat bars and missing opens and closes."""
    draw = random.Random(seed)
    rows, close = [], 10_000
    for _ in range(n):
        open_ = close + draw.randint(-3, 3)
        if draw.random() < 0.05:
            open_ += draw.randint(-80, 80)
        close = open_ + draw.randint(-25, 25)
        high = max(open_, close) + draw.randint(0, 12)
        low = min(open_, close) - draw.randint(0, 12)
        if draw.random() < 0.03:
            open_ = high = low = close
        row = [open_ / 100, high / 100, low / 100, close / 100]
        if draw.random() < 0.01:
            row[0] = math.nan
        if draw.random() < 0.01:
            row[3] = math.nan
        rows.append(tuple(row))
    return rows


def cases():
    """Every (name, settings) pair the record covers."""
    for seed in range(6):
        for pattern in PATTERNS:
            for overlap in (False, True):
                for cost in COSTS:
                    yield f"{seed}-{pattern}-{int(overlap)}-{cost}", {
                        "seed": seed, "pattern": pattern, "overlap": overlap, "cost": cost}


def run_case(settings: dict) -> list[str]:
    """The reprs of one case's trades and then its matched controls."""
    from candlebench import engine, patterns  # pylint: disable=import-outside-toplevel
    from candlebench.config import CostConfig, Thresholds, TradeConfig  # pylint: disable=import-outside-toplevel
    from candlebench.patterns import context  # pylint: disable=import-outside-toplevel

    seed = settings["seed"]
    draw = random.Random(1000 + seed)
    n = 60 + draw.randint(0, 340)
    data = np.asarray(session_rows(seed, n), dtype=np.float64)
    geom = context.geometry({"open": data[:, 0], "high": data[:, 1], "low": data[:, 2],
                             "close": data[:, 3], "volume": np.full(n, 1000.0)}, 3, 0.0)
    spec = patterns.get(settings["pattern"])
    mask = patterns.detect(spec, geom, Thresholds()) | np.array(
        [draw.random() < 0.05 for _ in range(n)])
    trade_cfg = replace(TradeConfig(), allow_overlapping_trades=settings["overlap"],
                        max_hold_bars=draw.randint(1, 30),
                        reward_multiple=draw.choice([0.5, 1.0, 2.0, 3.3]))
    cost_cfg = CostConfig(slippage_bps=1.7, commission_per_trade=0.35)
    one_way_cost = {
        "none": None,
        "flat": 0.00031,
        "per_bar": np.array([draw.randint(0, 1000) / 1e7 for _ in range(draw.randint(1, n))]),
    }[settings["cost"]]
    bar_minutes = None if seed % 3 == 0 else np.arange(n, dtype=np.int64) * 5

    made = engine.simulate(geom, mask, spec, trade_cfg, cost_cfg, symbol="TEST", interval="5m",
                           session=SESSION, trial_index=seed, window=seed % 2,
                           bar_minutes=bar_minutes, one_way_cost=one_way_cost)
    controls = engine.simulate_matched(geom, made, trade_cfg, cost_cfg,
                                       np.random.default_rng(seed),
                                       bar_minutes=bar_minutes, one_way_cost=one_way_cost)
    return [repr(t) for t in [*made, *controls]]


def digest(reprs: list[str]) -> dict:
    return {"trades": len(reprs),
            "sha256": hashlib.sha256("\n".join(reprs).encode()).hexdigest()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", maxsplit=1)[0])
    parser.add_argument("--source", type=Path, default=None,
                        help="checkout whose candlebench package is recorded "
                             "(default: the installed package)")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--out", type=Path, help="write a new record here")
    mode.add_argument("--check", type=Path,
                      help="compare with this record and exit 1 on any difference")
    parser.add_argument("--revision", help="commit the checkout is at (with --out)")
    args = parser.parse_args()
    if args.out and not args.revision:
        parser.error("--out needs --revision")
    if args.source is not None:
        sys.path.insert(0, str(args.source.resolve()))
    cases_ = {name: digest(run_case(settings)) for name, settings in cases()}

    if args.check:
        # Used by the release build on every platform's wheel, so a wheel whose
        # engine rounds differently from the recorded one is never published.
        from candlebench import engine  # pylint: disable=import-outside-toplevel
        expected = json.loads(args.check.read_text())
        differ = sorted(n for n in expected["cases"] if cases_.get(n) != expected["cases"][n])
        native = "native" if engine._enginecore is not None else "python"  # pylint: disable=protected-access
        if differ or set(cases_) != set(expected["cases"]):
            print(f"{native} engine differs from revision {expected['revision']} "
                  f"in {len(differ)} cases: {', '.join(differ[:5])}")
            return 1
        print(f"{native} engine reproduces revision {expected['revision']} "
              f"on all {len(cases_)} cases")
        return 0

    record = {"revision": args.revision, "cases": cases_}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    total = sum(case["trades"] for case in record["cases"].values())
    print(f"recorded {len(record['cases'])} cases, {total} trades and controls, to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
