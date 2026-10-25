# Architecture

candlebench answers one question: does a candlestick pattern beat random entry,
after costs, on dates it was not chosen on? Everything below serves that
measurement. `stock.py` at the repository root is a separate ticker scorer and
shares no code with the package.

## Data flow

```
bars (alpaca | yfinance | synthetic | ticks)
  -> .cache/bars/<source>/<interval>/<SYMBOL>.parquet      bars.py
  -> sampled (symbol, session) trials, discovery / holdout   sampling.py
  -> per-session geometry and prior trend                    patterns/context.py
  -> pattern masks (registry applies history + trend gates)  patterns/
  -> trades and stop-matched random controls                 engine.py (+ _enginecore.cpp)
  -> per-pattern statistics, bootstrap, Holm, verdicts       metrics.py
  -> RunResult                                               runner.py
  -> terminal table, JSON, Parquet trades                    leaderboard.py, trades.py
  -> HTML report, static dashboard, live dashboard           report.py, site.py, web/
```

## Modules

| Module | Owns |
| --- | --- |
| `config.py` | The TOML schema, defaults and validation. Every user value is checked here. |
| `universe.py` | The built-in 117-symbol list, fixed so a seed reproduces a run. |
| `bars.py` | Bar sources, download, validation, atomic cache writes, session grouping. `alpaca.py`, `ticks.py` and `synthetic.py` register themselves as sources. |
| `quotes.py`, `costs.py` | Observed NBBO half-spreads per symbol and time of day, the bar-based spread estimator, and the fixed fallback. |
| `sampling.py` | Draws trials from what the cache holds, reserves the chronological holdout, honours `run.end_date`. |
| `patterns/` | The detector registry: 66 patterns in `single`, `double`, `triple`, `multi`, plus the random controls. Detectors return boolean masks; history and trend gates are applied centrally. |
| `engine.py` | Signal to closed trade: entry, stop at the pattern's extreme, target, timeout, costs, and the stop-matched controls. `_enginecore.cpp` is an optional C++ walk held bit-identical to the Python one by `tests/test_engine_golden.py`. |
| `metrics.py` | Aggregation, date-clustered bootstrap, Holm correction with a declared experiment count, minimum detectable effects, verdicts. |
| `runner.py` | Orchestrates one run: discovery, selection, holdout, warnings, provenance. |
| `walkforward.py` | Repeats the run per fold and tests the pooled out-of-sample trades once. |
| `signals.py` | Loads a user's own detector file and tests it with the same pipeline (`candlebench test`). |
| `trades.py` | The trade frame: pinned dtypes, Parquet I/O, queries, equity curves, breakdowns. |
| `leaderboard.py`, `report.py`, `site.py` | Terminal and JSON output, the self-contained HTML report, the static dashboard export. |
| `web/` | The local dashboard server, background jobs and run history. `server.py` validates every request; `--read-only` refuses work. |
| `provenance.py` | Records commit, packages, config hash and data hash with every run. |

## Invariants

- **No look-ahead.** A detector sees only bars up to its signal bar; the trend
  gate reads the bar before the pattern starts. Matched controls enter 1-5
  bars *after* their pattern, never before.
- **Unavailable is not zero.** A statistic that cannot be computed is `None`
  and renders as `n/a`.
- **Holdout is untouched until discovery has chosen.** Only discovery
  candidates are evaluated on it.
- **The static site answers every query exactly as the server does**, enforced
  by `tests/browser/test_static_site.py`.
- **No prices from licensed data are published.** Reports and sites carry R
  multiples and dates only unless the source is synthetic.
- **Credentials come from the environment only**, never from config or a
  browser request.

## Technology choices

- **Python, NumPy, pandas, PyArrow**: vectorised detectors and Parquet caches
  keep a million-trade run to minutes on one core.
- **Optional C++ (pybind11)** for the trade walk, built with flags that forbid
  fused multiply-add so results stay bit-identical to Python.
- **No web framework**: `http.server` plus one vanilla JS file, so the same
  page runs against the live server or static JSON files on GitHub Pages.
- **GitHub Actions**: tests on Python 3.11 and 3.12, Chromium tests, Pylint,
  wheel and container builds (`ci.yml`), the published site (`pages.yml`), and
  tagged releases (`release.yml`).

## Lifecycle of a published result

1. `candlebench fetch` and `candlebench quotes` fill the cache.
2. `candlebench run --config X --json out.json` from a clean commit.
3. Price-free trades go to the `data-2026-10-08` GitHub release; the JSON to
   `docs/results/`; `candlebench report` writes `docs/reports/`.
4. `pages.yml` downloads the trades, rebuilds the dashboards and deploys.

Experiment write-ups that justify each design decision live in
`docs/experiments/`. `DESIGN.md` covers the dashboard's visual design.
