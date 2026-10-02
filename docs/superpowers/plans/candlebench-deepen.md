# candlebench: trade-level data, deeper statistics, richer UI, sub-minute bars

Spec: none written separately; this plan is the spec. Authority for conflicts is
the project's stated goal — measure whether classic candlestick patterns have a
real intraday edge, and never report a number the data does not support.

## Global Constraints

- Test command: `python3 -m pytest -q`. Baseline 254 passed.
- `pytest.ini_options` sets `filterwarnings = ["error::RuntimeWarning"]`, so any
  numpy empty-slice or divide-by-zero warning fails the suite. Guard every mean.
- Unavailable stays distinct from zero everywhere (project convention).
- No new runtime dependency. `pyarrow`, `pandas`, `numpy`, `yfinance` only.
- Web server: standard library only, loopback only, validate every input that
  reaches a filesystem path or the pattern registry.
- A full run yields ~43,000 trades (~9.5 MiB as JSON). Trades go to Parquet and
  are served on demand, never embedded in `leaderboard.payload`.

## Task 1: Merge and tidy

Produces: `master` containing the frontend and README work; no stale branch.

Steps:
1. `git checkout master && git merge --ff-only web-frontend`.
   Expected: fast-forward to `f7f1fee`.
2. `git branch -d candlestick-backtest`. Expected: deleted (merged at 157765d).
3. `git checkout -b deepen` so later tasks do not commit to master.
4. `python3 -m pytest -q`. Expected: 254 passed.

## Task 2: Persist trades

Produces: `Trade.window`, `Trade.entry_minute`; `RunResult.trades`;
`candlebench/trades.py` with `to_frame`, `write`, `read`, `query`, `COLUMNS`;
`bars.minutes_from_open`.
Consumes: `engine.Trade`, `runner.run`.

`Trade` gains two fields so later tasks can group without re-deriving:
- `window: int` — the walk-forward window its trial came from (0 when
  `run.windows` is 1). Task 4 fills it; here it defaults to 0.
- `entry_minute: int | None` — minutes from the session open at the entry bar.
  Bar index cannot stand in: validation drops bad bars, so index times interval
  is not clock time. `simulate` takes `bar_minutes: np.ndarray | None = None`
  and records `int(bar_minutes[entry_index])`, or None when not supplied.

`bars.minutes_from_open(frame) -> np.ndarray` — minutes since 09:30 Eastern per
bar, int64, from the tz-aware index.

`trades.py`:
- `COLUMNS` — the frame's column order: every `Trade` field plus `bars_held`.
- `to_frame(trades) -> DataFrame` — one row per trade. `session` as an ISO
  string, so Parquet round-trips without a dtype surprise and lexicographic
  order is chronological order.
- `write(trades, path)` / `read(path) -> DataFrame` — Parquet via pandas.
- `query(frame, pattern=None, interval=None, symbol=None, limit=None,
  offset=0) -> DataFrame` — filter then slice.

`runner.run` flattens `collected` onto `RunResult.trades` instead of discarding
it, and passes `bar_minutes` into `engine.simulate`. The runner writes nothing;
persistence belongs to callers.

Steps:
1. Write `tests/test_trades.py`: empty-list frame still has `COLUMNS`;
   round-trip through Parquet preserves every field and dtype; `query` filters
   by each key and respects `limit`/`offset`; `RunResult.trades` is non-empty
   and its length equals the summed `trades` of the per-interval stats;
   `entry_minute` is a multiple of the interval for a contiguous session.
2. Run them. Expected: failures — no `candlebench.trades`.
3. Implement `bars.minutes_from_open`, the `Trade` fields, `simulate`'s
   `bar_minutes`, `trades.py`, and `RunResult.trades`.
4. Run `pytest -q`. Expected: all pass, 254 + new.
5. Commit.

## Task 3: Serve trades

Produces: `GET /api/trades`; `JobRunner` writing `last_run_trades.parquet`;
`cli.py --json` writing a sibling `.parquet`.
Consumes: Task 2's `trades` module and `RunResult.trades`.

- `JobRunner.submit` work functions may now return `(payload, trades)`. Keep it
  simpler: `JobRunner` gains `trades_path`, and `submit` accepts an optional
  `trades` callable result. Chosen shape: `work` returns a `JobResult` dataclass
  with `payload: dict` and `trades: list[Trade] | None`.
- `GET /api/trades?pattern=&interval=&symbol=&limit=&offset=` returns
  `{"total": int, "limit": int, "offset": int, "trades": [...]}`.
  Validate `pattern` against `patterns.registry()` and `interval` against
  `bars.SUPPORTED_INTERVALS` before touching the frame; 400 on anything else.
  Cap `limit` at `MAX_PAGE = 500`, default 200. 404 when no trade file exists.
- `cli.py`: `--json out.json` also writes `out.parquet`.

Steps:
1. Tests in `tests/test_web.py`: a run writes the Parquet file; `/api/trades`
   returns rows; `?pattern=../../etc/passwd` is a 400 naming the pattern;
   `?interval=1s` is a 400; `limit=100000` is capped; `/api/trades` before any
   run is a 404; filtering by pattern returns only that pattern.
2. Run them. Expected: 404s and 200s wrong — endpoint absent.
3. Implement.
4. `pytest -q`. Expected: all pass.
5. Commit.

## Task 4: Estimated spread

Produces: `candlebench/costs.py` with `corwin_schultz` and `one_way_fraction`;
`CostConfig.model`; `RunResult.spread_bps`; spread line in `leaderboard.render`
and in `payload`.
Consumes: nothing from earlier tasks except `RunResult`.

Corwin-Schultz (2012) proportional effective spread, per adjacent bar pair:

```
beta  = ln(H_t/L_t)^2 + ln(H_t1/L_t1)^2
gamma = ln(max(H_t,H_t1)/min(L_t,L_t1))^2
k     = 3 - 2*sqrt(2)
alpha = (sqrt(2*beta) - sqrt(beta))/k - sqrt(gamma/k)
S     = 2*(exp(alpha) - 1)/(1 + exp(alpha))
```

Negative `S` is clamped to 0 per the paper's convention, then the session's
estimate is the mean of the clamped pair estimates. An estimate of exactly 0, or
a session with fewer than 2 usable bars, is unavailable — fall back to the fixed
model. `S` is a round-trip spread, so the one-way cost charged per leg is `S/2`,
matching what `slippage_bps` already meant.

- `CostConfig` gains `model: str = "estimated"`, validated against
  `{"estimated", "fixed"}` in `config.validate`.
- `costs.one_way_fraction(cost_cfg, high, low) -> tuple[float, float | None]`
  returns the fraction to charge and the estimated round-trip spread, or None
  when the fallback was used.
- `engine.simulate` gains `one_way_cost: float | None = None`; when None it
  keeps using `cost_cfg.slippage_bps / 10_000`, so every existing engine test is
  unchanged. The runner computes the fraction once per (interval, trial) and
  passes it to all 22 patterns, so one session cannot be priced two ways.
- `RunResult.spread_bps: dict[str, float | None]` — mean estimated round-trip
  spread in bps per interval, None when no session yielded an estimate.
  Rendered under the header and carried in `payload`.

Steps:
1. Write `tests/test_costs.py`: a hand-built two-bar case against a
   by-hand-computed `S`; a zero-range pair estimates 0 and is unavailable;
   a negative-alpha pair clamps to 0 rather than going negative; `model="fixed"`
   ignores the bars; `one_way_fraction` returns half the estimated spread;
   a one-bar array is unavailable. Plus a runner test: `spread_bps["1m"]` is
   positive on the synthetic cache, and estimated costs change expectancy
   relative to `model="fixed"`.
2. Run them. Expected: failures — no `candlebench.costs`.
3. Implement.
4. `pytest -q`. Expected: all pass.
5. `python3 -m candlebench run --json /tmp/estimated.json`
   against the real cache and compare 1m expectancy with `model="fixed"`.
   Expected: the 1m cost rises. If the headline conclusion flips, stop and
   investigate before accepting.
6. Commit.

## Task 5: Walk-forward windows

Produces: `RunConfig.windows`; `Trial.window`; `Trade.window` filled;
`PatternStats.window_expectancy_r` and `PatternStats.stability`.
Consumes: Task 2's `Trade.window`.

- `sampling.draw_trials` gains `windows: int`. It partitions the pool's distinct
  sessions chronologically into `windows` contiguous groups, draws
  `trials // windows` from each (the remainder going to the earliest windows so
  the count is exact), and tags each `Trial` with its window index. With
  `windows == 1` the draw must be byte-identical to today's, so existing
  reproducibility tests keep passing.
- `metrics.summarise` gains `window_expectancy_r: dict[int, float] | None` and
  `stability: float | None` — the share of qualifying windows whose own
  expectancy is positive, where a window qualifies at
  `stats_cfg.min_trades_per_trial` trades or more. With fewer than two
  qualifying windows, `stability` is None: one window cannot show stability.
- `config.validate`: `run.windows` at least 1, and no more than `run.trials`.
- `leaderboard`: a `stab` column in the verbose table, `window_expectancy_r`
  flattened in `write_csv` the way `exit_mix` already is, and an honest note
  when `windows > 1` that 28-59 days of history makes these adjacent weeks of
  one regime rather than independent regimes.

Steps:
1. Write `tests/test_windows.py`: `windows=1` reproduces today's trial list
   exactly; `windows=3` gives every trial a window in `range(3)` and splits the
   count evenly; windows are chronologically ordered and disjoint by session;
   `stability` is None at one window; a pattern positive in one window and
   negative in another gets `stability == 0.5`; `windows=0` and
   `windows > trials` are rejected.
2. Run them. Expected: failures.
3. Implement.
4. `pytest -q`. Expected: all pass.
5. Commit.

## Task 6: Breakdowns

Produces: `trades.breakdown`, `trades.equity_curve`, `trades.TIME_BUCKETS`;
`GET /api/breakdown`; `GET /api/equity`.
Consumes: Tasks 2, 3, 5.

- `trades.breakdown(frame, by, pattern=None, interval=None) -> list[dict]` for
  `by` in `{"symbol", "time_of_day", "window", "exit_reason"}`. Each row carries
  `key, trades, win_rate, expectancy_r, total_r` and an `exit_mix` dict, so the
  exit mix is available per breakdown and not only overall.
- `TIME_BUCKETS`: `open` is the first 30 minutes, `close` the last 30 of a
  390-minute session (`entry_minute >= 360`), `midday` the rest.
- `trades.equity_curve(frame, pattern, interval) -> dict` with `points` (the
  cumulative net R series) and `max_drawdown_r`. Ordering is
  `(session, entry_index, symbol)`, the same rule as `metrics._max_drawdown_r`,
  and a test asserts the two agree on the same trades — that assertion is what
  keeps the curve and the reported drawdown from disagreeing.
- `GET /api/breakdown?by=&pattern=&interval=` and
  `GET /api/equity?pattern=&interval=`, both validating `by`, `pattern` and
  `interval` and both 400 on anything else.

Steps:
1. Write `tests/test_breakdowns.py`: a hand-built frame breaks down by symbol
   with the expected per-symbol expectancy; time-of-day buckets land on the
   right side of each boundary minute (0, 29, 30, 359, 360, 389); an unknown
   `by` raises; `equity_curve`'s `max_drawdown_r` equals
   `metrics._max_drawdown_r` over the same trades; the curve is ordered
   chronologically regardless of the frame's row order. Plus web tests for both
   endpoints and their 400s.
2. Run them. Expected: failures.
3. Implement.
4. `pytest -q`. Expected: all pass.
5. Commit.

## Task 7: Equity curve and trade drill-down

Produces: equity-curve figure and a paged, sortable trade table in the browser.
Consumes: Task 6's `/api/equity`, Task 3's `/api/trades`.

Extend `app.js` in its existing hand-rolled-SVG style; `scale()` and `svg()` are
reused. Clicking a leaderboard row selects that pattern: the equity curve draws
it with its matched control overlaid, and the drill-down table pages through its
trades with sortable columns (session, symbol, direction, entry, exit, bars
held, exit reason, R). Page size is whatever the server allows, not what the
page asks for.

Steps:
1. Add `tests/test_web.py` coverage for the paging and sort parameters the page
   relies on (`offset`, `sort`, `desc`), including a 400 for an unknown sort
   key. Run them; expected: failures.
2. Implement the server side, then the page.
3. `pytest -q`. Expected: all pass.
4. Commit.

## Task 8: Session chart

Produces: `GET /api/session`; a candlestick figure with signals and levels.
Consumes: Tasks 3 and 6.

`GET /api/session?symbol=&session=&interval=&pattern=` returns the session's
OHLC, the pattern's signal mask from `patterns.detect`, and the entry, stop and
target of the trades the last run actually recorded there — the marks come from
the stored trades, so the chart cannot disagree with the leaderboard. Validate
`symbol` with `config.SYMBOL_PATTERN` (it reaches the cache path), `session` as
an ISO date, `interval` against `SUPPORTED_INTERVALS`, `pattern` against the
registry. Thresholds come from the last run's config when there is one.

Steps:
1. Tests: a valid request returns bars and a mask whose True count equals the
   leaderboard's signal count for that pattern, symbol and session;
   `symbol=../../../etc/passwd` is a 400; a session with no cached bars is a
   404. Run them; expected: failures.
2. Implement server, then the chart.
3. `pytest -q`. Expected: all pass.
4. Commit.

## Task 9: Save and compare runs

Produces: `candlebench/web/history.py`; `GET /api/runs`; a run picker and a
two-run diff in the page.
Consumes: Tasks 3 and 7.

Each completed run is saved as `<cache>/runs/<timestamp>.json` plus
`<timestamp>.parquet`, keeping the most recent `KEEP = 10`. `GET /api/runs`
lists them; `GET /api/runs?id=<id>` returns one payload; `/api/trades?run=<id>`
reads that run's Parquet. `id` is validated against a strict timestamp pattern
before it becomes a path segment. The page diffs two runs' expectancy per
pattern and interval.

Steps:
1. Tests: a run appends to the history; the eleventh save evicts the oldest;
   `?id=../../etc/passwd` is a 400; `?id=` of an unknown run is a 404;
   `/api/trades?run=<old>` returns that run's trades, not the latest. Run them;
   expected: failures.
2. Implement.
3. `pytest -q`. Expected: all pass.
4. Commit.

## Task 10: Surface the new statistics

Produces: `stability` and estimated-spread columns in the leaderboard, and the
breakdown views, in the browser.
Consumes: Tasks 4, 5, 6.

Steps:
1. Add the columns and a breakdown panel with a `by` selector.
2. `pytest -q`. Expected: all pass (page changes are not unit tested beyond the
   endpoints they call, which Tasks 3, 6, 8 and 9 already cover).
3. Commit.

## Task 11: Sub-minute timeframes

Gated. Requires a free Alpaca key in `ALPACA_API_KEY` / `ALPACA_SECRET_KEY`.
Neither is set in this environment, so the probe cannot run and nothing may be
built on an unverified assumption about which feed the free tier serves.

Steps:
1. Check the environment for both variables.
2. If absent: write no code, record the block in the ledger, and report it. The
   probe in the plan — compare an unspecified-feed historical trade count
   against that day's known consolidated volume — is the gate, and a gate that
   cannot run does not open.
3. If present: run the probe, then stop and report the finding before building,
   because IEX-only data would measure IEX microstructure rather than the
   market.

## Review Focus

- Every endpoint that takes a symbol, pattern, interval, sort key, `by` or run
  id: confirm the rejection path returns 400 and never 500, and never touches a
  path or a frame before validating.
- `filterwarnings = ["error::RuntimeWarning"]`: any mean, std or division over a
  possibly-empty selection is a latent suite failure. Breakdowns and window
  statistics are where empty groups appear.
- `windows == 1` must reproduce the previous sampling exactly; a changed draw
  would silently move every historical result.
- Corwin-Schultz on a zero-range bar, a single bar, and a bar pair with no
  overlap — all three must fall back rather than produce a nonsense cost.
- The equity curve and `max_drawdown_r` must not be able to disagree.
