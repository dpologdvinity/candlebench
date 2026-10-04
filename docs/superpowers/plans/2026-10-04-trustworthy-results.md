# Trustworthy Results Implementation Plan

Goal: implement all five improvements approved in the conversation, preserving
unavailable versus zero and keeping fetches explicit.

Architecture: extend Analysis for coverage, metrics for session-aware inference,
sampling/runner for chronological discovery and validation, and existing report
and dashboard surfaces for transparent evidence. Keep the standard-library web
server and use Playwright only as a development dependency.

User instruction: do all five improvements without stopping for approval.

## Decisions and acceptance conditions

- Stock ratings require at least three evaluated metrics and 60% coverage.
  Percentages are labelled indicator scores; show evaluated/total coverage.
- Drawdown includes starting equity zero; statistics and chart share the rule.
- Resample whole market dates (all symbols and timeframes together), not trades.
  Report trade-weighted mean expectancy, date-clustered 95% intervals, and a
  paired bootstrap interval for pattern minus direction-matched control.
- EDGE needs usable pattern and control samples (30 trades and 10 distinct
  sessions by default), positive expectancy and positive paired delta, both
  significant after Holm correction across all enabled report rows. Missing
  controls never establish EDGE. Controls can be NEGATIVE/NOISE/INSUFFICIENT,
  never EDGE because they have no pattern edge to demonstrate.
- Count both expectancy and baseline hypotheses in the testing family, including
  unmeasurable hypotheses. stats.experiment_count defaults to 1; a declared
  parameter sweep multiplies adjusted p-values conservatively across configs.
  Pointwise 95% intervals remain labelled separately from corrected verdicts.
- run.holdout_fraction defaults to 0.2. Reserve the newest fraction of distinct
  cached dates before sampling or selection; draw discovery and validation
  separately with chronological dates and independent deterministic seeds.
  The total requested trial count is divided between them. Zero disables
  validation for exploratory replays, which cannot establish confirmed EDGE.
  Refuse impossible splits/windows explicitly rather than silently disabling.
- Discovery candidates are selected before validation. Only selected candidates
  can become final EDGE after independent corrected validation evidence.
  Preserve the original discovery verdict and nest validation evidence in each
  report row. Validation is not tuning data: repeated use is explicitly warned.
- Persist exact sampled phases, seeds, split date, sample coverage and inference
  method. Dashboard and CLI identify discovery/validation and display paired
  interval, corrected significance and validation outcome. Drilldowns default
  to discovery and can select validation; legacy files remain readable.
- Real browser tests exercise config submissions, progress, failure/recovery,
  sorting, trade paging, run comparison, validation evidence and no-data values.
  Use deterministic local fixtures and prohibit live market requests.

## Tasks

- [x] Write regression tests; fix stock coverage and starting-equity drawdown.
- [x] Write statistical tests; implement date-cluster bootstrap, paired inference,
      minimum independent-session floor and Holm multiple-comparison correction.
- [x] Write split/runner tests; implement chronological holdout, selection before
      validation, leakage prevention and explicit insufficient-data handling.
- [x] Update CLI/API/CSV/dashboard evidence, filters and legacy compatibility;
      add focused rendering and API regressions.
- [x] Add Playwright browser suite and documented setup; fix exposed UI defects.
- [ ] Run Python and Chromium suites, inspect rendered desktop/mobile dashboard,
      update documentation and review the final diff.

## Review focus

No-control and one-date samples cannot establish edge; repeated sampled dates
must stay in one inference cluster. Discovery must not depend on holdout prices
or control rates. No qualifying holdout candidates must not be disguised as a
successful validation. Old JSON/Parquet must not be labelled newly validated.
Parameter selection or repeated inspection of the holdout is not unseen proof.
