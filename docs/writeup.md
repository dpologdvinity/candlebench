# A million candlestick trades, and none of them beat a coin flip

*How candlebench tested 66 classic patterns, why the first answer was wrong,
and how to put a signal of your own through the same test.*

Candlestick patterns are among the oldest ideas in trading: a hammer after a
decline means buyers are stepping in, an engulfing bar means momentum has
turned, three white soldiers mean a trend. Most of what is written about them
is anecdote, or a backtest that compares the pattern with doing nothing. I
wanted a number, measured in a way that could also have said "yes".

## The setup

[candlebench](https://github.com/dpologdvinity/candlebench) turns every
pattern into a mechanical trade. The trade enters at the next bar's open, puts
its stop just beyond the pattern's own extreme, and takes profit at twice that
risk. It is charged the NBBO spread actually quoted for that symbol at that
time of day, because the open runs about four times the midday spread.

The main run covers two years of 1-minute bars, October 2024 to October 2026,
for 117 liquid US stocks and ETFs: 5,000 sampled symbol-days on 501 market
dates, and 1,021,358 pattern trades across 66 patterns. A second run repeats
it at 5, 15 and 60 minutes (227,879 more trades).

## The answer

- **No pattern makes money after costs.** None of the 66 has positive
  expectancy, and 59 have 95% intervals entirely below zero.
- **No pattern beats random entry, even before costs.** Each pattern trade is
  paired with a random entry on the same session, in the same direction, at the
  same stop distance. Compared that way in gross R, no row survives
  multiple-comparison correction, at any of the four timeframes.
- **Costs are the whole story of the losses.** Random entry itself loses about
  0.34R a trade at 1-minute bars, almost all of it spread; before costs the two
  random controls sit at +0.015R and +0.009R.

A null result is cheap if the test could never have found anything, so every
row reports the smallest advantage over random entry it would have caught 80%
of the time. For the 19 patterns with more than 20,000 trades, that is 0.018R
to 0.034R. Any edge larger than that would very likely have been found.

## The first answer was wrong

An earlier version of this project reported that eight patterns beat random
entry on their signal and lost only to costs. That was a bias in my own method,
and the way it was found is the most useful part of the project.

The check is simple: run the whole method on a market where no pattern can
work, a synthetic random walk, and count how often it reports an edge anyway.
It reported far too many, and two flaws explained them.

1. **Net R rewards wide stops.** Costs are a fixed number of basis points, and
   R divides by the distance to the stop, so a pattern with a wider stop pays
   fewer R for the same spread. Compared with random entry in net R, wide-stop
   patterns "won" with no signal at all: 38 of 800 rows across ten seeds.
2. **A control with a different stop is not a fair control.** A bar that
   touches both the stop and the target is counted as a stop, since a bar does
   not say which came first, and that rule catches tight stops more often. So
   each pattern trade now gets its own control, at the same stop distance, entering
   one to five bars later. Entering *earlier* was a trap of its own: those bars
   were selected by the pattern, and a control placed five bars before a
   tweezer top lost 0.56R on pure noise.

With both fixed, 1 row in 800 passes on the random walk, which is what a 5%
familywise error rate predicts. Planting edges of known size in synthetic
markets then measured the other half: an advantage of 0.144R was caught every
time, and 0.097R half the time.

## Test your own signal

The patterns were only the first thing this method was pointed at. Its value is
the comparison: a signal is credited only for choosing *when* to enter, not for
the market's drift, its stop width, or costs left out. So the same test runs on
anything you write:

```bash
pip install candlebench
```

```python
# breakout_20.py
from candlebench.patterns import pattern
from candlebench.patterns.context import lag, rolling_max

@pattern("breakout_20", bias="bull", bars_required=21)
def breakout_20(geom, thresholds):
    # True on a bar that closes above the highest high of the 20 before it.
    return geom.close > lag(rolling_max(geom.high, 20))
```

```bash
candlebench test breakout_20.py --synthetic   # offline random walk, no data or key
```

It prints the full leaderboard, then one verdict per signal and timeframe in
plain words. On the synthetic market:

```
breakout_20 at 1m: NEGATIVE: averages -0.09R a trade after costs over 763 trades on 61
dates (corrected p 0.001), and +0.00R a trade against its matched random entries.
breakout_20 at 5m: NOISE: no confirmed edge over 117 trades on 54 dates: -0.01R a trade
against its matched random entries, -0.10R after costs. An advantage of 0.11R or more
over random entry would have been caught 80% of the time.
```

The first line is the comparison doing its job. The breakout loses money, but
no more than random entry does, so what it loses is the cost of trading, not a
signal pointing the wrong way. Run against real bars, a verdict of EDGE needs
positive expectancy after costs, a corrected advantage over the matched
controls, and confirmation on held-out dates it was not selected on.

## Engineering notes

The trade walk is a C++ extension (pybind11), 4 to 5 times faster than the
Python engine it replaced and held to it bit for bit: by a parity test on
randomised sessions, and by a recorded fixture of the trades the engine made
before the port, which every release wheel must reproduce on its own platform.
Keeping a run's trades as numpy columns instead of objects cut a 1,600-trial
run's peak memory from 1.38 to 0.92 GB, with results unchanged. About 1,400
tests run in CI, including headless-Chromium tests of the dashboard.

Everything is open source under the MIT licence:
[the code](https://github.com/dpologdvinity/candlebench), the
[interactive two-year results](https://dpologdvinity.github.io/candlebench/app/real/),
and the [method write-ups](https://github.com/dpologdvinity/candlebench/tree/master/docs/experiments).
