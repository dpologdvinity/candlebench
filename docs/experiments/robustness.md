# Robustness: clustered volatility and a rare pattern

October 8, 2026. Two gaps in the [power study](detection-power.md) are closed
here: its synthetic market had no volatility clustering, and it planted an edge
in one frequent pattern only. Reproduce with:

```bash
# Calibration on clustered markets, demo design
python scripts/null_calibration.py --seeds 10 --clustered --cache-dir .cache/demo-clustered \
    --out docs/experiments/robustness-calibration-clustered.csv
# 80 noise-only markets each, power design, clustered and plain
python scripts/detection_power.py run --out docs/experiments/robustness-null-clustered.csv \
    --replicates 80 --persistence 1 --drifts 0 --clustered --workers 1
python scripts/detection_power.py run --out docs/experiments/robustness-null-plain.csv \
    --replicates 80 --persistence 1 --drifts 0 --workers 1
# Power on clustered markets (the 1-bar and 5-bar grids of the power study)
python scripts/detection_power.py run --out docs/experiments/robustness-power-clustered.csv \
    --replicates 8 --clustered --workers 1
# Power for a rare pattern
python scripts/detection_power.py run --out docs/experiments/robustness-power-morning-star.csv \
    --replicates 8 --persistence 1 --pattern morning_star --drifts 0 0.3 0.6 1.0 1.5 --workers 1
python scripts/detection_power.py summary <csv>
```

## Clustered volatility

Real volatility comes in runs. `synthetic.session(..., clustered=True)` adds
that: each symbol's daily volatility follows a persistent process (half-life
about 23 calendar days), 36% of whose variance is shared by every symbol, and
within a session a volatility shock halves in about 7 minutes. Average
volatility is unchanged. In one symbol's generated bars over 400 days, a day's
intraday volatility correlated 0.85 with the next day's (−0.04 in the plain
market), and 0.54 with one other symbol's on the same day; the design share of
common variance is 0.36.

This matters because the bootstrap resamples whole market dates as if they were
independent draws. Clustering makes neighbouring dates alike and puts every
symbol's turbulent day on the same date.

**Calibration holds.** With nothing planted:

| Market    | Design           | Markets | Markets with any false positive | Rows passing |
| --------- | ---------------- | ------: | ------------------------------: | -----------: |
| clustered | demo (3 intervals) |    10 |                               0 |   0 of 800   |
| clustered | power (1m)       |      80 |                      4 (5.0%) |  4 of 1,520  |
| plain     | power (1m)       |      80 |                      1 (1.2%) |  1 of 1,520  |

Rows count the 19 patterns per power-design market other than the one an edge
would be planted in; that one never passed either. The corrected test promises
at most a 5% chance of any false positive per run.
Clustered markets reached that rate and plain ones stayed under it. The reported
detection limit was the same in both (0.095R and 0.096R on average).

**Power holds.** The power study's grids, rerun on clustered markets, 8 markets
per setting:

| Planted edge (bullish_engulfing) | Advantage, clustered | Detected, clustered | Detected, plain |
| -------------------------------- | -------------------: | ------------------: | --------------: |
| 1-bar, 0.1                       |               0.044R |                 38% |             12% |
| 1-bar, 0.2                       |               0.077R |                 50% |             38% |
| 1-bar, 0.3                       |               0.108R |                 75% |             88% |
| 1-bar, 0.45                      |               0.151R |                100% |            100% |
| 5-bar, 0.1                       |               0.083R |                 50% |             62% |
| 5-bar, 0.15                      |               0.115R |                 75% |             88% |
| 5-bar, 0.22                      |               0.174R |                100% |            100% |

The 80% crossing stays at about the reported detection limit of 0.10R. With 8
markets per setting each rate has a standard error of up to 0.18, so the two
curves are indistinguishable.

## A rare pattern

The frequent patterns' detection limits were checked by planting an edge in
`bullish_engulfing`, about 1,500 trades per market. A pattern with few trades,
which reports a wide limit, was not. Here an edge is planted after
`morning_star`, which fires about 47 times per synthetic market, few enough to
report a limit of about 0.5R. (In the published 1m run it traded 1,880 times and
reports 0.09R.)

| Drift | Advantage (R) | Detected | Reported limit (R) |
| ----: | ------------: | -------: | -----------------: |
| 0     |        −0.023 |   0 of 8 |               0.50 |
| 0.3   |        +0.071 |   0 of 8 |               0.52 |
| 0.6   |        +0.134 |   0 of 8 |               0.50 |
| 1.0   |        +0.276 |   1 of 8 |               0.53 |
| 1.5   |        +0.423 |   4 of 8 |               0.51 |

An advantage of 0.42R, just under the reported 0.51R, was caught half the time,
and 0.28R once in eight. That is the shape the limit predicts: 80% at about the
limit and much less below it. So a wide limit on a thin row means what it
says: an advantage that size would probably have shown, and anything smaller
could be hiding.

## Limits

Overnight moves do not cluster in this generator; only intraday volatility
does. No jumps or news days. The rare-pattern check used one pattern and one
persistence. The first 8 rows of the clustered noise file are the same markets
as the clustered power study's zero-drift rows.
