# stock.py

A companion script, separate from candlebench: it shares no code and is not
part of the installed package. Run it from a clone with `python stock.py AAPL`.

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
penalised for Yahoo's gaps. Ratings require at least three evaluated metrics and 60% coverage. The percentage
is an indicator score, not a measured win probability. Both concise and verbose
output show evaluated/total metrics; insufficient coverage is `INCONCLUSIVE`.

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
