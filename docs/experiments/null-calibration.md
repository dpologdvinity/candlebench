# Null calibration: does the method find edges in a random walk?

October 7, 2026. Reproduce with:

```bash
python scripts/null_calibration.py --seeds 10 --out docs/experiments/null-calibration.csv
```

## Question

On prices that cannot be predicted, every row that passes a test is a false
positive. How often does each of candlebench's tests pass?

## Setup

`candlebench.synthetic` generates a random walk: eight symbols, 61 weekday
sessions of 1m bars, a U-shaped intraday volatility and volume profile, small
open gaps, prices in cents. 5m and 15m bars are resampled from the 1m bars.
Each run uses the demo configuration (`candlebench demo`): 150 trials, 20%
chronological holdout, 4,000 bootstrap draws, estimated costs (about 1.8 bps per
leg), all 20 patterns plus both controls at 1m, 5m, 15m and pooled. Seeds 1-10
change the trial sample, not the bars, so the ten runs are correlated and are
not ten independent experiments.

## Results

800 pattern rows in total (20 patterns x 4 interval views x 10 seeds). A row
"beats its control" when the corrected p-value is at most 0.05 and the paired
interval lies above zero.

| Pattern compared with                                  | Rows beating control | Runs with any | Raw counts |
| ------------------------------------------------------ | -------------------: | ------------: | ---------- |
| Shared random control, net R                           |           38 (4.75%) |      10 of 10 | [csv](null-calibration-net-r.csv) |
| Shared random control, gross R                         |            3 (0.38%) |       3 of 10 | [csv](null-calibration-gross-shared.csv) |
| **Own matched controls, gross R (current)**            |        **1 (0.13%)** |   **1 of 10** | [csv](null-calibration.csv) |

No configuration produced positive net expectancy, a discovery `EDGE` or a
confirmed `EDGE` in any run: net expectancy and held-out validation are separate
gates, and they held throughout. One run in ten with a false positive is what a
5% familywise rate predicts (the chance of at least one in ten runs is 40%).

## What each stage fixed

**Net R rewarded wide stops.** Costs are a fixed number of bps per leg, and R
divides by the distance to the stop, so a pattern whose stop sits further away
pays fewer R for the same spread. Engulfing patterns have a median stop of
22.6 bps against 15.1 for the random control, and at 1m on seed 42 bullish
engulfing beat its control by +0.117R at a corrected p of 0.042; with costs set
to zero the same row read +0.044R at p = 1.0. Comparing gross R removed most of
this.

**The shared control's stop geometry still differed.** Its stop sits at its own
signal bar's extreme, usually tighter than a multi-bar formation's, and the
pessimistic rule that a bar touching both levels is a stop costs more R behind a
tight stop. So each pattern trade now gets its own control
(`engine.simulate_matched`): same direction, same stop distance from the last
close before entry, same exit rules through the same code, entering on a random
bar shortly after the pattern's entry.

**A control must not enter before its pattern.** The first matched controls
entered within five bars either side, and made results worse: 26 false
positives. Controls entering before the pattern trade through bars the pattern
was selected on — its formation, its trend gate, the exit of the trade before
it. Measured on seed 6 at 1m, holding each trade's stop distance fixed and
moving only the entry:

| Entry offset from the pattern's | −5 | −2 | −1 | 0 (pattern) | +1 | +2 | +5 |
| ------------------------------- | -: | -: | -: | ----------: | -: | -: | -: |
| `random_long` template, gross R | −0.104 | −0.107 | −0.109 | +0.014 | −0.024 | −0.016 | −0.001 |
| `tweezer_top` template, gross R | −0.555 | −0.146 | +0.291 | +0.010 | +0.026 | +0.016 | −0.007 |

Five bars before a tweezer top, a short trade rides the rise the trend gate
selected and loses 0.56R. After the entry the paths are statistically the same.
Controls now enter one to five bars after their pattern. Matching the stop in
units of recent bar range instead was also tried: a noisy width estimate puts
more controls at very tight stops, where the tie rule's penalty is steepest, and
it did not help (24 false positives).

## What it does not show

A pattern whose edge lasts several bars partly shares it with controls entering
one to five bars later, so the comparison is conservative for slow edges. This
calibration measures false positives only; how large a real edge must be to be
detected (power) has not been measured. The synthetic walk has open-gap
reversion and U-shaped volatility, but not the volatility clustering, news
jumps or auction effects of real sessions.
