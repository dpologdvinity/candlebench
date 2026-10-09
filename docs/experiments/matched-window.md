# Matched controls: how late should they enter?

October 8, 2026. Reproduce with:

```bash
python scripts/matched_window.py run --out docs/experiments/matched-window.csv --replicates 16
python scripts/matched_window.py summary docs/experiments/matched-window.csv
```

## Question

Each pattern trade is compared with a random entry of the same direction and
stop distance, made one to five bars after the pattern
(`engine.MATCH_ENTRY_BARS`). The [power study](detection-power.md) found the
cost of that choice: an edge lasting five bars is partly shared with controls
entering inside it, so the comparison captured only about half of it.

Moving the controls later should recover more of a slow edge. It should also
cost something: a control further from its pattern is drawn from a different
stretch of market, so the comparison gets noisier. This measures both.

## Setup

Same markets and design as the power study: 10 synthetic symbols, about 300
sessions, 1m bars, 200 trials, 20% holdout, 10,000 bootstrap draws, all 20
patterns and both controls in the corrected family. Four settings, 16
independent markets each:

- noise: nothing planted;
- a slow edge: `bullish_engulfing` followed by a 5-bar drift of 0.10 and of
  0.15 times bar volatility;
- a fast edge: a 1-bar drift of 0.30.

Each market is generated once and run under five entry windows, so every
window is measured on the same pattern trades. Only the controls differ.

## Results

On noise, every window is calibrated, and the current one is the least biased:

| Window (bars after entry) | Rows beating control | Mean per-pattern bias (R) |
| ------------------------- | -------------------: | ------------------------: |
| 1-5 (current)             |           1 of 320   |                     0.009 |
| 1-15                      |           1 of 320   |                     0.014 |
| 1-30                      |           1 of 320   |                     0.013 |
| 6-30                      |           0 of 320   |                     0.014 |
| 11-30                     |           0 of 320   |                     0.020 |

With an edge planted (`delta` is the measured advantage over the controls,
`detected` the share of markets where it passed the corrected test, `MDE` the
reported minimum detectable advantage):

| Planted edge | True gross R | Window | Delta R | Detected | MDE (R) |
| ------------ | -----------: | ------ | ------: | -------: | ------: |
| 1-bar, 0.30  |        0.109 | 1-5    |   0.099 |      88% |   0.101 |
|              |              | 1-15   |   0.088 |      44% |   0.123 |
|              |              | 1-30   |   0.091 |      25% |   0.140 |
|              |              | 6-30   |   0.086 |      19% |   0.141 |
|              |              | 11-30  |   0.085 |      12% |   0.150 |
| 5-bar, 0.10  |        0.172 | 1-5    |   0.079 |      50% |   0.102 |
|              |              | 1-15   |   0.112 |      62% |   0.126 |
|              |              | 1-30   |   0.126 |      69% |   0.139 |
|              |              | 6-30   |   0.130 |      69% |   0.145 |
|              |              | 11-30  |   0.132 |      69% |   0.149 |
| 5-bar, 0.15  |        0.278 | 1-5    |   0.125 |      88% |   0.105 |
|              |              | 1-15   |   0.188 |      94% |   0.132 |
|              |              | 1-30   |   0.192 |     100% |   0.144 |
|              |              | 6-30   |   0.209 |     100% |   0.149 |
|              |              | 11-30  |   0.211 |     100% |   0.156 |

Other patterns passing in the planted markets, where they share bars with the
planted signals: 5 of 912 rows at 1-5, 7 to 10 of 912 for the later windows.

## What it shows

**Later controls do recover a slow edge.** For the 5-bar edges, the measured
advantage rose from about 45% of the gross gain at 1-5 to about 75% at 11-30.

**They pay for it in noise.** The detection limit rose by a fifth to a half,
from 0.10R to 0.12-0.16R. That gain and that loss nearly cancel for slow edges:
detection rose from 50% to 69% for the smaller one and from 88% to 100% for the
larger.

**Fast edges are mostly lost.** A 1-bar edge was caught in 88% of markets at
1-5, and in 12% to 44% with any later window. The captured advantage barely
moved; the wider interval around it is what hid it.

**Bias stays small but grows with distance.** On noise, the mean per-pattern
bias doubled from 0.009R to 0.020R between the nearest and furthest windows,
and spillover to other patterns rose slightly.

## Decision

The default stays at one to five bars. It is the only window that catches fast
edges reliably, it has the lowest detection limit and the least bias, and it
gives up only part of a slow edge rather than inventing one. The real-data
conclusion holds under every window tested: an edge that later windows would
catch and 1-5 would miss has to last several bars and be about 0.15R gross or
more. On real 1m data, no pattern with at least 30 trades averages more than
0.10R gross, and none with over 1,000 trades more than 0.05R.

## Limits

One pattern was planted, at one frequency, in a market without volatility
clustering. Sixteen markets per setting give each detection rate a standard
error of up to about 0.125.
