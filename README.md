# stock-analyzer

Two tools that answer different questions.

- **`stock.py`** scores a single ticker's fit for long-term, short-term, day and
  swing trading from current fundamentals and technicals.
- **`candlebench`** measures which classic candlestick patterns actually have an
  intraday edge, and which do not.

## stock.py

```bash
python stock.py AAPL        # concise verdict for each trading style
python stock.py AAPL -v     # full per-metric reasoning
python stock.py AAPL -i     # raw indicator readings, no verdicts
```

## candlebench

Twenty classic candlestick patterns are run through a mechanical risk-managed
trade on randomly sampled stocks and sessions, then ranked by measured
expectancy.

```bash
python -m candlebench fetch      # warm the bar cache (slow, once)
python -m candlebench run        # measure and rank
python -m candlebench run -v     # add gross vs net, exit mix, drawdown
python -m candlebench patterns   # list what is registered
```

Everything is configured in `config/backtest.toml`: which patterns run, which
timeframes, which symbols, the trade parameters, the costs, the detector
thresholds, and the trial count. Command-line flags override the file.

### What it actually measures

Given a pattern signal, the engine enters at the **next** bar's open, places a
stop past the pattern's extreme, targets `reward_multiple` times that risk, and
force-closes at `max_hold_bars` or at the session's end. Where bar data is
ambiguous the pessimistic reading wins: a bar touching both stop and target is
recorded as a stop, and gaps fill at the open rather than the level.

The headline metric is **expectancy in R**, not win rate. A pattern winning 70%
at 1:1 and one winning 35% at 3:1 have the same expectancy, and a high win rate
with poor expectancy is the usual way a pattern looks good and loses money.

### Reading the leaderboard

```
   #  pattern                trades   win%   exp R          95% CI     PF  consist  verdict
   1  inverted_hammer            58   51.7   +0.32   [-0.06,+0.68]   1.59     55.6  NOISE
   2  random_long *             149   44.3   +0.12   [-0.09,+0.34]   1.21     53.3  NOISE
   3  tweezer_bottom            120   41.7   +0.04   [-0.18,+0.29]   1.08     40.0  NOISE
```

`random_long` and `random_short`, marked `*`, enter at random bars and are the
**noise floor**. They run through the identical engine and ranking path as every
real pattern. A pattern ranked below its control has shown nothing.

| Verdict | Meaning |
| --- | --- |
| `EDGE` | the confidence interval excludes zero **and** it beats its control |
| `NOISE` | indistinguishable from chance |
| `NEGATIVE` | reliably loses |
| `INSUFFICIENT` | under `min_trades` trades |

Expect most rows to read `NOISE`. Twenty patterns across five timeframes is a
hundred comparisons, so about five will look significant by chance alone; the
controls and the intervals are the defence against believing those five.

Ranking is by the **lower bound** of the interval, which prefers a modest
well-evidenced edge over a large unreliable one.

### Timeframes

Sub-minute timeframes are not supported. yfinance exposes no interval below
`1m`, and sub-minute bars can only be built by resampling raw trade ticks from a
credentialed provider. Intraday lookback is also capped, which bounds what "a
random historical day" can mean:

| Interval | Usable history |
| --- | --- |
| `1m` | last 28 days, fetched in 7-day chunks |
| `2m` `5m` `15m` `30m` `1h` | last 59 days |

These sit just inside Yahoo's documented 30 and 60 days on purpose. Yahoo
compares each request against its own clock, and a full warm-up runs for
minutes, so a window built at exactly the limit is past it by the time a later
request lands. The 1m figure is also empirical: a 7-day chunk ending 21 days
ago returns bars, one ending 28 days ago returns nothing.

Adding a tick provider would touch only `candlebench/bars.py`.

### Caveats worth keeping in mind

The sample spans one market regime, not several. Slippage is a flat
approximation rather than a modelled book. Short results ignore borrow cost. The
universe is today's liquid names, so anything that collapsed out of it is
missing.

## Setup

```bash
pip install -e ".[dev]"
pytest
```

Requires Python 3.11+.
