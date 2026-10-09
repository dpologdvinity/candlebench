# Walk-forward confirmation

October 9, 2026. Reproduce with:

```bash
python scripts/walk_forward.py power --out docs/experiments/walk-forward.csv --replicates 24
python scripts/walk_forward.py power --noise-only --first 25 --replicates 88 --out docs/experiments/walk-forward.csv
python scripts/walk_forward.py summary docs/experiments/walk-forward.csv
python scripts/walk_forward.py run --config <run config> --folds 3     # on a real cache
```

## Question

A standard run confirms a discovery on one chronological holdout, the newest
20% of dates. The [power study](detection-power.md) showed how weak that is:
with all 66 patterns, a planted edge was confirmed in at most 12% of markets,
even where discovery found it every time. A bigger holdout is not free, because
every date it takes is one discovery loses.

## Method

`candlebench.walkforward` cuts the history into an initial 40% and three later
segments of equal length. Fold k runs the standard pipeline on every date up to
the end of segment k, holding out segment k itself (`run.end_date` stops the
sampler there, so a fold never sees anything later). Whatever discovery selects
is traded through segment k, which it has not seen. Each date is out of sample
for exactly one fold.

The folds' out-of-sample trades are pooled into one strategy: trade what
discovery selects. That gives two tests, made once rather than once per
pattern: net expectancy above zero, and gross advantage over the matched random
entries above zero. Both use the date-clustered bootstrap, are Holm-corrected
together with the declared experiment count, and must pass for the strategy to
be confirmed. A fold that selects nothing trades nothing; a strategy that never
trades has no record, reported as unavailable rather than as zero.

## Setup

The power study's markets (10 synthetic symbols, about 300 sessions, 1m, all 66
patterns, 200 trials, 10,000 bootstrap draws). On each market three procedures
try to confirm an edge planted in `bullish_engulfing`:

- the standard single holdout, 200 trials;
- the single holdout at 600 trials, the same compute as three folds;
- walk-forward, three folds of 200 trials.

24 markets per planted setting, and 86 markets with nothing planted.

## Results

| Planted edge | Single holdout, 200 trials | Single holdout, 600 trials | Walk-forward |
| ------------ | -------------------------: | -------------------------: | -----------: |
| none         |                     0 of 86 |                    0 of 86 |      0 of 86 |
| 1-bar, 0.6   |                         0% |                         4% |          17% |
| 5-bar, 0.15  |                         0% |                        21% |          67% |
| 5-bar, 0.22  |                         8% |                       100% |         100% |

Where walk-forward confirmed, the pooled out-of-sample record was large: about
650 trades for the moderate 5-bar edge and 2,000 for the strong one, against
about 300 validation trades of the planted pattern in a 200-trial single
holdout.

## What it shows

**Walk-forward confirms far more often at the same cost.** For the moderate
5-bar edge it confirmed 67% of markets against 21% for a single holdout given
the same 600 trials, and 0% for the standard 200. Spending more trials on one
holdout helps; reusing dates as each fold's future helps more.

**It does not confirm noise.** With nothing planted, no fold of any market
selected anything, so the strategy never traded and nothing was confirmed.

**Fast edges are still hard.** The 1-bar edge (an advantage of 0.2R) was
selected in discovery by fewer than one fold in six, so the pooled record was
thin and walk-forward confirmed only 17% of markets. Confirmation can only test what
discovery selects.

**It tests a strategy, not a pattern.** When engulfing carried an edge,
patterns that contain an engulfing (three outside up, for one) were often
selected alongside it, and their trades joined the pool. That is the right
question for "would trading what this finds have made money", and the wrong one
for "which pattern has the edge"; the per-fold candidate lists answer the
second.

## On real data

Run on the published 117-symbol design at 1m and at 5m, 15m and 1h, with folds
testing 2025-07-31 to 2025-12-19, 2025-12-22 to 2026-05-15 and 2026-05-18 to
2026-10-08: no fold selected any pattern at any timeframe, so there is nothing
to trade out of sample. The walk-forward result agrees with the standard run.

## Limits

One planted pattern, one market design. Three folds after a 40% initial window
is one choice; more folds give more out-of-sample dates but earlier folds
discover on less history. Per-fold runs reuse the run's seed plus the fold
number, so folds draw different trials.
