# Contributing

## Setup

```bash
git clone https://github.com/dpologdvinity/candlebench
cd candlebench
uv venv && source .venv/bin/activate
uv pip install -e '.[dev]'               # pybind11 builds the optional C++ walk
uv pip install -e '.[browser]' && python -m playwright install chromium   # browser tests
python -m candlebench demo               # offline end-to-end check, no key needed
```

Real market data needs a free Alpaca key in `ALPACA_API_KEY` and
`ALPACA_SECRET_KEY`. Never put keys in a config file.

## Checks

Run all three before pushing; CI runs the same:

```bash
python -m pytest -q -m "not browser"     # core suite
python -m pytest -q tests/browser        # Chromium dashboard and static-site parity
pylint --fail-under=9.0 candlebench stock.py
```

The suite treats `RuntimeWarning` as an error, so guard every mean or division
over a selection that could be empty.

## Branches and commits

- Work on a branch, or commit small logical changes directly to `master`.
  Never force-push.
- Conventional Commits: `feat:`, `fix:`, `docs:`, `test:`, `refactor:`,
  `chore:`, `ci:`, `perf:`, with a scope where it helps (`fix(engine): ...`).
- Releases: bump `pyproject.toml` and `candlebench/__init__.py`, date the
  `CHANGELOG.md` section, commit `chore(release): X.Y.Z`, tag `vX.Y.Z`.

## Conventions

- A bug fix starts with a failing test that reproduces it.
- Tests are named as sentences stating the rule they pin, and their docstrings
  say what breaks if it does not hold.
- Module docstrings explain why a design was chosen and the measurement behind
  it.
- A missing statistic is `None`, never 0.
- A new pattern needs a hit and a near miss in `tests/test_patterns.py`, and an
  entry in `config/backtest.toml`.
- Changing the dashboard means changing the server and the static adapter in
  `app.js` together.
- Heavy runs share a machine: use `nice -n 19` and few workers, and delete
  scratch output when done.

See `ARCHITECTURE.md` for how the pieces fit and `docs/guide.md` for usage.
