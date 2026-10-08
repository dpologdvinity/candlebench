# Detection power: how large an edge would candlebench find?

October 7, 2026. Reproduce with:

```bash
python scripts/detection_power.py run --out docs/experiments/detection-power.csv --replicates 8 --workers 1
python scripts/detection_power.py summary docs/experiments/detection-power.csv
```

## Question

The [null calibration](null-calibration.md) showed the method finds almost
nothing in pure noise. That is only half an answer: a method that never finds
anything also passes it. If a pattern really had an edge, how large would the
edge have to be before candlebench reported it?

## Setup

`candlebench.synthetic.Planted` plants a known edge. After every signal of
`bullish_engulfing`, the returns of the next H bars gain a drift of k times
that bar's volatility. Planted drifts can create or remove later signals, so
the generator solves for the causal answer exactly. It is tested against a
bar-by-bar reference.

Each task builds an independent synthetic market (10 symbols, about 300
sessions) and runs the full pipeline with the real-data design: 1m bars, 200
trials, 20% chronological holdout, 10,000 bootstrap draws, all 20 patterns and
both controls in the corrected family, and estimated costs of about 1.8 bps per
leg. There are 8 markets per setting, plus 11 for the unplanted baseline. Two
persistences were tested: a 1-bar edge (H = 1) and a 5-bar edge (H = 5).

The x-axis is the planted pattern's measured advantage over its matched
controls in gross R, averaged over markets (standard errors 0.007 to 0.013R).

![Detection rate against true advantage](../images/detection-power.svg)

| Edge | Drift k | Advantage (R) | Gross R | Net R | Beats controls | Profitable in discovery | Confirmed EDGE |
| ---- | ------: | ------------: | ------: | ----: | -------------: | ----------------------: | -------------: |
| none |    0    |        +0.004 |  −0.015 | −0.177 |      0 of 11 |                  0 of 11 |        0 of 11 |
| 1-bar |   0.1  |        +0.041 |  +0.034 | −0.128 |          12% |                       0% |             0% |
| 1-bar |   0.2  |        +0.072 |  +0.079 | −0.083 |          38% |                       0% |             0% |
| 1-bar |   0.3  |        +0.103 |  +0.120 | −0.041 |          88% |                       0% |             0% |
| 1-bar |   0.45 |        +0.156 |  +0.179 | +0.019 |         100% |                       0% |             0% |
| 1-bar |   0.6  |        +0.206 |  +0.246 | +0.086 |         100% |                      25% |            12% |
| 5-bar |   0.03 |        +0.034 |  +0.048 | −0.114 |           0% |                       0% |             0% |
| 5-bar |   0.06 |        +0.043 |  +0.103 | −0.059 |          12% |                       0% |             0% |
| 5-bar |   0.1  |        +0.085 |  +0.182 | +0.020 |          62% |                       0% |             0% |
| 5-bar |   0.15 |        +0.123 |  +0.287 | +0.125 |          88% |                      88% |            12% |
| 5-bar |   0.22 |        +0.183 |  +0.426 | +0.265 |         100% |                     100% |            38% |

## What it shows

**The reported minimum detectable effect is honest.** Every row now reports the
smallest advantage its corrected test would catch 80% of the time. Here it
averaged 0.10R. In simulation, an advantage of 0.103R was caught 88% of the time
and 0.085R 62% of the time. The 80% crossing therefore sits at or just below the
reported figure, so the MDE is accurate to slightly conservative.

**False alarms stayed rare.** With nothing planted, no pattern beat its controls
in any of the 11 markets (0 of 220 rows). With an edge planted, other bullish
patterns that share bars with engulfing signals occasionally passed too (up to
2.6% of rows). That is the planted edge leaking into overlapping signals, not
noise.

**Beating random entry and making money are different bars.** At 1m, random
entry loses about 0.18R per trade to costs. A pattern can beat its controls
every time and still lose money. Discovery flags profitability reliably (88%)
only once the gross edge reaches about 0.29R.

**The holdout confirms only large edges.** With 40 validation trials, a
confirmed EDGE was found in at most 38% of markets even where discovery
succeeded every time. A missing confirmed EDGE is weak evidence by itself. The
per-row detection limits say how large an edge could still be hiding.

**Slow edges are understated by design.** Matched controls enter one to five
bars after their pattern. A 5-bar edge is partly shared with them, so the
comparison captured about half the gross gain (+0.085R of +0.182R). A 1-bar edge
was captured at about 85%. The test is conservative for edges that unfold
slowly. It cannot invent them. [Entering the controls later](matched-window.md)
recovers more of a slow edge but loses most fast ones, so the window stays.

## What it means for the real data

On two years of 1m data, the frequent patterns report minimum detectable
advantages of 0.07R to 0.14R (tweezers 0.07R, engulfing 0.09R, hammer 0.11R, harami 0.14R).
Rare ones such as the stars and crows report 0.30R or more. So for the frequent
patterns, a real advantage over random entry of about 0.1R or more would very
likely have been found, and none was. For the rare patterns the data cannot
rule out much.

## Limits

One pattern was planted, at one frequency. The synthetic market has U-shaped
volatility and open-gap reversion, but none of the volatility clustering, news
jumps or auction effects of real sessions. Eight markets per setting give each
rate a standard error of up to about 0.18, which is why the curve is read near
its crossing rather than at single points.
