# Null calibration: does the method find edges in a random walk?

October 7, 2026. Reproduce with:

```bash
python scripts/null_calibration.py --seeds 10 --out docs/experiments/null-calibration.csv
```

## Question

On prices that cannot be predicted, every row that passes a test is a false
positive. How often does each of candlebench's tests pass?

## Setup

`candlebench.synthetic` generates a driftless random walk: eight symbols, 61
weekday sessions of 1m bars, a U-shaped intraday volatility and volume profile,
small open gaps, prices in cents. 5m and 15m bars are resampled from the 1m bars.
Each run uses the demo configuration (`candlebench demo`): 150 trials, 20%
chronological holdout, 4,000 bootstrap draws, estimated costs (about 1.8 bps per
leg), all 20 patterns plus both controls at 1m, 5m, 15m and pooled. Seeds 1-10
change the trial sample, not the bars, so the ten runs are correlated and are
not ten independent experiments.

## Results

800 pattern rows in total (20 patterns x 4 interval views x 10 seeds).

| Test, corrected at 0.05                     | Control compared in net R | Control compared in gross R |
| ------------------------------------------- | ------------------------: | --------------------------: |
| Rows beating their control                  |              38 (4.75%)   |                  3 (0.38%)  |
| Runs with at least one such row             |                  10 of 10 |                     3 of 10 |
| Rows with positive net expectancy           |                         0 |                           0 |
| Discovery `EDGE`                            |                         0 |                           0 |
| Confirmed `EDGE`                            |                         0 |                           0 |

Net-R rows by view: 1m 16, 5m 0, 15m 1, pooled 21. Gross-R rows: 1m 0, 5m 0,
15m 1, pooled 2. Raw counts per seed and interval are in
[null-calibration-net-r.csv](null-calibration-net-r.csv) (before) and
[null-calibration.csv](null-calibration.csv) (after).

## What it showed

Comparing a pattern with its random-entry control in **net R** was biased.
Costs are a fixed number of bps per leg, and R divides by the distance to the
stop, so a pattern with a wider stop pays fewer R for the same spread. Engulfing
patterns have a median stop of 22.6 bps against 15.1 for the random control, so
they saved roughly a third of the control's cost in R without any signal. At
1m on seed 42, bullish engulfing beat its control by +0.117R at a corrected
p of 0.042; with costs set to zero the same row read +0.044R at p = 1.0.

The fix, in `metrics.attach_baselines`: the control comparison is made on
**gross R**, which asks whether the pattern carries signal beyond random entry,
while expectancy is still tested **net**, which asks whether it is profitable.
`EDGE` requires both, then confirmation on held-out dates.

## What it did not fix

Three of ten runs still produce a row that beats its control, above the nominal
5% familywise rate Holm correction targets. The likely remaining cause is the
same confound in another form: the pessimistic rule that a bar touching both
stop and target is a stop costs more R when the stop is tight relative to the
bar's range, so gross R is still not independent of stop width. A control whose
stop distance is matched to each pattern's would remove both effects; that is
the next methodological change worth making.

The final verdicts held throughout: no run produced a discovery or confirmed
`EDGE`, because net expectancy and held-out validation are separate gates.
