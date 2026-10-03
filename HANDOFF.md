# Start here: candlebench handoff

Updated October 2, 2026, for a coding agent continuing this work.

`candlebench` asks whether classic candlestick patterns have a real intraday
edge. Its answer, measured over two years and 49,062 trades, is **no pattern
earns `EDGE` at any timeframe** — and the reason is costs, not absent signal.
Eight patterns beat random entry on signal alone; none beats it after the spread.

The project's one rule, which outranks everything below: **never report a number
the data does not support, and keep "unavailable" distinct from zero.** Most
defects found here have been violations of that rule rather than crashes.

## Read in this order

1. This file: state, environment, pitfalls, what I would do next.
2. [README.md](README.md): full usage, the cost model, what it found, caveats.
3. [docs/hft-data-and-costs.md](docs/hft-data-and-costs.md): data and cost
   measurements written for `~/git/hft`, which is considering reusing this.
4. [docs/superpowers/plans/candlebench-deepen.md](docs/superpowers/plans/candlebench-deepen.md):
   the plan this session executed, with outcomes recorded against each task.
5. [docs/superpowers/specs/2026-10-01-candlestick-backtest-design.md](docs/superpowers/specs/2026-10-01-candlestick-backtest-design.md):
   original design. Historical; several decisions in it have since been revised.

## Verified state

Branch `master` at `1495aa4`, pushed to `origin`. `deepen` is the same work and
can be deleted. **476 tests pass** under Python 3.12.3, pandas 3.0.6, numpy
2.5.3, pyarrow 25.0.1.

```bash
python3 -m pytest -q     # the venv at ~/.venvs/finance is already on PATH
```

Verified by hand this session, not only by test:

- 29 malformed HTTP requests return 400 or 404 with **zero** tracebacks, covering
  every parameter: pattern, interval, symbol, sort key, breakdown key, session
  date, run id, limit, offset, source, lookback.
- `windows = 1` draws **byte-identical** trials to the pre-change sampler across
  nine seed/trial combinations, diffed against `git show 639b672:`. No historical
  result moved.
- The equity curve's drawdown equals the leaderboard's `max_drawdown_r` to the
  last digit; `engine.CHRONOLOGICAL` is the single ordering both use.
- A resampled 1s bar's OHLC equals the first/max/min/last **eligible** print in
  that second, and its volume the sum of **all** print sizes, against live API data.
- The Corwin-Schultz estimator raises nothing across 9,348 real symbol-sessions
  under `np.seterr(all="raise")`.

## Environment and credentials

`ALPACA_API_KEY` and `ALPACA_SECRET_KEY` are in the user's shell profile and are
read from the environment only — never from config, never from the browser. They
are not in the tree or in any commit (verified with `git log -p --all`). Do not
print them, copy them into docs, or accept them from a request.

A hook forbids edits outside `~/git/stock-analyzer`, `~/.claude` and `/tmp`, and
forbids force-pushing master. Writing a literal `API_KEY=` or `token=` in a shell
command trips the secret scanner; assemble such strings in a Python heredoc
instead.

`.cache/bars` is 236M and gitignored: two years of 1m bars for 50 symbols from
Alpaca, plus the yfinance cache and the observed spread table. A fresh clone has
none of it. Check before assuming a path exists.

## What the three cost models mean

This is the most important thing to understand before changing anything, because
the headline finding rests on it.

| `[costs] model` | Source | Measured level |
| --- | --- | --- |
| `quoted` | Observed NBBO quotes, per symbol and time-of-day bucket | 1.83 bps/leg |
| `estimated` (default) | Inferred from high-low ranges, Corwin-Schultz | 1.28 bps/leg |
| `fixed` | A flat `slippage_bps` | 1.0 bps/leg |

`quoted` is the best evidenced. It is not the default only because it needs a key
and `python -m candlebench quotes` to build a table; without one it falls back to
the estimator and **says so** rather than calling an estimate observed.

The estimator reads **low**, so a conclusion drawn from it understates costs.
Charging the observed spread moves mean 1m expectancy −0.074R — the finding gets
*stronger*, not weaker.

## Pitfalls that have already caused real defects

Each of these was a bug in this codebase, found and fixed. They are the shape of
mistake this code invites.

- **A local name shadowing a parameter.** `simulate` had `window = spec.bars_required`
  shadowing the new `window` argument, so every trade recorded its pattern's bar
  count as its walk-forward window and every window statistic grouped by shape.
- **A statistic on a floor too low to carry it.** `stability` qualified a window at
  3 trades, so four thin windows could read `stab 100%` on twelve trades. It now
  answers to `min_trades`, because taking the *sign* of a mean is the same kind of
  claim the verdict makes.
- **An estimator applied where its assumptions fail.** Corwin-Schultz climbs with
  bar length (1.8 bps at 1m to 15.1 at 1h on the same days) and collapses toward
  zero when bars have no range (0.074 at 1s against 2.028 at 1m). It is therefore
  computed only from the narrowest **non-sub-minute** interval, and capped at
  `MAX_PLAUSIBLE_SPREAD`, since the function asymptotes to 200% of price.
- **A chart computed with different parameters than the run.** `/api/session`
  detected signals with the server's startup thresholds while thresholds are
  browser-editable, so a run with an edited threshold outlined bars the
  leaderboard never counted. It now reads them from the run via `_measured_as`.
- **A non-atomic write.** `to_parquet` truncates its target on open, so an
  interrupted write destroyed the previous run's trades. Both the trade file and
  the run history now write to a temp file and rename.
- **Generalising from one symbol.** I measured AAPL's quoted spread, concluded the
  estimator overcharged by 2.7x, and reported the project's finding was overstated
  by half. Across all 50 symbols the opposite is true. AAPL is the most liquid
  name in the universe.
- **Documentation asserting a limit that became a choice.** The regime note, the
  caveats and the config comments all hardcoded "28 days of history" after a
  source with years of it existed. A report that misdescribes its evidence is
  wrong in the same way as one that overstates it.

`pytest.ini_options` sets `filterwarnings = ["error::RuntimeWarning"]`. Any mean,
std or division over a possibly-empty selection is a latent suite failure; guard
every one.

## Code map

| Module | Responsibility / what to know |
| --- | --- |
| `bars.py` | The only module that knows where bars come from. `SOURCES` registers yfinance and Alpaca with their intervals, chunking, caps and end lag. |
| `alpaca.py` | SIP bars to 2016, split-adjusted. `SIP_DELAY` ends windows 16 minutes short of now, because the API refuses the whole request rather than the restricted rows. `Throttled`/`with_retry` survive 429s. |
| `ticks.py` | Sub-minute bars from raw prints. Price from condition-eligible prints, volume from all of them — they follow different rules, and filtering both came out 21% light. |
| `quotes.py` | Observed NBBO half-spreads, sampled per symbol and time-of-day bucket. The open runs a median 4.0x midday. |
| `costs.py` | The credential-free fallback estimator, and why it is not preferred. |
| `engine.py` | Signal to closed trade. `one_way_cost` may be per-bar; each leg is charged at the bar it filled on. `CHRONOLOGICAL` is the shared ordering. |
| `runner.py` | Orchestration. `_estimate_spreads` prices one session once for all 22 patterns. Reports `flat_bar_share` and warns above 5%. |
| `metrics.py` | `PatternStats`. `consistency` counts trials, `stability` counts walk-forward windows — different questions, different floors. |
| `trades.py` | Per-trade Parquet, paging, breakdowns, equity curves. `check_dtype_coverage` runs at import. |
| `web/server.py` | Loopback only. Every parameter validated before it reaches a path or a frame. |
| `web/history.py` | Last ten runs, for two-run comparison. Ids are monotonic and validated before becoming filenames. |

## What I would do next, in order

1. **Sweep the trade-management parameters.** The entire conclusion is conditional
   on one scheme: `reward_multiple = 2.0`, `max_hold_bars = 20`, stop at the
   pattern extreme. A pattern with no edge at 2R could have one at 1R. The
   run-comparison UI exists to make this readable and the sweep was never run.
   This is the largest untested assumption remaining.
2. **Check whether `stop_buffer` dominates risk at fine intervals.** A 1s bar's
   range is 1–3 bps; the buffer is 10 bps. So `risk_per_share` — the denominator
   of every R multiple — is mostly a config constant rather than the pattern's
   geometry. Sub-minute R multiples may be measuring the buffer. Untested.
3. **Add frontend tests.** About 1,100 lines of `app.js` have none. A fresh
   reviewer dismissed a real observation with "tests pass, so it's intentional" —
   there are no tests for that file, so the reasoning was void.
4. **Consider making `quoted` the default** once a table ships or is built on
   first use. It is the best-evidenced model and currently opt-in.

Do not expect pattern features to find an edge. Two years, 49,062 trades, five
timeframes, three cost models, six walk-forward windows: nothing. The value of
further work is in tightening what "nothing" means, not in finding something.

## Commands

```bash
python3 -m pytest -q                         # 476 tests
python -m candlebench fetch                  # warm the bar cache for [run] source
python -m candlebench quotes --sessions 4     # build the observed spread table
python -m candlebench run -v --json out.json  # also writes out.parquet of trades
python -m candlebench serve                   # loopback dashboard on :8765
```

A full 200-trial run over five timeframes takes a few minutes and peaks around
1.1 GiB RSS on two years of 1m data.

## What not to do

- Do not push to `origin` without being asked, and do not force-push `master`.
- Do not relabel or reuse a cache written by one source as another's. Each source
  caches under its own directory because the two disagree on prices by design.
- Do not forward-fill a sub-minute bar. An interval nobody traded in has no bar;
  carrying the close forward invents a doji at a price nobody traded, and doji is
  one of the patterns being detected.
- Do not call a figure measured when it fell back. Every cost path reports its own
  provenance and the tests pin it.
