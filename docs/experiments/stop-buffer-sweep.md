# Stop-buffer sensitivity at 1m

Measured October 3, 2026, with the existing simulator at revision `1c96527`.

**The default 10 bps stop buffer supplies most of the risk in 76.0% of
accepted candlestick trades at 1m.** Its median contribution is **64.9%**.
The concern that a fixed buffer can dominate pattern geometry is therefore
already observable at 1m; this measurement does not extend to sub-minute bars.

Buffers of **0, 2, 5, 10 and 20 bps** all produce **zero `EDGE` verdicts**.
Changing the buffer changes trade eligibility, stops, targets, position size
and the denominator of R. It is a substantive part of the tested strategy.

## Evidence and fixed inputs

- [Per-pattern statistics and diagnostics](stop-buffer-sweep.csv): 110 rows,
  twenty candlestick patterns and two controls for each setting. The duplicate
  pooled `all` rows are excluded.
- [Manifest](stop-buffer-sweep.json): full configuration, all 300 trials with
  child seeds and window indices, dependency versions, input hashes, counting
  definitions and summaries for both candlestick trades and all trades.
- Full run reports and per-pattern diagnostics are retained locally in
  `.cache/experiments/stop-buffer-2026-10-03/`.

The experiment reuses the [trade-management sweep](trade-management-sweep.md)
and its exact inputs: 300 trials, all fifty symbols, 236 distinct sampled dates
from 2024-10-03 through 2026-09-30, seed 42 and six chronological windows.
The target stays at 2R, the holding cap at twenty bars and the minimum risk
at 5 bps. Overlap remains disabled and commission is zero.

All five settings evaluate all 300 sampled sessions, with identical signal
counts, no skips or warnings, and full sampled-quote coverage. The fifty bar
files and the quote table match their hashes before and after the probe.
The 10 bps baseline's entire report matches the earlier 2R/20-bar report.

## Selection and risk attribution

The table below covers only the twenty candlestick patterns. They supply the
same **59,122 signals** in each setting, including **207 signals on a session's
final bar** that cannot enter. All remaining **58,915 candidates** have valid
entry prices and pattern extremes.

Risk eligibility is evaluated on every candidate **before overlap suppression**.
An ineligible candidate has nonpositive risk or positive risk below the 5 bps
floor. The simulator checks overlap earlier, so these diagnostics describe
the candidate population rather than the order of its runtime rejection paths.
Accepted-trade attribution is measured only over trades the simulator records.

| Buffer (bps) | Accepted trades | Risk-ineligible candidates | Median risk (bps) | Median buffer contribution | Trades with buffer supplying most risk | EDGE patterns |
| --- | --- | --- | --- | --- | --- | --- |
| 0 | 25,898 | 48.356% | 8.928 | 0.0% | 0.0% | 0 |
| 2 | 32,883 | 28.468% | 9.163 | 21.8% | 0.0% | 0 |
| 5 | 39,209 | 3.926% | 10.586 | 47.2% | 45.0% | 0 |
| 10 | 36,130 | 0.048% | 15.414 | 64.9% | 76.0% | 0 |
| 20 | 32,082 | 0.000% | 25.346 | 78.9% | 93.9% | 0 |

At zero buffer, **2,468** candidates have nonpositive risk and **26,021**
have positive risk below the floor. At the default buffer, those counts fall
to **3** and **25**. Of its **36,130** accepted candlestick trades, **16,859
(46.7%)** would be risk-ineligible at that same signal without a buffer.
That is a per-entry counterfactual; removing the buffer also changes the exit
sequence and which later signals can be entered.

At the default buffer, **17 of twenty patterns** have the buffer supplying most
of the risk in a majority of their accepted trades. Fewer trades at 10 and
20 bps than at 5 bps reflect longer overlap suppression, even as more candidates
become risk-eligible. At 20 bps, all 58,915 candlestick candidates are
risk-eligible, but 26,833 are suppressed by existing positions.

## Returns in both units

These are trade-weighted averages across candlestick trades, excluding controls.
Each pattern still receives its own confidence interval and verdict in the CSV.

| Buffer (bps) | Mean gross R | Mean net R | Mean net price return (bps) |
| --- | --- | --- | --- |
| 0 | -0.0113 | -0.4065 | -3.7086 |
| 2 | +0.0096 | -0.3639 | -3.3675 |
| 5 | +0.0116 | -0.3076 | -3.2845 |
| 10 | -0.0012 | -0.2184 | -3.4148 |
| 20 | +0.0002 | -0.1282 | -3.3540 |

Increasing the buffer brings the R average much closer to zero while mean
price losses remain around 3.3–3.7 bps. This is not an isolated test of the
denominator: entries and exits also change. R remains meaningful for the
simulator's fixed-dollar risk sizing, since wider stops imply smaller positions.
The price-return column helps distinguish a change in price performance from
a change in risk sizing; neither column is a portfolio return.

All patterns clear the thirty-trade minimum in all five settings. At buffers
0 through 10 bps, eighteen patterns are `NEGATIVE` and two are `NOISE`; at
20 bps, seventeen are `NEGATIVE` and three are `NOISE`. Both controls remain
`NEGATIVE` throughout. Nothing here selects a profitable buffer setting.

## Calculation and checks

For direction `d` (+1 long, -1 short), next-bar open `e`, the pattern extreme
`x`, and fractional buffer `b`, the unchanged simulator uses:

```text
stop          = x * (1 - d * b)
risk          = d * (e - stop)
geometry_risk = d * (e - x)
buffer_risk   = x * b
risk          = geometry_risk + buffer_risk
buffer_share  = buffer_risk / risk
```

The extreme is the minimum low for a long or maximum high for a short across
the pattern's required bars. The probe reuses the engine's rolling extrema,
calls the original simulator and observes its results. Every accepted trade's
entry must belong to the risk-eligible candidate set, and its stored risk
must match this calculation. Counts reconcile from signals through final-bar
exclusions, invalid entries, risk exclusions, eligible candidates and trades.

A gap can put `geometry_risk` below zero while the buffered stop still supplies
valid positive risk. At the default setting this occurs in **946 trades
(2.62%)**; another **729 (2.02%)** have exactly zero geometry risk. A negative
geometry contribution makes the buffer supply more than 100% of total risk.
Those cases are counted from `geometry_risk < 0`, and buffer dominance from
`buffer_risk > geometry_risk`, so floating-point noise in a ratio at exactly
100% or 50% does not inflate their counts. Shares are not clipped.

Hand-calculated long and short examples verified rejection at zero risk,
positive risk below the floor, zero geometry, negative geometry rescued by a
buffer, final-bar exclusion, overlap accounting and unavailable statistics on
empty selections. The corrected diagnostic was rerun across all five settings.
The existing suite passed **476 tests** under the finance virtual environment.

## Replaying returns

From the repository root, with the recorded dependencies and unchanged input
files, this reproduces the backtest reports. The diagnostics can be independently
recomputed from the signal masks and session geometry with the formulas and
counting rules above. They were produced by temporary research instrumentation,
not an additional trading rule.

```python
import hashlib
import json
from dataclasses import replace
from pathlib import Path
from candlebench import config, leaderboard, runner

manifest = json.loads(Path("docs/experiments/stop-buffer-sweep.json").read_text())
parent = Path(manifest["parent_manifest"])
assert hashlib.sha256(parent.read_bytes()).hexdigest() == manifest["parent_manifest_sha256"]
for path, expected in manifest["fingerprints"].items():
    assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == expected, path
saved = manifest["base_config"]
classes = {
    "run": config.RunConfig, "universe": config.UniverseConfig,
    "trade": config.TradeConfig, "costs": config.CostConfig,
    "thresholds": config.Thresholds, "stats": config.StatsConfig,
}
sections = {
    name: cls(**{k: tuple(v) if isinstance(v, list) else v
                 for k, v in saved[name].items()})
    for name, cls in classes.items()
}
base = config.validate(config.Config(patterns=tuple(saved["patterns"]), **sections))
assert json.loads(json.dumps(leaderboard.payload_config(base))) == saved
out = Path(".cache/experiments/replayed-buffer-sweep")
out.mkdir(parents=True, exist_ok=True)
for bps in manifest["grid"]["stop_buffer_bps"]:
    cfg = config.validate(replace(base, trade=replace(
        base.trade, stop_buffer=bps / 10_000)))
    result = runner.run(cfg)
    assert result.skipped_sessions == 0 and result.sessions_evaluated == 300
    assert result.quoted_share == 1.0
    assert [(t.index, t.symbol, str(t.session), t.seed, t.window)
            for t in result.trials] == [
                (t["index"], t["symbol"], t["session"], t["seed"], t["window"])
                for t in manifest["trials"]]
    leaderboard.write_json(result, cfg, out / f"buffer-{bps}.json")
    leaderboard.write_csv(result, out / f"buffer-{bps}.csv")
```

## Remaining scope

Sub-minute bars have not been measured by this sweep. The risk floor stays
fixed, and target, holding cap and buffer have not been varied jointly. These
five evaluations reuse one sample; the existing per-trade bootstrap does not
correct for symbol/session clustering or multiple comparisons. The six windows
are descriptive slices, not withheld validation data. Quote costs remain
sampled per symbol and time bucket, as in the preceding experiment.
