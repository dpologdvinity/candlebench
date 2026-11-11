# AGENTS.md

Instructions for coding agents working in this repository. `CLAUDE.md` imports
this file, so Claude Code and Codex read the same text.

## Two independent tools in one repo

- `stock.py` at the root — scores one ticker for long-term, short-term, day and
  swing trading. Standalone script, no relation to the package below.
- `candlebench/` — measures whether candlestick patterns have a real intraday
  edge. This is where nearly all the code and all the tests live.

[HANDOFF.md](HANDOFF.md) carries the three cost models, a code map, and the
pitfalls that have already caused real defects here. Read it before changing
anything in `candlebench/`.

## Setup

```bash
uv venv && source .venv/bin/activate
uv pip install -e '.[dev]'        # pybind11 builds the optional C++ trade walk
uv pip install -e '.[browser]' && python -m playwright install chromium   # browser tests only
```

Everything runs offline except `fetch`, `quotes` and runs against the `alpaca`
source, which need `ALPACA_API_KEY` and `ALPACA_SECRET_KEY` (see Gotchas).

## Checks

CI runs the same three. Run them before calling work done.

```bash
python -m pytest -q -m "not browser"           # ~1,470 tests, about 2.5 minutes
python -m pytest -q tests/browser              # 32 Chromium tests: dashboard and static-site parity
pylint --fail-under=9.0 candlebench stock.py
```

## Commands

```bash
python -m candlebench demo                     # offline synthetic run + dashboard, no key
python -m candlebench fetch                    # warm the bar cache for the configured source
python -m candlebench quotes                   # sample NBBO quotes into the spread table
python -m candlebench run -v --json X.json     # measure and rank; also writes X.parquet of trades
python -m candlebench test examples/breakout_20.py --synthetic   # measure a user signal file
python -m candlebench report X.json            # self-contained HTML report
python -m candlebench site X.json --out DIR    # static interactive dashboard (no server)
python -m candlebench serve                    # loopback dashboard on :8765
python scripts/detection_power.py run --out CSV   # power study; long
```

Delete scratch output (`tmp/`, `/tmp/power-*`) when done.

## Gotchas

- **`filterwarnings = ["error::RuntimeWarning"]`** in `pyproject.toml`. Any mean,
  std, sum or division over a possibly-empty or all-NaN selection fails the
  suite. Guard every one.
- **`ALPACA_API_KEY` / `ALPACA_SECRET_KEY` come from the environment only** —
  never from config, never from a browser request. A key in the TOML would be
  committed; one accepted from the page would be echoed into a saved report.
- **`.cache/` is gitignored and ~700M**, almost all of it two years of 1m bars
  plus the spread table. A fresh clone has none of it. Check a path exists
  before assuming it.
- **Circular imports between `bars`, `alpaca`, `ticks` and `quotes` are a live
  fragility.** `bars` registers the others as sources at its own import time, so
  those modules import `bars` inside functions rather than at module level. The
  breakage only appears when the other module is imported *first*, which is why
  a hook imports each edited module in a fresh process (Claude Code only;
  otherwise run `python -c 'import candlebench.<mod>'`).
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
- Publish no prices from licensed data. Reports and `candlebench site` show R
  multiples and dates only, unless the source is `synthetic`.
- The static site must answer every dashboard query exactly as the server does;
  `tests/browser/test_static_site.py` enforces it. Change both together.

## Git

- Conventional Commits (`feat:`, `fix:`, `docs:`, `test:`, `refactor:`,
  `chore:`, `ci:`, `perf:`), small and atomic. Release steps are in
  [CONTRIBUTING.md](CONTRIBUTING.md).
- Commit each logical change and push to `origin/master` without asking. Never
  force-push.
- No AI attribution lines in commits, and no mention of coding agents in code
  or product docs. The agent files listed below are the only exception.

## Agent workflow

- `.agents/skills/` — project skills, written once for every agent. Codex reads
  this directory; Claude Code reads it through the relative symlinks in
  `.claude/skills/`:
  - `verify` runs the core suite plus a live sweep of 38 malformed requests
    against the server, which must all be refused with a 400 or 404 and no
    traceback. Use it before claiming work is done.
  - `costs` rebuilds the observed spread table and compares the three cost
    models. Manual only (`disable-model-invocation`), because it needs a key
    and makes ~1,800 API requests.
- `.claude/settings.json` — a `PostToolUse` hook that imports every edited
  `candlebench/*.py` in a fresh process and blocks on failure, which catches
  the circular-import breakage above as soon as it happens.
- `HANDOFF.md` — state carried between sessions: what was verified, what is
  open, and the mistakes not to repeat.
- `lefthook.yml` — pre-commit runs gitleaks and typos on staged files. Run
  `lefthook install` once after cloning.
