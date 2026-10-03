# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Two independent tools in one repo

- `stock.py` at the root — scores one ticker for long-term, short-term, day and
  swing trading. Standalone script, no relation to the package below.
- `candlebench/` — measures whether candlestick patterns have a real intraday
  edge. This is where nearly all the code and all the tests live.

[HANDOFF.md](HANDOFF.md) carries the project's verified state, the three cost
models, and the pitfalls that have already caused real defects here. Read it
before changing anything in `candlebench/`.

## Commands

```bash
python3 -m pytest -q              # 476 tests; the venv at ~/.venvs/finance is on PATH
python -m candlebench fetch       # warm the bar cache for the configured source
python -m candlebench quotes      # sample NBBO quotes into the spread table
python -m candlebench run -v      # measure and rank; --json also writes trades to .parquet
python -m candlebench serve       # loopback dashboard on :8765
```

A full 200-trial run takes a few minutes and peaks around 1.1 GiB RSS on two
years of 1m data.

## Gotchas

- **`filterwarnings = ["error::RuntimeWarning"]`** in `pyproject.toml`. Any mean,
  std, sum or division over a possibly-empty or all-NaN selection fails the
  suite. Guard every one.
- **`ALPACA_API_KEY` / `ALPACA_SECRET_KEY` come from the environment only** —
  never from config, never from a browser request. A key in the TOML would be
  committed; one accepted from the page would be echoed into a saved report.
- **`.cache/` is gitignored and ~236M**: two years of 1m bars, plus the spread
  table. A fresh clone has none of it. Check a path exists before assuming it.
- **Circular imports between `bars`, `alpaca`, `ticks` and `quotes` are a live
  fragility.** `bars` registers the others as sources at its own import time, so
  those modules import `bars` inside functions rather than at module level. The
  breakage only appears when the other module is imported *first*, which is why
  a hook imports each edited module in a fresh process.
- Each bar source caches under its own directory (`Config.cache_path`), because
  Alpaca bars are split-adjusted and yfinance's are not. Never reuse one
  source's cache as another's.

## Conventions worth matching

- Module docstrings explain *why*, not what, and record the measurement behind a
  design decision. `costs.py` and `ticks.py` are the reference examples.
- Tests are named as sentences that state the rule being pinned —
  `test_a_negative_estimate_is_clamped_rather_than_credited` — and their
  docstrings say what breaks if the rule does not hold.
- Keep "unavailable" distinct from zero. A missing statistic is `None`, never 0.

## Git

Commit each logical change and push to `origin/master` without asking. Do not
force-push.
