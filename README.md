# stock-analyzer

Two command-line tools for equity analysis. They share no code and answer
different questions.

| Tool | Question it answers |
| --- | --- |
| **`stock.py`** | Is *this ticker* currently a reasonable candidate for long-term, short-term, day or swing trading? |
| **`candlebench`** | Do the classic candlestick patterns have a *measurable intraday edge* at all — and which are useless? |

---

## Quick start

```bash
# one-time setup
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# score a single stock
python stock.py AAPL

# download intraday bars, then measure every candlestick pattern
python -m candlebench fetch
python -m candlebench serve
```

`fetch` takes several minutes and only needs doing once per session of work.
`serve` opens a browser page with the leaderboard and charts.

Requires Python 3.11 or newer. Runtime dependencies: `yfinance`, `pandas`,
`numpy`, `pyarrow`.

---

# stock.py

Scores one ticker against four trading styles, using current fundamentals and
technicals from Yahoo Finance. Each style gets a percentage and a verdict.

```bash
python stock.py AAPL        # one line per trading style
python stock.py AAPL -v     # every metric with its reading and reasoning
python stock.py AAPL -i     # raw indicator readings only, no verdicts
python stock.py             # prompts for a ticker
```

A metric with no data available scores `None` and is **excluded** from the
percentage rather than counted as a failure, so a thinly covered ticker is not
penalised for Yahoo's gaps. Styles reading `INCONCLUSIVE` had too little data to
judge.

What each style looks at:

- **Long-term** — EPS and revenue trends, P/E, P/B, return on equity,
  debt-to-equity, profit margin, dividend payout, beta.
- **Short-term** — SMA20/SMA50 trend, RSI(14), Bollinger position, upcoming
  earnings, average volume, bid-ask spread.
- **Day trading** — the short-term signals plus average daily range, beta,
  volume depth, and today's move.
- **Swing trading** — the short-term signals plus support/resistance,
  Bollinger-width consolidation, double top/bottom detection, and daily-vs-weekly
  trend alignment.

Day and swing trading build on the short-term score, so those metrics are
counted once and printed once, under the style that owns them.

---

# candlebench

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

Downloads the maximum history Yahoo permits for each symbol and interval and
writes it to `.cache/bars/{interval}/{symbol}.parquet`. Runs are then read
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
  for. The levels come from the stored trades rather than from re-deriving them.
- a **paged trade table**, sortable by any column, server-side so page two of a
  sorted table continues page one.

The last run is persisted, so reopening shows results rather than an empty table,
and the last ten runs are kept so two can be compared: pick a baseline and an
"against" run to see expectancy move per pattern, which verdicts changed, and
which settings actually differ between them.

No frontend dependencies: `http.server`, hand-rolled SVG, vanilla JavaScript.

#### HTTP API

The page uses nothing the command line cannot. Every endpoint is loopback-only.

| Route | Returns |
| --- | --- |
| `GET /api/meta` | patterns, intervals, breakdown keys, cost models, the base config |
| `GET /api/results` | the last run's report |
| `GET /api/status` | progress of a run or fetch in flight |
| `GET /api/cache` | what the bar cache holds per interval |
| `GET /api/trades?pattern=&interval=&symbol=&sort=&desc=&limit=&offset=&run=` | a counted page of trades |
| `GET /api/breakdown?by=&pattern=&interval=&run=` | one grouping of those trades |
| `GET /api/equity?pattern=&interval=&run=` | cumulative R and its matched control |
| `GET /api/session?symbol=&session=&interval=&pattern=` | one session's bars, signal mask and trade levels |
| `GET /api/runs` / `GET /api/runs?id=` | the saved run list, or one saved report |
| `POST /api/run` / `POST /api/fetch` | start work |

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
  bar touches both levels   -> STOP wins
  max_hold_bars reached     -> exit at that bar's close  (timeout)
  session's last bar        -> exit at its close         (session_end)
```

Short patterns mirror this exactly, using the pattern's highest high.

Two choices are deliberately pessimistic. A bar records only open, high, low
and close, so when it touches both the stop and the target there is no way to
know which came first — assuming the favourable one is the most common way a
backtest flatters itself. And gaps fill at the open because that is what
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

It is now estimated from the cached bars with the Corwin and Schultz (2012)
high-low estimator. A bar's own high-low range contains the spread once, while a
two-bar range contains it once but spans twice the variance; comparing the two
separates the spread from the volatility. Negative estimates are clamped to zero
per the paper's convention, and a session whose estimate clamps is treated as
*unmeasured* rather than free — it falls back to `slippage_bps`, because an
unmeasurable spread is not a free trade.

Measured across 50 symbols and 600 sessions the estimate is **1.16 bps per leg**
against the 1.0 bps guess, so expectancy falls by about 0.011R at 1m. No verdict
changes at 1m and nothing earns `EDGE` under either model: the headline
conclusion is unchanged, and now measured.

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

Set `model = "fixed"` to go back to a flat `slippage_bps`, which is also the way
to measure how much the cost model moved a result.

---

## Reading the leaderboard

Real output, pooled across timeframes, 50 symbols and 1,000 sessions:

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

| Column | Meaning |
| --- | --- |
| `trades` | closed trades. Small numbers make every other column unreliable. |
| `win%` | share of trades that made money. **Not** a measure of profitability. |
| `exp R` | **the headline.** Mean profit per trade in units of risk. |
| `95% CI` | bootstrap interval on `exp R`. Crossing zero means not distinguishable from chance. |
| `vs ctrl` | `exp R` minus its random-entry control's. The answer to "is there signal here at all". |
| `PF` | profit factor: gross wins over gross losses. |
| `consist` | share of trials whose own expectancy was positive. A trial is one symbol on one day, so this asks whether the pattern works on a typical day. |
| `stab` | share of walk-forward windows whose own expectancy was positive — whether the sign survives from one stretch of calendar time to the next. `n/a` at one window, since a single period cannot show that anything persists. Shown with `-v`. |
| `verdict` | see below. |

**Expectancy, not win rate.** A pattern winning 70% at 1:1 and one winning 35%
at 3:1 have identical expectancy. A high win rate with poor expectancy is the
usual way a pattern looks good and loses money — nine small wins and one large
loss is a 90% win rate that bleeds.

**The starred rows are the point.** `random_long` and `random_short` enter at
randomly chosen bars at a matched rate and flow through the identical engine,
metrics and ranking path as every real pattern. They are the noise floor. A
pattern ranked below its control, or showing a negative `vs ctrl`, has
demonstrated nothing.

### Verdicts

| Verdict | Condition |
| --- | --- |
| `EDGE` | the interval excludes zero **and** it beats its control |
| `NOISE` | indistinguishable from chance |
| `NEGATIVE` | reliably loses money |
| `INSUFFICIENT` | fewer than `min_trades` trades |

Expect most rows to read `NOISE`, and treat that as the tool working. Twenty
patterns across five timeframes is a hundred comparisons, so roughly five will
look significant by chance alone. The controls and the intervals are the defence
against believing those five.

Ranking is by the **lower bound** of the interval, which prefers a modest
well-evidenced edge over a large unreliable one. Rows that cannot be measured
sort last rather than being dropped, because a pattern that produced nothing is
itself a finding.

### When costs swallow everything

At fine intervals the spread and slippage can exceed any edge a pattern could
have. The control then loses money too, every row reads `NEGATIVE`, and the
verdict column stops telling patterns apart. The report says so and redirects
you:

```
note: random entry itself loses here, so costs exceed any pattern edge at
      this interval. read the 'vs ctrl' column, not the verdict.
```

A `NEGATIVE` pattern with a positive `vs ctrl` has real signal that the costs
ate. That is a different finding from a pattern that simply does not work, and
the verdict alone cannot distinguish them.

---

## The patterns

Ten bullish, ten bearish, plus two controls.

| Bars | Bullish | Bearish |
| --- | --- | --- |
| 1 | `hammer`, `inverted_hammer`, `dragonfly_doji` | `hanging_man`, `shooting_star`, `gravestone_doji` |
| 2 | `bullish_engulfing`, `bullish_harami`, `piercing_line`, `tweezer_bottom`, `bullish_kicker` | `bearish_engulfing`, `bearish_harami`, `dark_cloud_cover`, `tweezer_top`, `bearish_kicker` |
| 3 | `morning_star`, `three_white_soldiers` | `evening_star`, `three_black_crows` |
| — | `random_long` (control) | `random_short` (control) |

**Prior trend is part of the definition.** `hammer` and `hanging_man` are the
same geometry; they differ only in the trend that precedes them, as do
`inverted_hammer`/`shooting_star` and `dragonfly_doji`/`gravestone_doji`. The
trend is measured as the normalised least-squares slope of the closes over
`trend_lookback` bars ending at the bar *before* the pattern starts, so a
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
bootstrap_samples = 2000
rank_by = "ci_low"             # ci_low | expectancy_r | win_rate | profit_factor | total_return_pct

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

| Interval | Usable history | Bars per session |
| --- | --- | --- |
| `1m` | last 28 days, in 7-day chunks | ~390 |
| `5m` | last 59 days | ~78 |
| `15m` | last 59 days | ~26 |
| `30m` | last 59 days | ~13 |
| `1h` | last 59 days | ~7 |

Those sit just inside Yahoo's documented 30 and 60 days deliberately. Yahoo
compares each request against *its own* clock while a warm-up runs for minutes,
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

**Nothing is stable across periods either.** Splitting the same 1m measurement
into four chronological windows, no pattern is positive in more than two of them,
and the random-entry controls are negative in all four. That is a weaker
statement than it sounds — four adjacent weeks are one regime — but it rules out
the reading that an edge exists and the pooled average merely hides it.

---

## Caveats

These bound every number above. Read them before acting on anything.

1. **One market regime.** The lookback caps mean 28–59 days of history. A
   pattern that worked across that window has been tested once, not across
   conditions. `windows = N` splits it into adjacent stretches and reports
   `stab`, which is worth reading — measured over 120 trials at 1m in four
   windows, no pattern clears 0.5, the best are positive in two windows of four,
   and both controls are negative in all four. But adjacent weeks of one regime
   are not independent regimes, so stability here is necessary for an edge and
   nowhere near sufficient.
2. **The spread is estimated, not observed.** Corwin-Schultz infers it from
   high-low ranges; it is not a quote feed and not a modelled order book. It is
   also charged at the 1m estimate across every timeframe, for the reason given
   in [The cost model](#the-cost-model). Real fills at `1m` on a fast move are
   worse than any average, so the true cost is likely above what is charged
   here, not below.
3. **No short borrow cost or locate.** Short results are slightly optimistic.
4. **Survivorship.** The universe is today's liquid names, so anything that
   collapsed out of the list is absent.
5. **Multiple comparisons.** A hundred comparisons at a 5% threshold yields
   about five false positives by chance. The controls and intervals are the
   defence; the verdict column is not a substitute for reading `trades`.

This is a measurement tool, not trading advice.

---

## Development

```bash
pip install -e ".[dev]"
pytest                      # 254 tests
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
docs/superpowers/specs/     design spec
tests/
```

Trials are **paired**: one trial draws a symbol and a session, then every
pattern runs on that same session at every timeframe. Independent draws per
pattern would mean leaderboard differences mostly reflected which pattern drew
a trending day, and would need orders of magnitude more trials for the same
confidence.
