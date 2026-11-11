---
name: costs
description: Rebuild the observed spread table and compare the three cost models side by side. Use when the cost assumption is in question, after changing costs.py or quotes.py, or when asked how much the cost model moves a result.
disable-model-invocation: true
---

The project's headline finding is that costs exceed the patterns' edge, so the
cost term is the number the whole conclusion rests on. This compares the three
ways of getting it.

Needs `ALPACA_API_KEY` and `ALPACA_SECRET_KEY` in the environment. Confirm they
are present with `env | grep -c ALPACA` — print the count, never the value.

## 1. Rebuild the observed table

```bash
python -m candlebench quotes --sessions 4
```

Nine requests per symbol-session, paced inside the free tier's 200/minute. For
50 symbols over 4 sessions this is ~1,800 requests and takes about 15 minutes —
run it in the background. It writes `.cache/bars/quoted_spreads.json` and prints
a median per time-of-day bucket.

Last measured: open 2.67 bps/leg (max 16.07), midday 1.52, close 1.02. The open
runs a median 4.0x midday, which is why the table is bucketed and why
`engine.simulate` charges each leg at the bar it filled on.

## 2. Compare the models

Run the same trials under each model and diff the expectancies:

```python
from dataclasses import replace
from candlebench import config as cm, leaderboard, runner
from candlebench.config import CostConfig

base = cm.load("config/backtest.toml")
base = cm.validate(replace(base, run=replace(base.run, trials=120, intervals=("1m",))))

out = {}
for label, costs in (("fixed", CostConfig(model="fixed", slippage_bps=1.0)),
                     ("estimated", CostConfig(model="estimated")),
                     ("quoted", CostConfig(model="quoted"))):
    cfg = cm.validate(replace(base, costs=costs))
    r = runner.run(cfg)
    out[label] = {(s.pattern, s.interval): s for s in r.stats}
    print(f"{label:<10} {leaderboard.describe_costs(r, cfg)}")

keys = [k for k in out["estimated"] if k[1] == "1m"
        and out["estimated"][k].expectancy_r is not None]
for a, b in (("estimated", "quoted"), ("fixed", "estimated")):
    shift = sum(out[b][k].expectancy_r - out[a][k].expectancy_r for k in keys) / len(keys)
    flips = sum(1 for k in keys if out[a][k].verdict != out[b][k].verdict)
    print(f"{a} -> {b}: mean expectancy {shift:+.4f}R, {flips} verdict changes")
```

## What to check in the result

- **Does any pattern earn `EDGE` under any model?** Two years and 49,062 trades
  say no. If one appears, that is a finding — investigate before reporting it.
- **Is the quoted figure above or below the estimated one?** Last measured, 1.83
  bps/leg observed against 1.28 estimated: the estimator reads **low**, so a
  conclusion drawn from it understates costs. Do not assume the direction — a
  spot check on AAPL alone once suggested the opposite, because AAPL is the most
  liquid name in the universe and does not generalise.
- **Does `describe_costs` call anything observed that was not?** With
  `model = "quoted"` and no table covering the symbols, it must report the figure
  as estimated and say why. Reporting an estimate as observed is the one failure
  this project exists to avoid.

Report the actual numbers from this run, not the ones above.
