# Changelog

## Unreleased

- 46 more patterns, 66 in all. Twenty in ten mirrored pairs: marubozu, belt
  hold, harami cross, counterattack, homing pigeon and descending hawk, three
  inside, three outside, doji stars, abandoned babies, and rising and falling
  three methods. Twenty-six more from the standard catalogue: separating
  lines, on neck, in neck, thrusting, matching low, two crows, upside gap two
  crows, identical three crows, stick sandwich, unique three river, tasuki
  gaps, gap three methods, advance block, tri-stars, hikkake, ladder bottom,
  mat hold, breakaways and three-line strikes (new module
  `candlebench/patterns/multi.py` for four- and five-bar patterns). Each has a
  hit and a near miss in tests/test_patterns.py. New threshold `marubozu_body`.
- The built-in universe has 100 liquid symbols (sector, bond, gold and
  emerging-market ETFs, and 34 more large stocks); the default
  `sample_size` is 100. The first 50 are the original list in its original
  order, so `sample_size = 50` reproduces earlier runs.
- The default Holm family grows from 252 to 804 hypotheses, so the default
  bootstrap rises from 10,000 to 20,000 draws (12,000 for the demo) to keep
  corrected p-values able to reach 0.05.

## 0.5.2 — 2026-10-08

- The repository and site are now named candlebench
  (https://dpologdvinity.github.io/candlebench/).
- A failed trade download on the static site is reported instead of shown as
  no trades; only a pattern the run never measured has no trade file.
- A clustered synthetic session outside 2020-2039 is refused rather than given
  the wrong volatility, and a matched-control window must start at least one
  bar after the pattern.

## 0.5.1 — 2026-10-08

- Republished the two-year 1m results at 2,500 sampled symbol-days instead of
  200 (240,627 pattern trades). No pattern beats its matched controls, all 20
  have net intervals below zero, and the frequent patterns' detection limits
  tighten from 0.07-0.14R to 0.02-0.04R. Two experiments are declared, since
  the 200-trial run came first.
- The static site writes trades one file per pattern (`data/trades/<name>.json`)
  instead of one file for the run, which at 2,500 trials would have been 57 MB.
  A query across patterns restores the server's stored order; the parity test
  now covers one.

## 0.5.0 — 2026-10-08

- Measured how late matched controls should enter
  (docs/experiments/matched-window.md, `scripts/matched_window.py`). Later
  windows recover more of a slow edge but miss most one-bar edges and raise the
  detection limit, so the default stays at one to five bars. The window is now
  `engine.MATCH_ENTRY_BARS`, a (first, last) pair.
- Published a second real-data run at 5m, 15m and 1h over the same two years
  (docs/results/two-year-5m-15m-1h.json, its report and an interactive
  dashboard at app/intervals). No pattern beats its matched controls at any
  timeframe. It samples 2,500 symbol-days per timeframe and declares two
  experiments, because a 200-trial run of the same design came first and was
  too thin to say anything at 1h.
- The synthetic market can cluster volatility (`clustered=True`): persistent
  daily levels, partly shared across symbols, and persistent within a session.
  Off by default. `null_calibration.py` and `detection_power.py` take
  `--clustered`, and `detection_power.py` takes `--pattern` and `--drifts`.
- Robustness study (docs/experiments/robustness.md): on clustered markets, 4 of
  80 noise-only markets had any false positive (5.0%, the corrected test's
  bound) and power matched the plain market; an edge planted in the rare
  `morning_star` was detected at the rate its reported limit predicts.

## 0.4.3 — 2026-10-08

- On a phone, run settings sit two per row and each unit stays beside its
  input. Read-only and static dashboards fold the settings into "Settings
  this run used", so the results come first instead of two screens down.
- The README screenshot shows the current leaderboard, with matched-control
  deltas, detection limits and verdicts.

## 0.4.2 — 2026-10-07

- Fixed: the equity chart's matched-control line was empty whenever a sample
  was selected, which the dashboard always does. Narrowing trades to one
  sample dropped the stored controls on both the server and the static site.
  Tests now require a non-empty comparison curve.

## 0.4.1 — 2026-10-07

- The equity chart draws each pattern against its own stop-matched controls,
  the comparison the statistics use. Runs now store those controls beside
  their trades. Runs saved earlier fall back to the reference row, labelled
  as such.
- A slow response for an earlier selection can no longer overwrite a later
  one in any drill-down panel.
- Sort headers and leaderboard rows are buttons: reachable by Tab, operable
  with Enter or Space, and announced with their sort and selection state.
- The published two-year results were re-run so their dashboard shows the
  matched controls.

## 0.4.0 — 2026-10-07

### Method

- Every row reports a minimum detectable effect: the smallest advantage over
  its matched controls, and the smallest net expectancy, that its corrected
  test would detect 80% of the time. It is computed from the row's own
  bootstrap spread and the actual family size, and is None when the row cannot
  be tested.
- Detection power was measured by planting edges of known size in independent
  synthetic markets, using the real-data design. The reported limit (0.10R) is
  accurate to slightly conservative. Profitability needs a gross edge near
  0.29R, and the 20% holdout confirms only large edges.
- Pooled equity curves and drawdowns follow clock time rather than bar number.

### Features

- `candlebench site` exports one run as an interactive dashboard made of static
  files. GitHub Pages now hosts it for the two-year results and for a synthetic
  demo rebuilt on every push. A browser test requires its answers to match the
  live server's exactly.
- `candlebench.synthetic.Planted` plants a known edge after a pattern's
  signals; `scripts/detection_power.py` runs the resumable power sweep.

### Fixes

- Dashboard: "toggle all" no longer double-submits patterns. Sorting survives
  tab changes. A new run starts at page one. A failed trade page clears stale
  rows. The pooled session chart uses the finest interval, and a control row
  no longer overlays itself.
- API: `desc` must be 1 or 0. Symbol and run ids reject trailing newlines and
  non-ASCII digits. Unexpected GET errors return JSON 500. Sessions the run
  skipped show no signals. Run history counts each edge pattern once.
- `candlebench report` accepts trade files published without price columns.

## 0.3.0 — 2026-10-07

### Method

- Each pattern is compared with its own matched controls instead of the shared
  random-entry rows. Every pattern trade gets one random-entry trade with the
  same direction and the same stop distance from the last close, entering one to
  five bars after the pattern and exiting through the same code. On the
  synthetic random walk, rows falsely beating their control fall from 3 of 800
  to 1, in one run of ten, as a 5% familywise rate predicts.
- Two matched-control designs were rejected by that calibration and are
  recorded in it. Controls entering before the pattern trade through bars the
  pattern was selected on, and lost up to 0.56R on noise. Scaling the stop by
  recent bar range added noise that the tie rule turns into bias.
- Re-measured on two years of 1m data: no pattern beats its matched controls
  after correction, and none is profitable after costs.

### Features

- `candlebench report` and `run --html`: a self-contained HTML report with a
  leaderboard and charts per timeframe and per-pattern drill-downs. It has no
  scripts and no prices.
- The dashboard downloads any saved run as a report (`/api/report`).
- `serve --read-only` / `demo --read-only`: browse saved runs, refuse runs and
  fetches. Only a read-only server may bind beyond loopback.
- A `Dockerfile` serving the read-only interactive demo, checked in CI.
- GitHub Pages site with the two-year report and a demo report rebuilt from the
  pushed code: https://dpologdvinity.github.io/candlebench/

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
