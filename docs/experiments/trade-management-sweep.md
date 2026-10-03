# Trade-management sensitivity at 1m

Measured October 3, 2026, using the backtest at revision `1c96527`.

**None of the twenty candlestick patterns earns `EDGE` in any of nine
trade-management settings at 1m.** Changing the profit target from 2R to 1R or
3R, or the holding cap from 20 bars to 5 or 60, does not produce a demonstrated
positive net expectancy on this sample. This tests the first open assumption
in [the handoff](../../HANDOFF.md); it does not exhaust possible trade rules.

## Evidence

- [Per-pattern statistics](trade-management-sweep.csv): 198 rows, covering
  twenty patterns and two controls in each of nine runs. Only `1m` rows are
  included; the runner's identical `all` rows would count the same trades twice.
- [Experiment manifest](trade-management-sweep.json): full shared config,
  parameter grid, all 300 trials including child seeds and window indices,
  dependency versions, run summaries, and SHA-256 hashes of all fifty bar files
  and the quote table. The data itself remains in the ignored cache.
- Full individual reports are saved locally under
  `.cache/experiments/trade-management-2026-10-03/`. Replay instructions below
  produce equivalent reports through the existing runner.

Every setting uses the same 300 sampled symbol/session pairs, root seed 42,
six chronological windows, all fifty symbols, and the same detector thresholds.
There are 236 distinct sampled dates, spanning 2024-10-03 through 2026-09-30.
The underlying cache has 501 dates from 2024-10-03 through 2026-10-02, or 25,050
symbol/sessions. These are repeated evaluations of one sample, not nine
independent datasets.

Costs use `model = "quoted"`. All 150 symbol/bucket entries exist, and every
evaluated session uses quoted spreads. No estimator or fixed fallback is used.
The runner reports **1.7507 bps per leg** in every setting; this is its average
of session-wide bar costs, not an average weighted by actual trade fills.
The engine charges each entry and exit at its own bar's bucket cost. The table
still samples a typical spread rather than observing every historical fill.

The stop remains at the pattern extreme with `stop_buffer = 0.001` and
`min_risk_pct = 0.0005`. Overlapping trades remain disabled, commission is zero,
and every position closes by session end. Each interval's bar cap includes its
entry bar. Changing the exit rule therefore changes which later signals can be
traded; trade counts are expected to differ even though the signal masks do not.

## Results

Trade counts include the two controls. Verdict counts cover only the twenty
patterns. Every pattern clears the 30-trade minimum in every setting; there are
no `INSUFFICIENT` verdicts. All 300 sessions are evaluated in each run, with no
skips or runner warnings.

| Target (R) | Holding cap (bars) | Trades | NEGATIVE | NOISE | EDGE | Highest pattern CI lower bound (R) |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 5 | 67,818 | 19 | 1 | 0 | -0.1503 |
| 1 | 20 | 53,785 | 18 | 2 | 0 | -0.1359 |
| 1 | 60 | 49,997 | 17 | 3 | 0 | -0.1966 |
| 2 | 5 | 66,497 | 18 | 2 | 0 | -0.1471 |
| 2 | 20 | 49,062 | 18 | 2 | 0 | -0.0902 |
| 2 | 60 | 41,208 | 16 | 4 | 0 | -0.1967 |
| 3 | 5 | 66,297 | 18 | 2 | 0 | -0.1457 |
| 3 | 20 | 47,797 | 17 | 3 | 0 | -0.1260 |
| 3 | 60 | 37,651 | 15 | 5 | 0 | -0.2063 |

No pattern's 95% net-expectancy interval has a positive lower bound anywhere
in the grid. Both random-entry controls are `NEGATIVE` throughout, with mean
net expectancy ranging from **-0.2711R to -0.2323R**. The default 2R/20-bar
setting reproduces the handoff's 49,062 trades, now charged the quoted costs.

The most favourable lower bound belongs to `three_white_soldiers` at the
default setting: net expectancy **+0.0781R**, with a 95% interval of
**[-0.0902R, +0.2633R]**,
on **70 trades**. Its slightly positive averages at 1R/20 bars and 3R/20 bars
also have intervals spanning zero. No chronological window supplies the thirty
trades needed to measure its stability, so stability is unavailable, not zero.

Longer holds produce some positive individual windows despite negative pooled
averages. `dark_cloud_cover` at 2R/60 bars is positive in two of six qualifying
windows, the highest measured stability in this grid (**33.3%**). The other
positive-window cases reach one of six. These results do not support saying
every pattern loses in every period; they also do not establish a stable edge.

## What this establishes, and what remains open

The no-`EDGE` finding at **1m** survives these nine target/holding-cap choices
under the sampled quoted costs. This is a sensitivity check, not selection of
an optimal strategy or proof that every possible setting loses.

- Other timeframes were not swept. This Alpaca cache contains only 1m bars;
  coarser bars can be constructed from them with consistent session boundaries.
  Results at a different source cannot substitute for this sample.
- The stop buffer and risk floor were held fixed in this experiment. The
  [subsequent buffer sweep](stop-buffer-sweep.md) measured substantial buffer
  dependence at 1m; sub-minute sensitivity remains untested.
- The bootstrap is the existing 2,000-draw, per-trade percentile procedure.
  It does not adjust for clustering by session/symbol or for the 180 pattern
  comparisons. The six chronological windows are descriptive slices, not a
  withheld validation set. No strategy was promoted from this sweep.
- The universe is today's liquid names, and quoted costs are sampled per symbol
  and time bucket. Survivorship, day-specific spread changes, short borrow
  costs, and real execution remain outside this measurement.

## Replay from the repository root

Use the recorded code revision and dependency versions from the manifest. This
requires the original cached files and quote table; a fresh clone does not
contain them. The hash checks stop if the inputs have changed. Reusing the
cache requires no network access or credentials.

Run this with the finance virtual environment's Python, or a Python environment
with the project's dependencies installed:

```python
import hashlib
import json
from dataclasses import replace
from pathlib import Path
from candlebench import config, leaderboard, runner

manifest = json.loads(Path("docs/experiments/trade-management-sweep.json").read_text())
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
out = Path(".cache/experiments/replayed-sweep")
out.mkdir(parents=True, exist_ok=True)
expected_trials = manifest["trials"]
for reward in manifest["grid"]["reward_multiple"]:
    for hold in manifest["grid"]["max_hold_bars"]:
        cfg = config.validate(replace(base, trade=replace(
            base.trade, reward_multiple=reward, max_hold_bars=hold)))
        result = runner.run(cfg)
        assert result.skipped_sessions == 0 and result.sessions_evaluated == 300
        assert result.quoted_share == 1.0
        assert [(t.index, t.symbol, str(t.session), t.seed, t.window)
                for t in result.trials] == [
                    (t["index"], t["symbol"], t["session"], t["seed"], t["window"])
                    for t in expected_trials]
        stem = out / f"reward-{reward:g}-hold-{hold}"
        leaderboard.write_json(result, cfg, stem.with_suffix(".json"))
        leaderboard.write_csv(result, stem.with_suffix(".csv"))
```

The experiment additionally asserted identical signal counts across all nine
runs and rechecked every input hash after completion. The existing suite passed
all **476 tests** using `~/.venvs/finance/bin/python -m pytest -q`; its HTTP tests
require permission to open local loopback sockets in a restricted sandbox.
