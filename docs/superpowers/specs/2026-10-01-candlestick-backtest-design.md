# Candlestick Pattern Backtest — Design

Date: 2026-10-01
Status: approved design, pending implementation plan

## Purpose

Find out which classic candlestick patterns produce a measurable trading edge on
intraday data, and which ones do not. The deliverable is a ranked leaderboard
covering 20 widely used patterns, driven entirely by a config file, with enough
statistical honesty that a pattern cannot look good purely by luck.

The question being answered is narrow and worth stating precisely: *given a
pattern signal on an intraday chart, and a mechanical risk-managed trade taken
on the next bar, does the resulting distribution of returns differ from the
distribution produced by entering at random?* A pattern that cannot beat a
random entry on the same data has no demonstrated edge, regardless of how high
its raw win rate is.

## Success criteria

1. `python -m candlebench run` produces a ranked leaderboard with no arguments,
   reading defaults from `config/backtest.toml`.
2. Every element the user asked to configure is configurable: which patterns
   run, which timeframes run, which symbols are sampled, the trade parameters
   (stop buffer, reward multiple, max holding bars, costs), and the trial count.
3. Each of the 20 patterns has a detector with a unit test proving one known
   positive and one known negative case.
4. The engine provably takes no look-ahead: detection at bar `i` reads only bars
   up to and including `i`, and entry occurs at bar `i+1`. A test asserts this.
5. A repeated run with the same seed produces byte-identical output.
6. Patterns whose confidence interval straddles zero are reported as noise
   rather than ranked as if the difference were real.

## Scope boundary: data availability

The project originally asked for 1-second, 5-second, 10-second, and 30-second
timeframes. These are **out of scope**, by decision, because the chosen data
source cannot supply them.

yfinance exposes only these intervals: `1m, 2m, 5m, 15m, 30m, 60m, 90m, 1h, 1d,
5d, 1wk, 1mo, 3mo`. There is no sub-minute interval. Sub-minute bars can only be
produced by resampling raw trade ticks, which requires a credentialed provider
(Databento, Alpaca, or Polygon). The decision was to stay on yfinance with no
API keys, accepting the reduced timeframe set.

yfinance also caps intraday lookback, which bounds what "a random historical
day" can mean:

| Interval | Usable history |
| --- | --- |
| `1m` | last 30 days, retrievable in 7-day chunks |
| `2m`, `5m`, `15m`, `30m`, `1h` | last 60 days |

Default enabled timeframes are therefore `1m, 5m, 15m, 30m, 1h`, and a sampled
day is drawn from within the window permitted for the interval in question.
`2m` is supported but disabled by default, since it adds little beyond `1m` and
`5m`.

If a tick provider is added later, the only module that must change is
`bars.py`; nothing downstream depends on where bars came from.

## Architecture

A new `candlebench` package sits alongside the existing `stock.py`. The existing
script is not modified. The two share no code, because they answer different
questions, but the new package follows its conventions: dataclasses for value
objects, pure functions for scoring, plain-text output with a concise default
and a verbose flag.

```
stock-analyzer/
├── stock.py                      unchanged
├── pyproject.toml                dependencies and pytest config
├── config/
│   └── backtest.toml             default configuration
├── candlebench/
│   ├── __init__.py
│   ├── config.py                 TOML load, validation, frozen dataclasses
│   ├── universe.py               the top-50 liquid symbol list
│   ├── bars.py                   yfinance fetch, interval caps, Parquet cache
│   ├── patterns/
│   │   ├── __init__.py           registry and @pattern decorator
│   │   ├── context.py            bar geometry primitives and trend detection
│   │   ├── single.py             6 single-bar patterns
│   │   ├── double.py             10 two-bar patterns
│   │   ├── triple.py             4 three-bar patterns
│   │   └── control.py            random-entry null baselines
│   ├── engine.py                 signal to closed-trade simulation
│   ├── sampling.py               trial draws
│   ├── metrics.py                per-pattern aggregation and bootstrap
│   ├── leaderboard.py            ranking and rendering
│   └── cli.py                    argparse entry point
└── tests/
```

Each module has one job and a narrow interface. `bars.py` turns a symbol and
interval into a DataFrame and knows nothing about patterns. The detectors turn
an array of bars into a boolean mask and know nothing about trades. `engine.py`
turns a mask into closed trades and knows nothing about statistics.
`metrics.py` turns trades into numbers and knows nothing about formatting.

### Data flow

```
config.toml
   │
   ├─> universe.py ──┐
   │                 ├─> sampling.py ──> trials: [(symbol, window)]
   └─> bars.py ──────┘                        │
         (Parquet cache)                      │
                                              v
                        for each trial, for each interval:
                             bars: ndarray(o,h,l,c,v)
                                   │
                                   v
                        for each enabled pattern:
                             detector ──> bool mask ──> engine ──> trades
                                                                     │
                                                                     v
                                               metrics.py ──> per-pattern stats
                                                                     │
                                                                     v
                                               leaderboard.py ──> ranked output
```

## Module: bars.py

Responsible for all data acquisition and the interval caps.

```python
INTERVAL_MAX_LOOKBACK_DAYS = {"1m": 30, "2m": 60, "5m": 60,
                              "15m": 60, "30m": 60, "1h": 60}
INTERVAL_CHUNK_DAYS = {"1m": 7}   # anything absent fetches in one request

def warm_cache(symbols: list[str], intervals: list[str],
               cache_dir: Path, throttle_s: float) -> CacheReport
def load(symbol: str, interval: str, cache_dir: Path) -> pd.DataFrame
```

`warm_cache` downloads the maximum permitted window for each interval and
writes `{cache_dir}/{interval}/{symbol}.parquet` with a UTC `DatetimeIndex` and
columns `open, high, low, close, volume`. Symbols are batched through
`yf.download(tickers=[...])` to cut request count; `1m` loops over 7-day
sub-windows. Failures are collected into the returned `CacheReport` rather than
raised, so one delisted or illiquid symbol cannot abort a warm-up.

Trials never hit the network. A missing cache entry is an error telling the user
to run `candlebench fetch`, not a silent download mid-measurement, because an
implicit fetch would make run times unpredictable and could introduce different
data between patterns in the same trial.

Rows where `volume == 0` or any of OHLC is NaN are dropped on load. Bars are
validated: `low <= min(open, close)` and `high >= max(open, close)`. Violating
rows are dropped and counted, because Yahoo intraday data does contain
occasional bad bars and a bad bar would otherwise fabricate a pattern.

### Session handling

Day trading does not hold through an overnight gap, so bars are grouped by
trading session (US/Eastern calendar date) on load. Regular hours only
(09:30–16:00 ET); `prepost=False`. A trade is force-closed at the last bar of
its session. No pattern may span two sessions: the detectors receive one
session's bars at a time.

## Module: universe.py

A hardcoded list of 50 large, consistently liquid US equities and ETFs
(mega-cap technology, the major index ETFs, high-volume financials and energy).
Hardcoded rather than screened at runtime so that runs are reproducible and do
not depend on a screener endpoint. The list is overridable in config, which is
the escape hatch if the user wants a different universe.

## Module: patterns/context.py

Geometry primitives, computed once per bar array and passed to every detector so
the work is not repeated 20 times:

```python
@dataclass(frozen=True)
class Geometry:
    open: np.ndarray; high: np.ndarray
    low: np.ndarray;  close: np.ndarray
    rng: np.ndarray           # high - low
    body: np.ndarray          # abs(close - open)
    body_top: np.ndarray      # maximum(open, close)
    body_bottom: np.ndarray   # minimum(open, close)
    upper_shadow: np.ndarray  # high - body_top
    lower_shadow: np.ndarray  # body_bottom - low
    body_ratio: np.ndarray    # body / rng, 0 where rng == 0
    is_bull: np.ndarray       # close > open
    is_bear: np.ndarray       # close < open
    trend: np.ndarray         # -1 down, 0 flat, +1 up
```

`rng == 0` is possible on a thin 1-minute bar where all four prices are equal.
Every ratio guards against it by yielding 0, and such bars match no pattern.

### Trend definition

`hammer` and `hanging_man` are the same geometry; they differ only in the trend
that precedes them. The reference implementation ignores this, which is why it
reports two different results for one shape. Prior trend is therefore a first
class input.

Trend at bar `i` is the ordinary least squares slope of the closes over the
`trend_lookback` bars ending at `i`, normalised by their mean:

```
slope_i = sum_k (k - k_mean) * close[i-w+1+k]  /  sum_k (k - k_mean)^2
trend_i = +1 if slope_i / mean(window) >=  trend_min_slope
          -1 if slope_i / mean(window) <= -trend_min_slope
           0 otherwise
```

Because the `(k - k_mean)` weights are constant for a fixed window, this is a
fixed-weight rolling dot product and is computed for the whole array with one
convolution.

A pattern occupying bars `[i-n+1, i]` reads the trend value at bar `i-n`, the
last bar before the pattern begins. Using the trend at `i` would let the
pattern's own bars define the trend they are supposed to reverse.

Bars where the trend window is incomplete get `trend = 0` and are excluded from
detection entirely.

## Module: patterns/__init__.py

A registry in the style of the JavaScript reference project's plugin API, which
had the cleanest separation of the three repositories surveyed.

```python
@dataclass(frozen=True)
class PatternSpec:
    name: str
    bias: Literal["bull", "bear"]
    bars_required: int
    requires_trend: int          # -1, 0 (any), or +1
    fn: Callable[[Geometry, Thresholds], np.ndarray]

def pattern(name, bias, bars_required, requires_trend): ...   # decorator
def registry() -> dict[str, PatternSpec]: ...
```

A detector returns a boolean mask the same length as the bar array, `True` at
the pattern's **final** bar. The registry wrapper, not the detector, is
responsible for two safety rules, so that no individual detector can forget
them:

- The first `bars_required - 1 + trend_lookback` entries of every mask are
  forced to `False`. The reference implementation loops over all rows including
  those without enough history; this removes that class of bug centrally.
- If `requires_trend` is non-zero, the mask is ANDed with
  `trend[i - bars_required] == requires_trend`.

Adding a pattern is one decorated function. No registration list to maintain.

## The 20 patterns

Thresholds in braces are config values, listed with defaults in the
configuration section. All comparisons are vectorised; `prev` means bar `i-1`
and `prev2` means bar `i-2`.

### Single bar (patterns/single.py)

| Pattern | Bias | Trend | Rule |
| --- | --- | --- | --- |
| `hammer` | bull | down | `body_ratio <= {small_body}`, `lower_shadow >= {shadow_dominance} * body`, `upper_shadow <= {opposite_shadow_max} * rng` |
| `inverted_hammer` | bull | down | as hammer with upper and lower shadows exchanged |
| `hanging_man` | bear | up | hammer geometry |
| `shooting_star` | bear | up | inverted hammer geometry |
| `dragonfly_doji` | bull | down | `body_ratio <= {doji_body}`, `lower_shadow >= {doji_shadow_min} * rng`, `upper_shadow <= {opposite_shadow_max} * rng` |
| `gravestone_doji` | bear | up | dragonfly with shadows exchanged |

### Two bar (patterns/double.py)

| Pattern | Bias | Trend | Rule |
| --- | --- | --- | --- |
| `bullish_engulfing` | bull | down | `prev.is_bear`, `is_bull`, `body_bottom <= prev.body_bottom`, `body_top >= prev.body_top` |
| `bearish_engulfing` | bear | up | mirror |
| `bullish_harami` | bull | down | `prev.is_bear`, `prev.body_ratio >= {long_body}`, `is_bull`, `body_top <= prev.body_top`, `body_bottom >= prev.body_bottom` |
| `bearish_harami` | bear | up | mirror |
| `piercing_line` | bull | down | `prev.is_bear`, `prev.body_ratio >= {long_body}`, `open < prev.close`, `close > prev.body_bottom + 0.5 * prev.body`, `close < prev.open` |
| `dark_cloud_cover` | bear | up | mirror |
| `tweezer_bottom` | bull | down | `prev.is_bear`, `is_bull`, `abs(low - prev.low) <= {near_equal} * prev.low` |
| `tweezer_top` | bear | up | `prev.is_bull`, `is_bear`, `abs(high - prev.high) <= {near_equal} * prev.high` |
| `bullish_kicker` | bull | any | `prev.is_bear`, `is_bull`, `open >= prev.high * (1 + {gap_min})` |
| `bearish_kicker` | bear | any | `prev.is_bull`, `is_bear`, `open <= prev.low * (1 - {gap_min})` |

Kickers carry `requires_trend = 0` deliberately: the gap is the signal, and
classic descriptions do not condition a kicker on a prior trend.

### Three bar (patterns/triple.py)

| Pattern | Bias | Trend | Rule |
| --- | --- | --- | --- |
| `morning_star` | bull | down | `prev2.is_bear`, `prev2.body_ratio >= {long_body}`, `prev.body_ratio <= {small_body}`, `prev.body_top < prev2.body_bottom`, `is_bull`, `close > prev2.body_bottom + 0.5 * prev2.body` |
| `evening_star` | bear | up | mirror |
| `three_white_soldiers` | bull | down | three consecutive `is_bull` with `body_ratio >= {long_body}`, strictly rising closes, and each open inside the previous body |
| `three_black_crows` | bear | up | mirror |

### Controls (patterns/control.py)

`random_long` and `random_short` fire at randomly chosen bars at a rate matched
to the median signal rate of the enabled directional patterns on the same bar
array, drawn from the trial's seeded generator. They are ordinary registry
entries, so they flow through the identical engine, metrics, and ranking path as
every real pattern.

These are the reference line of the whole experiment. A pattern ranked above its
matched control has shown an edge on this data; a pattern ranked at or below it
has not, which is the direct answer to "which ones are useless."

## Module: engine.py

Converts a boolean signal mask into closed trades using the approved model.

```python
@dataclass(frozen=True)
class Trade:
    entry_index: int; exit_index: int
    entry_price: float; exit_price: float
    stop_price: float; target_price: float
    direction: int                  # +1 long, -1 short
    exit_reason: Literal["stop", "target", "timeout", "session_end"]
    r_multiple: float               # net of costs
    gross_r_multiple: float
    return_pct: float               # net

def simulate(geom, mask, spec, params, session_end_index) -> list[Trade]
```

For a signal at bar `i`, long side:

```
pattern_low = min(low[i - bars_required + 1 : i + 1])
entry       = open[i+1]
risk        = entry - pattern_low * (1 - stop_buffer)
stop        = entry - risk
target      = entry + risk * reward_multiple
```

The stop references the lowest low across *all* the pattern's bars, not just its
final bar, so a three-bar morning star is stopped below the whole formation.
Short side mirrors with the pattern's highest high. A signal with `risk <= 0`,
or `risk / entry < min_risk_pct`, is discarded: a stop within a tick of entry
produces a meaningless R multiple that would dominate the statistics.

Forward walk from bar `i+1`:

1. If the bar's open is already at or through the stop, exit at the open. Gaps
   fill at the open, not the level, because that is what actually happens.
2. Otherwise if the open is at or through the target, exit at the open.
3. Otherwise, if the bar touches both stop and target, **the stop wins**. Bar
   data cannot say which came first within the bar, and assuming the favourable
   one is the single most common way a backtest flatters itself.
4. Otherwise exit at the touched level, if either is touched.
5. On reaching `max_hold_bars`, exit at that bar's close.
6. On reaching the last bar of the session, exit at its close.

`max_hold_bars` is counted in bars, so its wall-clock meaning changes with the
interval: 20 bars is 20 minutes at `1m` but exceeds a single session at `1h`. At
the coarser intervals the session-end rule therefore becomes the binding exit,
which is correct for a day-trading study and needs no per-interval override.

Implementation is vectorised per signal rather than bar by bar: slice the
forward window, compute the first stop-touch index and first target-touch index
with `argmax` over the boolean comparisons, and compare. Signals are sparse, so
the cost is proportional to signal count, not bar count.

### Costs

`slippage_bps` is charged against the trader on both legs (entry filled worse,
exit filled worse) and `commission_per_trade` is divided across the position.
Both gross and net R multiples are retained so the leaderboard can show how much
of an apparent edge the costs consume — at 1-minute resolution this is often all
of it, and hiding it would be the single most misleading thing this tool could
do.

### Overlapping signals

Each pattern keeps its own independent position book. While a trade is open,
further signals from that same pattern are skipped when
`allow_overlapping_trades = false` (the default), which prevents one clustered
burst of signals from dominating the sample. Different patterns never interact.

## Module: sampling.py

```python
@dataclass(frozen=True)
class Trial:
    index: int; symbol: str; session: date; seed: int

def draw_trials(config, rng) -> list[Trial]
```

A trial draws one symbol and one trading session. Every enabled pattern then
runs on every enabled timeframe over **that same session**.

This pairing is a deliberate variance-reduction choice. If each pattern drew its
own random symbol and day, differences in the leaderboard would mostly reflect
which pattern happened to draw a trending day, and reaching usable confidence
would need orders of magnitude more trials. Paired sampling means every pattern
faces identical market conditions, so the comparison between patterns is clean
even when the absolute numbers are noisy.

Sessions are drawn from those actually present in the cache for the narrowest
enabled interval, so a drawn session is guaranteed to have `1m` coverage.
Trials are drawn without replacement where the pool allows.

All randomness derives from one root seed, and each trial gets a derived child
seed, so a single trial can be replayed in isolation for debugging.

## Module: metrics.py

Trades are pooled per `(pattern, interval)` across all trials.

| Metric | Definition |
| --- | --- |
| `signals` | signals detected, before risk filtering |
| `trades` | closed trades |
| `win_rate` | share of trades with `r_multiple > 0` |
| `expectancy_r` | mean net `r_multiple` — the headline number |
| `expectancy_r_gross` | mean gross `r_multiple` |
| `total_return_pct` | sum of `return_pct` |
| `profit_factor` | gross wins / gross losses, `inf` when no losses |
| `sharpe_per_trade` | `mean(r) / std(r)`, undefined for fewer than 2 trades |
| `max_drawdown_r` | deepest peak-to-trough decline of the cumulative R curve, trades ordered by entry time |
| `avg_bars_held` | mean holding period |
| `exit_mix` | share of exits by reason |
| `consistency` | share of trials whose own expectancy is positive, over trials having at least `min_trades_per_trial` trades |
| `expectancy_r_ci` | 95% bootstrap confidence interval on `expectancy_r` |
| `baseline_delta_r` | `expectancy_r` minus the matched control's `expectancy_r` at the same interval |

A pattern's matched control is chosen by bias: every `bull` pattern is compared
against `random_long`, every `bear` pattern against `random_short`, always at the
same interval. If the matching control is disabled in config,
`baseline_delta_r` is unavailable rather than zero, and the `EDGE` verdict falls
back to the confidence interval alone — with a warning printed once, because
without a control the leaderboard cannot distinguish a real edge from a
timeframe-wide directional drift.

Expectancy in R is the headline rather than win rate because win rate alone is
not a measure of profitability. A pattern that wins 70% of the time at a 1:1
reward multiple and a pattern that wins 35% of the time at 3:1 can have
identical expectancy, and a high win rate with poor expectancy is the most
common way a pattern looks good and loses money.

The bootstrap resamples the trade list with replacement `bootstrap_samples`
times (default 2000), takes the mean of each resample, and reports the 2.5th and
97.5th percentiles. Implemented in numpy; no scipy dependency.

A verdict is assigned from the interval and the trade count:

| Verdict | Condition |
| --- | --- |
| `INSUFFICIENT` | `trades < min_trades` |
| `EDGE` | `ci_low > 0` and `baseline_delta_r > 0` |
| `NEGATIVE` | `ci_high < 0` |
| `NOISE` | otherwise |

`NOISE` is the expected result for most patterns at most timeframes, and
labelling it plainly is the point of the exercise. Reporting a rank order
without confidence intervals would imply a precision the data does not support.

## Module: leaderboard.py

Default ranking key is the **lower bound** of the expectancy confidence
interval, descending, which prefers a modest well-evidenced edge over a large
unreliable one. Ties break on `win_rate`. The key is configurable
(`expectancy_r`, `ci_low`, `win_rate`, `profit_factor`, `total_return_pct`).

Default output is one table per timeframe plus an overall table pooling all
timeframes:

```
1m  —  200 trials, 50 symbols, net of 1.0 bps slippage

  #  pattern                trades   win%   exp R    95% CI        PF   consist  verdict
  1  bullish_engulfing         412   38.1   +0.07   [+0.01,+0.13]  1.21     0.58  EDGE
  2  morning_star               64   43.8   +0.12   [-0.04,+0.28]  1.33     0.55  NOISE
  ...
 19  random_long               390   36.4   -0.02   [-0.08,+0.04]  0.97     0.49  NOISE
 20  gravestone_doji            31   29.0   -0.21   [-0.44,-0.02]  0.58     0.31  NEGATIVE
```

Control rows stay in the table, in rank position, so the reader can see directly
where the noise floor sits.

`--verbose` adds gross versus net expectancy, exit mix, average bars held, and
max drawdown. `--json` and `--csv` write machine-readable output including the
full config and seed used, so any result can be reproduced.

## Module: config.py and config/backtest.toml

TOML parsed with the standard library `tomllib`, so no dependency is added.
Loading validates and produces frozen dataclasses; unknown keys are an error,
not a silent ignore, because a silently ignored typo in a threshold would
invalidate a run without any visible sign.

```toml
[run]
trials = 200
seed = 42
intervals = ["1m", "5m", "15m", "30m", "1h"]
cache_dir = ".cache/bars"
throttle_s = 0.3

[universe]
symbols = []          # empty uses the built-in top-50 list
sample_size = 50      # how many of those symbols trials may draw from

[trade]
stop_buffer = 0.001          # pad the stop past the pattern extreme
reward_multiple = 2.0        # target distance in units of risk
max_hold_bars = 20
min_risk_pct = 0.0005        # discard signals with a near-zero stop distance
allow_overlapping_trades = false
force_close_at_session_end = true

[costs]
slippage_bps = 1.0
commission_per_trade = 0.0

[thresholds]
doji_body = 0.10
small_body = 0.30
long_body = 0.60
shadow_dominance = 2.0
opposite_shadow_max = 0.25
doji_shadow_min = 0.60
near_equal = 0.001
gap_min = 0.0
trend_lookback = 10
trend_min_slope = 0.0

[stats]
min_trades = 30
min_trades_per_trial = 3
bootstrap_samples = 2000
rank_by = "ci_low"

[patterns]
# omit a name, or set it false, to disable it
hammer = true
inverted_hammer = true
hanging_man = true
shooting_star = true
dragonfly_doji = true
gravestone_doji = true
bullish_engulfing = true
bearish_engulfing = true
bullish_harami = true
bearish_harami = true
piercing_line = true
dark_cloud_cover = true
tweezer_bottom = true
tweezer_top = true
bullish_kicker = true
bearish_kicker = true
morning_star = true
evening_star = true
three_white_soldiers = true
three_black_crows = true
random_long = true
random_short = true
```

## Module: cli.py

```
python -m candlebench fetch   [--config PATH]        warm the Parquet cache
python -m candlebench run     [--config PATH] [--trials N] [--seed S]
                              [--intervals 1m,5m] [--patterns hammer,doji]
                              [-v] [--json OUT] [--csv OUT]
python -m candlebench patterns                       list registered patterns
```

Command-line flags override config values; the config file overrides built-in
defaults. The effective config is echoed in verbose and machine-readable output.

## Error handling

| Condition | Handling |
| --- | --- |
| Cache miss for a requested interval | fail fast, naming the `fetch` command |
| yfinance failure for one symbol during fetch | record in `CacheReport`, continue |
| Malformed bar (`high < low`, NaN, zero volume) | drop the row, count it, report the count |
| Zero-range bar | matches no pattern; ratios yield 0 rather than dividing by zero |
| Pattern enabled in config but not registered | fail fast listing valid names |
| Unknown config key or out-of-range value | fail fast naming the key |
| A pattern produces zero trades | present in the table as `INSUFFICIENT`, not omitted |
| Fewer than 2 trades for a deviation metric | report as unavailable, do not substitute 0 |

The distinction behind the last row is the one the existing `stock.py` already
makes with its `score = None` convention: unavailable is not the same as zero,
and conflating them silently biases the result.

## Testing

Detectors are tested against hand-built OHLC fixtures, not market data, because
a fixture states the intended shape unambiguously. Each of the 20 patterns gets
a known positive and a known negative, where the negative is a near miss that
fails exactly one condition (for example, a hammer shape in an uptrend, which
should register as a hanging man and not as a hammer).

Engine tests cover: entry at the next bar's open; a stop-only exit; a
target-only exit; a bar touching both, asserting the stop wins; a gap through
the stop filling at the open; the `max_hold_bars` timeout; session-end
force-close; short-side mirroring; and rejection of a signal whose risk is below
`min_risk_pct`.

A look-ahead test constructs two bar arrays identical up to bar `i` and
divergent afterwards, then asserts the mask value at `i` is the same in both.
This is the property that keeps the whole result meaningful, so it is tested
directly rather than assumed.

Metrics tests verify expectancy, profit factor, and max drawdown against
hand-computed values, and verify the bootstrap interval is reproducible under a
fixed seed.

An end-to-end test runs two trials on a small committed fixture with a fixed
seed and asserts the output is byte-identical across runs.

## Dependencies

The environment is `~/.venv/finance` (Python 3.12.3), which already has
`yfinance`, `pandas`, and `numpy`. To be added:

- `pyarrow` — Parquet cache
- `pytest` — tests

`tomllib` is in the standard library. `scipy` is deliberately avoided; the
bootstrap is a few lines of numpy.

## Interpretation caveats, recorded deliberately

These belong in the design because the tool's output invites over-reading, and a
result presented without them is misleading.

1. **The sample is shallow.** The yfinance caps mean roughly 30 to 60 days of
   intraday history. A pattern that worked across that window has been tested
   against one market regime, not across regimes.
2. **No bid-ask or depth modelling.** `slippage_bps` is a flat approximation.
   Real fills at 1-minute resolution on a fast move are worse.
3. **No short borrow cost or locate.** Short results are slightly optimistic.
4. **Survivorship.** The universe is today's liquid names, which excludes
   anything that collapsed out of the list.
5. **Multiple comparisons.** Testing 20 patterns across 5 timeframes is 100
   comparisons, so at a 5% threshold roughly five will look significant by
   chance alone. The matched random-entry controls and the reported intervals
   are the defence against reading those five as real.
