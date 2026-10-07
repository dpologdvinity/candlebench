# Changelog

## 0.2.0 — 2026-10-07

### Method

- Patterns are compared with their random-entry controls on gross R. In net R
  a wider stop pays fewer R for the same spread, so wide-stop patterns beat
  random entry without any signal. On a synthetic random walk this cut rows
  falsely beating their control from 38 of 800 to 3. Expectancy is still tested
  net. Re-measured on two years of 1m data, no pattern beats its control after
  correction, which reverses the earlier "signal destroyed by costs" reading.
- A run warns when bootstrap resolution makes the corrected threshold
  unreachable (for example 252 hypotheses × 2 experiments at 10,000 draws).
- A bar that opens beyond the target exits at the target, at the open, even if
  it later touches the stop.
- The reported cost is the average paid per executed leg, not an average over
  every bar. Quote buckets borrowed from a symbol's other buckets are reported
  separately from observed ones.
- Percentage returns pay commission, as net R does.
- A losing control is reported as cost drag on random entry, not as proof that
  costs exceed every possible pattern edge.

### Features

- `candlebench demo`: synthetic random-walk market, full run and dashboard,
  with no network access or API key.
- `scripts/null_calibration.py` and the null-calibration report.
- Run provenance: git commit, package versions, config hash and data content
  hash in every report and on the dashboard.

### Reliability

- Config and browser input are checked for type, finiteness, range and
  duplicates. Malformed requests return 400 and start nothing. The browser can
  no longer choose the quote-table path.
- A run is published only after its report, trades and archive copy are all
  saved. A failed save keeps the previous run and frees the job runner.
- A partly failed cache refresh keeps the existing history, cache writes are
  atomic, and bars with nonfinite or nonpositive values are rejected.
- Quote sampling honours the provider's pacing and backoff, and `--sessions`
  must be at least 1.
- The dashboard shows the last run without waiting for the cache scan, the
  cost headline matches the cost line, and the control's equity curve is scaled
  to the pattern's trade count.

### stock.py

- Negative P/E, P/B, debt-to-equity and payout ratios no longer score as cheap
  or safe. RSI is 50 for a flat window and unavailable on short or nonfinite
  history. A missing earnings date is unavailable rather than a failed catalyst.

### Project

- CI runs core tests on Python 3.11 and 3.12, Chromium dashboard tests,
  Pylint, and a clean wheel install. Package metadata includes the readme,
  license and project URLs.
- The README leads with the current result; the full reference is in
  `docs/guide.md`.
