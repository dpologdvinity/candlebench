# candlebench guide

The full reference: commands, the trade and cost models, how to read the
leaderboard, configuration, data sources, and the historical measurements.
The [README](../README.md) has the current headline result and a quick start.

Takes the 20 most widely cited candlestick patterns, samples random liquid
stocks and random trading sessions, converts every pattern signal into a
mechanical risk-managed trade, and ranks the patterns by measured profitability.

The question it is built to answer precisely:

> Given a pattern signal on an intraday chart, and a trade taken on the next
> bar with a stop and a target, does the resulting distribution of returns
> differ from the distribution produced by **entering at random**?

A pattern that cannot beat a random entry on the same data has no demonstrated
edge, however impressive its win rate looks in isolation. That comparison is
built into the tool rather than left to the reader.

## Commands

```bash
python -m candlebench fetch       # download and cache intraday bars
python -m candlebench serve       # browser UI: browse, configure, run
python -m candlebench run         # measure and rank in the terminal
python -m candlebench patterns    # list every registered pattern
```

### fetch

```bash
python -m candlebench fetch [--config PATH] [--intervals 1m,5m]
```

Downloads history for each symbol and interval and writes it to
`.cache/bars/{interval}/{symbol}.parquet`, or
`.cache/bars/alpaca/{interval}/{symbol}.parquet` when the source is Alpaca. Runs are then read
entirely from this cache and never touch the network, so a run's duration does
not depend on Yahoo's mood, and two patterns in the same trial cannot see
different data.

A symbol Yahoo refuses is recorded and reported; it does not abort the others.

### run

```bash
python -m candlebench run [--config PATH] [--trials N] [--seed S]
                          [--intervals 1m,5m] [--patterns hammer,doji]
                          [-v] [--json OUT] [--csv OUT]
```

`-v` adds gross-versus-net expectancy, exit mix, average holding period,
walk-forward stability and max drawdown. `--json` writes the full result
including the config and seed, so any number can be reproduced, plus a sibling
`.parquet` holding every individual trade — a full run produces about 43,000 of
them, roughly a hundred times the report's size, which is why they travel
separately. `--csv` writes a flat table.

Command-line flags override the config file, which overrides built-in defaults.

### serve

```bash
python -m candlebench serve [--port 8765] [--no-browser] [--config PATH]
```

Opens a page with sortable columns, a tab per timeframe, confidence-interval
whiskers and an edge-over-control chart. You can warm the cache, change the
trial count, walk-forward windows, timeframes, cost model, pattern set and trade
parameters, and start a run with a live progress bar.

Clicking a leaderboard row drills into it:

- its **equity curve** — cumulative R in the order the trades happened, with the
  matched random-entry control overlaid and the deepest drawdown shaded. The
  shading is located from the same series the server measured `maxdd R` over, so
  the chart and the leaderboard cannot disagree.
- a **breakdown** by symbol, time of day, walk-forward window or exit reason.
  One ticker carrying the whole result and the opening auction are the two usual
  explanations for an apparent intraday edge; both are one selection away.
- a **session chart** — candlesticks with the pattern's signal bars outlined and
  each recorded trade's entry, stop and target drawn across the bars it was open
  for. The levels come from the stored trades rather than from re-deriving them,
  and the mask is computed with the thresholds that run used, not the server's
  startup config — otherwise a run with an edited threshold would outline bars
  the leaderboard never counted.
- a **paged trade table**, sortable by any column, server-side so page two of a
  sorted table continues page one.

The last run is persisted, so reopening shows results rather than an empty table,
and the last ten runs are kept so two can be compared: pick a baseline and an
"against" run to see expectancy move per pattern, which verdicts changed, and
which settings actually differ between them.

No frontend dependencies: `http.server`, hand-rolled SVG, vanilla JavaScript.

#### HTTP API

The page uses nothing the command line cannot. Every endpoint is loopback-only.

| Route                                                                        | Returns                                                           |
| ---------------------------------------------------------------------------- | ----------------------------------------------------------------- |
| `GET /api/meta`                                                              | patterns, intervals, breakdown keys, cost models, the base config |
| `GET /api/results`                                                           | the last run's report                                             |
| `GET /api/status`                                                            | progress of a run or fetch in flight                              |
| `GET /api/cache`                                                             | what the bar cache holds per interval                             |
| `GET /api/trades?pattern=&interval=&symbol=&sort=&desc=&limit=&offset=&run=` | a counted page of trades                                          |
| `GET /api/breakdown?by=&pattern=&interval=&run=`                             | one grouping of those trades                                      |
| `GET /api/equity?pattern=&interval=&run=`                                    | cumulative R and its matched control                              |
| `GET /api/session?symbol=&session=&interval=&pattern=&run=`                  | one session's bars, signal mask and trade levels                  |
| `GET /api/runs` / `GET /api/runs?id=`                                        | the saved run list, or one saved report                           |
| `POST /api/run` / `POST /api/fetch`                                          | start work                                                        |

Every filter is validated before it reaches a frame or a path — patterns against
the registry, intervals against the supported list, symbols against
`SYMBOL_PATTERN`, run ids against a strict timestamp shape — so a traversal
attempt is a 400 rather than a 500. `limit` is capped at 500 server-side.

**It binds to `127.0.0.1` only, and the host is not configurable.** The server
starts real runs and real network fetches from unauthenticated requests, which
is acceptable only because nothing off this machine can reach it. For the same
reason `cache_dir` cannot be set from the browser, and symbols are validated
against a character whitelist — a symbol becomes a filename in the bar cache,
so an unchecked one could be written outside it.

---

## How a signal becomes a trade

```
pattern_low = min(low over the pattern's bars)
entry       = next_bar.open              # never the signal bar's close
risk        = entry - pattern_low * (1 - stop_buffer)
stop        = entry - risk
target      = entry + risk * reward_multiple

walk forward:
  bar gaps through a level  -> fill at the open, not the level
  bar touches both levels   -> STOP wins, unless it opened past the target
  max_hold_bars reached     -> exit at that bar's close  (timeout)
  session's last bar        -> exit at its close         (session_end)
```

Short patterns mirror this exactly, using the pattern's highest high.

Two choices are deliberately pessimistic. A bar records only open, high, low
and close, so when it touches both the stop and the target there is no way to
know which came first — assuming the favourable one is the most common way a
backtest flatters itself. The one exception is a bar that opens beyond the
target: the open is the only price whose place in the bar is known, so the
target order filled there first. And gaps fill at the open because that is what
happens to a real order.

Signals are discarded when the stop sits within `min_risk_pct` of entry, since
a near-zero risk denominator produces an R multiple that swamps every statistic
downstream. A signal on a session's final bar produces no trade, because there
is no next bar to enter on.

Costs: half the spread is charged against you on each leg. Under the default
`model = "estimated"` that spread is **measured, not assumed** — see
[The cost model](#the-cost-model). Positions are sized at
`risk_per_trade_usd / risk_per_share`, which makes a dollar commission cost
exactly `commission_per_trade / risk_per_trade_usd` in R regardless of the
stock's price. Gross and net are both reported, because at fine intervals the
costs are the whole story.

### The cost model

The central finding below is that costs exceed whatever edge these patterns
carry, so the cost term is the number that finding rests on. It used to be a flat
`slippage_bps = 1.0` — a guess applied to every symbol, session and timeframe
alike, which made the least evidenced number in the system the load-bearing one.

There are now three models, and the difference between them is the difference
between a guess, an inference and an observation.

| `[costs] model`       | Where the spread comes from                                                                          |
| --------------------- | ---------------------------------------------------------------------------------------------------- |
| `quoted`              | **Observed** from historical NBBO quotes. Needs a table from `candlebench quotes` and an Alpaca key. |
| `estimated` (default) | **Inferred** from high-low ranges by Corwin-Schultz. Needs no credentials.                           |
| `fixed`               | A flat `slippage_bps`. The original guess, kept for comparison.                                      |

`quoted` is the best evidenced and is what any claim about costs should rest on.
It is not the default only because it needs a key and a sampled table; without
them it falls back to the estimator and _says so_ in the cost line rather than
calling an estimate observed.

### What the estimator gets wrong

Corwin-Schultz is inferred from the cached bars with no extra data, which is why
it is the credential-free default. A bar's own high-low range contains the spread once, while a
two-bar range contains it once but spans twice the variance; comparing the two
separates the spread from the volatility. Negative estimates are clamped to zero
per the paper's convention, and a session whose estimate clamps is treated as
_unmeasured_ rather than free — it falls back to `slippage_bps`, because an
unmeasurable spread is not a free trade.

Measured across 50 symbols and 600 sessions the estimate is **1.16 bps per leg**
against the 1.0 bps guess, so expectancy falls by about 0.011R at 1m. No verdict
changes at 1m and nothing earns `EDGE` under either model.

But the estimator is not the observed spread, and it reads **low**. Against the
sampled quote table over two years at 1m, a run charges **1.83 bps per leg
observed against 1.28 estimated**, and mean expectancy moves **&minus;0.074R —
worse, not better**. A spot check on AAPL alone had suggested the opposite (0.45
observed against 1.21 estimated, implying the estimator charged nearly three
times too much); AAPL is the most liquid name in this universe and did not
generalise. Across all 50 symbols the observed median is 1.52 bps per leg at
midday and 2.67 at the open.

So the central finding is **stronger** than the estimator implied: costs beat the
patterns' edge by a wider margin than it charged. Still no `EDGE` under any of
the three models.

One thing the estimator cannot do is price each timeframe separately. Run on each
timeframe's own bars it returns a round-trip spread that climbs monotonically with
bar length — about 1.8 bps at 1m, 4.6 at 5m, 8.1 at 15m, 11.3 at 30m and 15.1 at
1h over the same symbols and days. A spread cannot depend on how finely you slice
the bars you look at; a trader holding an hour pays the same spread to get in as
one holding a minute. That climb is the estimator's volatility bias and it
survives every aggregation tried, including pooling beta and gamma before solving
and averaging the raw pair estimates before clamping. So the spread is estimated
**once per symbol and session from the narrowest enabled interval** and charged
unchanged everywhere. Pricing each timeframe separately would have charged ten
times as much at 1h and made the coarse intervals cost-dominated by
construction — manufacturing this project's finding rather than testing it.

One guard sits above all of this. `S = 2(e^a - 1)/(1 + e^a)` asymptotes to 2.0,
so two degenerate bars can "estimate" a round-trip spread of 200% of price, and
charging that would double the entry price and label it measured. Any estimate
above `MAX_PLAUSIBLE_SPREAD` (2%) is therefore treated as unmeasured and falls
back to `slippage_bps`, counted in the run's fallback total so it stays visible.
The ceiling is deliberately generous: across 9,348 real symbol-sessions the
worst 1m estimate is under 50 bps and the worst at any timeframe is 103 bps, on
a 1h session of seven bars. It fires on 2 of 2,100 cached 1h sessions and on
none at all at 1m, 5m, 15m or 30m — a tighter ceiling would substitute the flat
guess for a real if noisy measurement, which is the opposite of the point.

Set `model = "fixed"` to go back to a flat `slippage_bps`, which is also the way
to measure how much the cost model moved a result.

### Observed spreads

```bash
export ALPACA_API_KEY=... ALPACA_SECRET_KEY=...
python -m candlebench quotes --sessions 4   # writes .cache/bars/quoted_spreads.json
```

Samples historical NBBO quotes into a median half-spread per symbol and
time-of-day bucket, then `[costs] model = "quoted"` charges it. Nine requests per
symbol-session, paced inside the free tier's 200-per-minute budget.

The spread is **not one number per symbol**, which is why the table is bucketed:

| Bucket              | Median bps/leg | Min  | Max   |
| ------------------- | -------------- | ---- | ----- |
| open (first 30 min) | 2.67           | 0.13 | 16.07 |
| midday              | 1.52           | 0.13 | 4.44  |
| close (last 30 min) | 1.02           | 0.13 | 5.26  |

Sampled at 09:31, midday and 15:45 across five symbols and three sessions, the
open runs a median **4.0x midday** and as much as 7.7x: AAPL 1.50 against 0.30,
XOM 7.11 against 1.33. `engine.simulate` therefore takes a per-bar cost and
charges each leg at the bar it actually filled on — which matters because the
time-of-day breakdown shows the open is where an apparent intraday edge usually
lives.

Sampling is a stated limit. A liquid name quotes 38 to 65 times a second, so
reading every quote for 50 symbols over two years is out of reach on a free tier.
The table takes a few seconds per bucket across a few sessions and uses the
median, which cannot capture a spread that widened on one specific day.

### Where bars come from

`[run] source` selects the provider, and each caches under its own directory —
the two disagree on prices by design, since Alpaca bars are split-adjusted and
Yahoo's with `auto_adjust=False` are not, so one file half from each would carry
a fabricated gap where they met.

|                 | `yfinance` (default) | `alpaca`                                |
| --------------- | -------------------- | --------------------------------------- |
| Credentials     | none                 | free API key, environment only          |
| 1m history      | ~28 days             | back to 2016                            |
| Coarser history | ~59 days             | back to 2016                            |
| Sub-minute      | none                 | 1s, 5s, 10s, 30s, resampled from trades |
| Adjustment      | raw                  | split                                   |

Alpaca needs `ALPACA_API_KEY` and `ALPACA_SECRET_KEY` exported in your shell. They are read
from the environment and nowhere else: a key in the TOML would be committed, and
a key accepted from the browser would be echoed into a saved report.

Three things were measured against the live API, because each would have been
wrong to assume:

- **The free tier serves the whole tape.** Against yfinance's consolidated 1m
  volume over the same minutes, the default feed returned 102.56% for AAPL,
  99.70% for MSFT and 102.99% for KO — identical to an explicit SIP feed — while
  the IEX feed returned 4.47%, 5.50% and 7.08%. Bars built from 4% of the tape
  would have measured IEX microstructure and called it the market.
- **Splits must be adjusted for; dividends must not.** Unadjusted, AAPL opens at
  503.50 on 2020-08-28 and 128.05 on 2020-08-31 — an apparent 75% collapse that
  is entirely the 4:1 split, and a bar a detector would score as a pattern.
  Split-adjusted, the same bars read 125.88 and 128.05, the real move. Adjusting
  for dividends as well would rewrite historical prices for every later payout,
  moving the body and shadows away from what actually traded.
- **The 15-minute SIP restriction costs a chunk, not a row.** A window ending
  _now_ returns HTTP 403 for the whole request, so the first real 120-day fetch
  returned 62 sessions ending a month early. Windows now stop 16 minutes short of
  the present, and the same fetch returns 83 sessions reaching today.

### Sub-minute bars

Neither provider serves one: Yahoo has no interval below 1m and Alpaca's bar
endpoint rejects every sub-minute timeframe. `1s`, `5s`, `10s` and `30s` are
built by resampling raw trade prints, which raises two questions the data had to
answer.

**Which prints may set a price.** Trades were resampled to 1m and compared
against the provider's own 1Min bars over 171,317 prints and 75 bars across five
symbols. Excluding `W, 4, I, 7, V` — average price, derivatively priced, odd lot,
and the two contingent-trade codes — reproduces the OHLC on 74 of 75. Excluding
only `W, 4` reproduces none of them, because odd lots are over half a liquid
name's prints and are not last-sale eligible. Adding the other documented
ineligible codes changes nothing on this sample, so they are left out: excluding
an eligible code would drop a legitimate extreme, the same error inverted. One
bar in 75 still disagrees, and that is stated rather than hidden.

**Volume and price follow different rules.** Reproducing the provider's volume
needs every print counted, including the ones excluded from the price. Filtering
both matched volume on 0 of 5 bars and came out 21% light.

An interval nobody traded in produces no bar. Forward-filling the previous close
is conventional and would be wrong here: it invents a doji at a price nobody
traded, and a doji is one of the patterns being detected.

Scale is the binding constraint, and it belongs to the data rather than the code.
A liquid name prints on the order of a million trades a day against a 10,000-row
page, so one symbol-day is about a hundred requests. `config.validate` refuses a
sub-minute run above 20 symbol-days and states that arithmetic, because a default
must not be able to start an hours-long download.

---

## Reading the leaderboard

Historical output under earlier inference rules, pooled across timeframes, 50 symbols and 1,000 sessions:

```
   #  pattern                trades   win%   exp R          95% CI  vs ctrl     PF  consist  verdict
   1  bearish_engulfing        2242   43.1   -0.06   [-0.10,-0.01]   +0.07   0.88     41.5  NEGATIVE
   2  tweezer_top              4739   41.7   -0.07   [-0.11,-0.04]   +0.05   0.87     43.0  NEGATIVE
   3  bearish_harami           1626   41.6   -0.08   [-0.14,-0.03]   +0.04   0.86     43.7  NEGATIVE
   7  random_short ★           6058   38.1   -0.12   [-0.15,-0.09]     n/a   0.80     34.0  NEGATIVE
  13  random_long ★            6015   37.0   -0.15   [-0.18,-0.12]     n/a   0.77     31.0  NEGATIVE
```

Read that carefully: the top rows lose money, and so do the controls at ranks 7
and 13. The patterns are ahead of random entry — that is what `vs ctrl` says —
but not far enough ahead to pay the costs.

| Column       | Meaning                                                                                                                                                                                                                                                                                                                                                                                               |
| ------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `trades`     | closed trades. Small numbers make every other column unreliable.                                                                                                                                                                                                                                                                                                                                      |
| `win%`       | share of trades that made money. **Not** a measure of profitability.                                                                                                                                                                                                                                                                                                                                  |
| `exp R`      | **the headline.** Mean profit per trade in units of risk.                                                                                                                                                                                                                                                                                                                                             |
| `95% CI`     | pointwise market-date cluster bootstrap interval on `exp R`; corrected significance is separate.                                                                                                                                                                                                                                                                                                      |
| `vs ctrl`    | gross expectancy minus that of the pattern's own matched controls (same direction and stop distance, random entry 1-5 bars later): signal before costs. Read with paired interval and adjusted p-value.                                                                                                                                                                                               |
| `paired CI`  | pointwise date-paired 95% interval for the control difference.                                                                                                                                                                                                                                                                                                                                        |
| `adj p`      | Holm-adjusted significance for the paired difference, including declared experiment correction.                                                                                                                                                                                                                                                                                                       |
| `dates`      | distinct traded market dates in discovery.                                                                                                                                                                                                                                                                                                                                                            |
| `validation` | held-out candidate evidence, or explicit unavailable/not selected status.                                                                                                                                                                                                                                                                                                                             |
| `PF`         | profit factor: gross wins over gross losses.                                                                                                                                                                                                                                                                                                                                                          |
| `consist`    | share of trials whose own expectancy was positive. A trial is one symbol on one day, so this asks whether the pattern works on a typical day.                                                                                                                                                                                                                                                         |
| `stab`       | share of walk-forward windows whose own expectancy was positive — whether the sign survives from one stretch of calendar time to the next. A window must clear `min_trades` to count, the same floor the verdict answers to, because stability takes the _sign_ of each window's mean. `n/a` under two qualifying windows, since a single period cannot show that anything persists. Shown with `-v`. |
| `verdict`    | see below.                                                                                                                                                                                                                                                                                                                                                                                            |

**Expectancy, not win rate.** A pattern winning 70% at 1:1 and one winning 35%
at 3:1 have identical expectancy. A high win rate with poor expectancy is the
usual way a pattern looks good and loses money — nine small wins and one large
loss is a 90% win rate that bleeds.

**The starred rows are the reference.** `random_long` and `random_short` enter
at randomly chosen bars at a matched rate and flow through the identical engine,
metrics and ranking path as every real pattern. They show what random entry
earns, or loses, at each timeframe.

**Each pattern is compared with its own matched controls.** For every pattern
trade, `engine.simulate_matched` takes one random-entry trade with the same
direction and the same stop distance from the last close before entry. It
enters on a random bar one to five bars after the pattern and exits under the
same rules, through the same code. A pattern showing a negative `vs ctrl` has
demonstrated nothing.

**Signal is compared before costs; profit is tested after them.** Costs are a
fixed number of bps, so a pattern whose stop sits further from entry pays fewer
R for the same spread. Comparing in net R therefore rewards wide stops, not
signal: on the synthetic random walk, engulfing patterns (median stop 22.6 bps
against the shared control's 15.1) beat random entry at a corrected p of 0.042,
and at p = 1.0 once costs were removed. `vs ctrl` is gross R for that reason,
while `exp R` and its interval stay net.

**Controls enter after the pattern, never before.** Bars before a pattern's
entry were selected by the pattern: its formation, its trend gate, the exit of
the trade before it. A control entering there inherits that selection. Five
bars before a tweezer top, a matched short lost 0.56R on a random walk. See
[the calibration note](experiments/null-calibration.md) for the measurements.

### Verdicts

| Verdict        | Condition                                                                                                  |
| -------------- | ---------------------------------------------------------------------------------------------------------- |
| `EDGE`         | corrected positive net expectancy and paired gross advantage over its control in discovery, confirmed on later held-out dates |
| `NOISE`        | no confirmed edge                                                                                          |
| `NEGATIVE`     | corrected evidence of losses in discovery                                                                  |
| `INSUFFICIENT` | too few trades, independent dates, or no usable control                                                    |

Expect most rows to read `NOISE`, and treat that as the tool working. Twenty
patterns across five timeframes is a hundred comparisons, so roughly five will
look significant by chance alone. The controls and the intervals are the defence
against believing those five.

Ranking is by the **lower bound** of the interval, which prefers a modest
well-evidenced edge over a large unreliable one. Rows that cannot be measured
sort last rather than being dropped, because a pattern that produced nothing is
itself a finding.

### When costs swallow everything

At fine intervals the spread and slippage can outweigh whatever a random entry
earns. The control then loses money too, many rows read `NEGATIVE`, and the
verdict column stops telling patterns apart. The report names the losing control
and redirects you:

```
note: random_long reliably loses here after costs, so NEGATIVE verdicts partly
      reflect that cost drag.
      read the 'vs ctrl' column with its paired interval and corrected p-value.
```

A losing control shows what costs do to random entry in that direction. It does
not prove that costs exceed every edge a pattern could have.

A `NEGATIVE` pattern with positive `vs ctrl` may outperform its control while
still losing money. Read the paired interval and corrected p-value before
claiming a signal; a positive point estimate alone is not sufficient.

---

## The patterns

Ten bullish, ten bearish, plus two controls.

| Bars | Bullish                                                                                    | Bearish                                                                                    |
| ---- | ------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------ |
| 1    | `hammer`, `inverted_hammer`, `dragonfly_doji`                                              | `hanging_man`, `shooting_star`, `gravestone_doji`                                          |
| 2    | `bullish_engulfing`, `bullish_harami`, `piercing_line`, `tweezer_bottom`, `bullish_kicker` | `bearish_engulfing`, `bearish_harami`, `dark_cloud_cover`, `tweezer_top`, `bearish_kicker` |
| 3    | `morning_star`, `three_white_soldiers`                                                     | `evening_star`, `three_black_crows`                                                        |
| —    | `random_long` (control)                                                                    | `random_short` (control)                                                                   |

**Prior trend is part of the definition.** `hammer` and `hanging_man` are the
same geometry; they differ only in the trend that precedes them, as do
`inverted_hammer`/`shooting_star` and `dragonfly_doji`/`gravestone_doji`. The
trend is measured as the normalised least-squares slope of the closes over
`trend_lookback` bars ending at the bar _before_ the pattern starts, so a
pattern's own bars cannot define the trend it is supposed to reverse.

Adding a pattern is one decorated function in `candlebench/patterns/`:

```python
@pattern("my_pattern", bias="bull", bars_required=2, requires_trend=-1)
def my_pattern(g, t) -> np.ndarray:
    return lag(g.is_bear) & g.is_bull & (g.close > lag(g.high))
```

The registry — not the detector — masks bars without enough history and applies
the trend gate, so a new detector cannot forget either rule.

---

## Configuration

Everything lives in `config/backtest.toml`. Unknown keys are **rejected**, not
ignored: a silently dropped typo in a threshold would change what the run
measures with no visible sign.

```toml
[run]
trials = 200                   # (symbol, session) pairs to draw
seed = 42                      # same seed reproduces the run exactly
windows = 1                    # chronological slices to divide the trials between
intervals = ["1m", "5m", "15m", "30m", "1h"]
cache_dir = ".cache/bars"
throttle_s = 0.3               # pause between fetch requests

[universe]
symbols = []                   # empty selects the built-in top-50 liquid list
sample_size = 50               # how many of those trials may draw from

[trade]
stop_buffer = 0.001            # pad the stop past the pattern's extreme
reward_multiple = 2.0          # target distance in units of risk
max_hold_bars = 20
min_risk_pct = 0.0005          # discard signals whose stop is within a tick
risk_per_trade_usd = 100.0     # position size, so commission converts to R
allow_overlapping_trades = false

[costs]
model = "estimated"            # estimated (Corwin-Schultz) | fixed
slippage_bps = 1.0             # used by the fixed model, and as the fallback
commission_per_trade = 0.0

[thresholds]                   # pattern geometry, as fractions of a bar's range
doji_body = 0.10               # body/range at or below this is a doji
small_body = 0.30
long_body = 0.60
shadow_dominance = 2.0         # long shadow must be this multiple of the body
opposite_shadow_max = 0.25
doji_shadow_min = 0.60
near_equal = 0.001             # tweezers tolerance, as a share of price
gap_min = 0.0                  # minimum kicker gap
trend_lookback = 10            # bars used to label the prior trend
trend_min_slope = 0.0

[stats]
min_trades = 30                # fewer reports INSUFFICIENT
min_trades_per_trial = 3       # a trial or window below this does not count
bootstrap_samples = 10000
rank_by = "ci_low"             # ci_low | expectancy_r | win_rate | profit_factor | total_return_pct
                               # (total_return_pct sums per-trade returns; it is not a portfolio return)

[patterns]
hammer = true                  # set false or delete a line to disable
# ... all 22
```

Disabling the controls is allowed but prints a warning: without them the
leaderboard cannot separate a pattern's edge from a timeframe-wide directional
drift.

---

## Timeframes and their limits

**Sub-minute timeframes are not supported.** yfinance exposes no interval below
`1m`. Building 1s/5s/10s/30s bars requires resampling raw trade ticks from a
credentialed provider (Databento, Alpaca, Polygon). Adding one would touch only
`candlebench/bars.py`; nothing downstream knows where bars came from.

Intraday lookback is also capped, which bounds what "a random historical day"
can mean:

| Interval | Usable history                | Bars per session |
| -------- | ----------------------------- | ---------------- |
| `1m`     | last 28 days, in 7-day chunks | ~390             |
| `5m`     | last 59 days                  | ~78              |
| `15m`    | last 59 days                  | ~26              |
| `30m`    | last 59 days                  | ~13              |
| `1h`     | last 59 days                  | ~7               |

Those sit just inside Yahoo's documented 30 and 60 days deliberately. Yahoo
compares each request against _its own_ clock while a warm-up runs for minutes,
so a window built at exactly the limit is past it by the time a later request
lands. The `1m` figure is also empirical: a 7-day chunk ending 21 days ago
returns bars, one ending 28 days ago returns nothing.

Because `trend_lookback` counts bars, it may not fit a coarse session. When it
does not, the window is shortened by the minimum necessary and the run says so:

```
warning: 30m: trend_lookback reduced from 10 to 5 bars, because a typical
         session holds only 13 bars at this interval.
```

Trend context is then shorter there than at finer intervals, so **compare
patterns within an interval rather than across them.** Intervals with room to
spare keep the configured window untouched.

Trading is restricted to regular hours (09:30–16:00 ET) and no trade spans two
sessions: detectors receive one session's bars at a time, and an open position
is closed at that session's last bar.

---

## What it found

These are historical measurements under earlier inference rules. They have not
been recomputed with the corrected tests and held-out validation below.
Historical `EDGE` labels do not establish confirmation under the new rules.

**Correction, October 7, 2026.** Point 1 below compared patterns with their
controls in net R, which rewards wide stops: costs are fixed in bps, so a
pattern whose stop sits further away pays fewer R for the same spread. The
[null calibration](experiments/null-calibration.md) found that comparison
flagged rows as beating random entry on a synthetic random walk. Re-measured on
gross R over the same two years of 1m data, the advantages in point 1 largely
disappear: bullish engulfing moves from +0.09R to +0.00R, and no row beats its
control after correction. The README carries the current measurement.

Measured across 50 liquid symbols, 1,000 sessions and 200 trials at five
timeframes:

**No pattern earned `EDGE` at any timeframe.** None of the twenty.

The failures split three ways, and the distinction matters:

1. **Small real signal, destroyed by costs.** At `1m`, eight patterns beat
   random entry by +0.04R to +0.07R on 1,600–4,700 trades each, with intervals
   excluding zero. All still lose money absolutely, because random entry at
   `1m` loses −0.15R. Best by `vs ctrl`: `bearish_engulfing` (+0.066),
   `bullish_harami` (+0.064), `dragonfly_doji` (+0.062), `tweezer_top`
   (+0.052), `bearish_harami` (+0.045).
2. **Too rare intraday to judge.** `bullish_kicker`, `bearish_kicker`,
   `three_white_soldiers` and `three_black_crows` barely fire — a gap clear of
   the prior bar's high almost never happens inside a session.
3. **The trap the tool exists to catch.** At `30m`, `piercing_line` shows
   +0.95R and `dark_cloud_cover` +0.85R, both with intervals excluding zero. On
   **3 and 6 trades.** Without the `INSUFFICIENT` floor they would top the
   leaderboard as the find of the exercise. They are noise wearing a confident
   interval.

**Two years of history does not change it.** With `source = "alpaca"` and
`lookback_days = 730`, a 300-trial run over 236 sessions spanning 2024-10-03 to
2026-09-30 produced 49,062 trades at 1m and still no `EDGE`. The measured spread
over that window is 1.21 bps per leg against 1.09 over 28 days. Split into six
four-month windows, every pattern with a usable sample size has `stab` of 0.0 —
positive in none of the six — and so do both controls.

**Varying trade management at 1m also produces no `EDGE`.** A paired sweep of
1R, 2R and 3R targets with 5-, 20- and 60-bar holding caps, using the same 300
trials and sampled quoted spreads in every run, finds no `EDGE` in any of the
nine settings. Some individual periods become positive under longer holds;
the strongest measured stability is two of six qualifying windows. The stop
buffer stays fixed, and other timeframes have not been swept. See the
[experiment report](experiments/trade-management-sweep.md) for the full
statistics, input hashes and replay instructions.

**The stop buffer is a major part of risk even at 1m.** With the default
10 bps padding, its median contribution to accepted candlestick-trade risk
is 64.9%, and it supplies most of the risk in 76.0% of those trades. Removing
it makes 48.4% of candidate entries risk-ineligible before overlap suppression.
Buffers of 0, 2, 5, 10 and 20 bps all produce no `EDGE`; wider buffers bring
average R losses closer to zero while average net price losses remain around
3.3–3.7 bps per trade. Stops, position size and trade selection also change,
so those averages describe different trading rules. See the
[stop-buffer experiment](experiments/stop-buffer-sweep.md) for risk
attribution, selection counts and returns in both units. Sub-minute sensitivity
remains unmeasured.

**Sub-minute is overwhelmingly cost-dominated, and its geometry means something
else.** At 1s over three symbols and five sessions, every pattern is `NEGATIVE`
at about &minus;0.16R on 26&ndash;32% win rates with profit factors near 0.31.
Two cautions matter more than the numbers:

- The spread cannot be estimated from sub-minute bars. Corwin-Schultz collapses
  toward zero when most bars have no range, returning 2.028 bps round trip from
  1m bars and 0.074 from 1s on the same symbol and day. Priced from 1s bars the
  same run reported every pattern as `NOISE` at about &plusmn;0.02R &mdash; the
  finding inverted by an artefact. The estimator now skips sub-minute intervals
  and says so.
- 30% of AAPL's 1s bars and 68% of KO's have open = high = low = close, against
  0.00% at 1m. Those bars are real, not fabricated, but each is a perfect doji,
  and the doji, dragonfly, gravestone and hammer detectors read exactly that
  shape. At 1s they largely measure how often a single eligible print lands in
  one second. A run warns when more than 5% of bars have no range.

**Pricing the costs from observed quotes makes it worse, not better.** Charged
at the sampled NBBO half-spread rather than the estimator, mean 1m expectancy
falls a further 0.074R and the margin by which costs beat the edge widens. The
estimator had been understating the cost of trading.

**Nothing is stable across periods either.** Splitting the same 1m measurement
into four chronological windows, no pattern is positive in more than two of them,
and the random-entry controls are negative in all four. That is a weaker
statement than it sounds — four adjacent weeks are one regime — but it rules out
the reading that an edge exists and the pooled average merely hides it.

---

## Caveats

These bound every number above. Read them before acting on anything.

1. **History depends on the source.** Yahoo's lookback caps mean 28–59 days,
   so its adjacent walk-forward windows describe one short market period.
   Alpaca can cover years: the two-year 1m measurement above used six
   chronological windows and found no positive qualifying window for any
   pattern. Those windows divide the sampled history; they do not hold out
   unseen data or prove that every market regime is represented. Read the
   report's actual date coverage rather than assuming either source's limit.
2. **The spread is sampled, and under the default model only inferred.** With
   `model = "quoted"` it is observed from real NBBO quotes, but from a few
   sampled seconds per bucket across a few sessions — not every quote, and not
   a modelled order book, so a day on which spreads widened is priced at the
   symbol's typical cost. Under the default `estimated` it is inferred from
   high-low ranges and reads low: 1.28 bps per leg against 1.83 observed. Either
   way a real fill on a fast move is worse than any average, so the true cost is
   likely above what is charged here, not below.
3. **No short borrow cost or locate.** Short results are slightly optimistic.
4. **Survivorship.** The universe is today's liquid names, so anything that
   collapsed out of the list is absent.
5. **Multiple comparisons.** A hundred comparisons at a 5% threshold yields
   about five false positives by chance. Sweeping parameters adds comparisons,
   the historical confidence intervals did not adjust for that search or for trades
   clustered within a symbol/session. New verdicts use date clustering and Holm
   correction plus a declared experiment count. Pointwise intervals remain
   pointwise, and undeclared searches are not corrected. The controls are useful reference points;
   a verdict is not a substitute for reading the sample size or validating a
   candidate on unseen data.

This is a measurement tool, not trading advice.

---

## Validation and uncertainty

New runs reserve the newest 20% of distinct cached market dates before discovery
sampling. The requested trial count is split between discovery and validation.
Only candidates passing discovery are traded on the holdout. If none pass,
the holdout remains unevaluated. Set `run.holdout_fraction = 0` or use
`--holdout-fraction 0` for an exploratory replay; it cannot produce confirmed
`EDGE`. Impossible date/window splits fail explicitly.

Bootstrap draws resample whole market dates, keeping every symbol, repeated
trial and overlapping timeframe on that date together. Expectancy remains a
trade-weighted mean. Reports include pointwise 95% intervals for expectancy and
for the paired difference in gross R between each pattern and its own matched controls. A positive
average alone does not demonstrate a control advantage.

Verdicts require at least `stats.min_trades` trades and `stats.min_sessions = 10`
independent traded dates in each sample, including ten common traded dates for
the control comparison. Missing or thin controls cannot establish an edge.
Holm correction covers expectancy and control-difference hypotheses across all
enabled rows, including pooled intervals and unavailable hypotheses. Set
`stats.experiment_count` to all configurations tried in a declared parameter
sweep; it applies an additional conservative correction. The default 10,000
bootstrap samples improves p-value resolution for this larger testing family.
The pointwise intervals themselves are not simultaneous corrected intervals.

`EDGE` requires corrected positive expectancy and control advantage in both
discovery and later validation. `discovery_verdict` preserves candidate status;
`validation` contains separate evidence. Chronological windows describe
stability within discovery, not unseen validation. A short Yahoo cache may not
provide ten held-out traded dates, in which case confirmation is unavailable.

Keep the holdout unseen while choosing settings. Reusing it to choose parameters,
patterns or seeds invalidates confirmation. Historical caches already inspected
are not made genuinely unseen by this split. Date clustering preserves within-day
dependence but does not prove independence between dates or remove survivorship
bias.

JSON schema 2 records inference method, cutoff, sample counts, trial seeds,
windows and sample phases. CSV flattens validation metrics into `validation_*`
columns. Trade Parquet includes `sample`; older files load as exploratory
`discovery` trades. Dashboard drilldowns select discovery or validation without
mixing their trade counts, curves, breakdowns or session dates. Historical reports
are explicitly marked as lacking validation evidence. Drawdown includes initial
equity zero, so an initial loss is counted.

## Development

```bash
pip install -e ".[dev]"
pytest                      # Python suite; browser tests run when installed
pytest tests/test_patterns.py -v
```

Each of the 20 detectors has a hand-built positive case and a near miss failing
exactly one condition. The engine's exit rules are pinned with exact prices. A
look-ahead test asserts that the mask at bar `i` is unchanged when later bars
change — the property that keeps every downstream number meaningful.

```
candlebench/
  config.py       TOML loading, validation, frozen dataclasses
  universe.py     the built-in top-50 liquid symbol list
  bars.py         all data acquisition, interval caps, Parquet cache, sessions
  patterns/
    __init__.py   registry; applies the history and trend gates centrally
    context.py    bar geometry and prior-trend detection
    single.py     double.py   triple.py   control.py
  engine.py       signal -> closed trade
  sampling.py     paired (symbol, session) trial draws
  metrics.py      aggregation, bootstrap intervals, verdicts
  leaderboard.py  ranking and rendering
  runner.py       orchestration
  cli.py          argparse entry point
  web/            local server, static page, SVG charts
config/backtest.toml
docs/                       guide, design spec, experiment reports
tests/
```

Trials are **paired**: one trial draws a symbol and a session, then every
pattern runs on that same session at every timeframe. Independent draws per
pattern would mean leaderboard differences mostly reflected which pattern drew
a trending day, and would need orders of magnitude more trials for the same
confidence.

Browser checks (Chromium, no live market requests):

```bash
pip install -e ".[dev,browser]"
python -m playwright install chromium
python -m pytest tests/browser -q
```

See [browser test setup](../tests/browser/README.md). Tests cover configuration,
progress, failures and recovery, sorting, paging, saved-run comparison, validation
phases, historical reports and mobile layout. Playwright is a development-only
dependency; the application still uses vanilla JavaScript and `http.server`.
