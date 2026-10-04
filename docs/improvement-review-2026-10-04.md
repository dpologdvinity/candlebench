# Engineering improvement review

Date: October 4, 2026

Status: Proposed work; implementation pending

## Scope

Review of `candlebench` and `stock.py`, with emphasis on result integrity,
failure recovery, reproducibility, and avoidable computation. The existing
date-clustered inference, multiple-comparison correction, holdout validation,
drawdown calculation, and stock-rating coverage requirements provide a useful
foundation. The following work addresses remaining gaps.

Configuration validation and report-save failure behavior were checked with
targeted reproductions. Quote coverage was checked with a synthetic cached
session. Other findings are based on code inspection. The full test suite and
performance benchmarks were not run for this review.

## Recommended order

| Priority | Work item | Intended outcome |
| --- | --- | --- |
| 1 | Complete configuration validation | Reject settings that distort results or fail during execution |
| 2 | Publish completed runs atomically | Keep reports and trades consistent and recover from save failures |
| 3 | Protect cached history | Preserve valid data through interrupted or partially failed fetches |
| 4 | Improve cost provenance and reporting | Distinguish observed, imputed, estimated, and fixed costs |
| 5 | Consolidate bootstrap calculations | Remove redundant computation while retaining the inference method |
| 6 | Record automatic run manifests | Make ordinary runs reproducible and auditable |
| 7 | Add run preflight checks | Explain data limitations before starting work |
| 8 | Preserve unavailable earnings evidence | Keep missing provider data out of stock-rating scores |

## 1. Complete configuration validation

**Location:** `candlebench/config.py:283`; `candlebench/web/server.py:65`

Validation accepts negative commissions, nonfinite reward multiples, and
fractional holding limits. Negative commissions increase net returns;
nonfinite or incorrectly typed values can propagate invalid calculations or
fail after a run starts. Browser requests also silently ignore unknown
top-level sections, while some malformed types raise exceptions other than
the `ValueError` handled by the request layer.

Define type, finiteness, and range checks at the shared configuration boundary.
Require integers for counts and holding limits, reject negative commission
charges, and validate trade and threshold values. Validate request structure
and reject unknown top-level keys before applying updates.

**Acceptance criteria:** Invalid file and API settings fail with clear messages;
malformed API configurations return 400 without starting a job. Valid existing
configurations remain supported.

## 2. Publish completed runs atomically

**Location:** `candlebench/web/jobs.py:131`

The latest trade file is replaced before its matching report is published.
A failure during archiving or report persistence can therefore leave the
latest report and trades referring to different runs.

Report persistence also occurs outside the worker's exception handler. A
simulated write failure terminated the worker, left the job marked `working`,
published the unsaved report in memory, and caused the next submission to be
rejected.

Stage the report and trades as one completed run and expose them through a
single publication point. Include persistence in failure handling and publish
the in-memory result only after the durable save succeeds.

**Acceptance criteria:** Failures at each save stage preserve the last consistent
run, set the job to `error`, and permit a subsequent submission. Concurrent
readers receive a report and trades from the same run.

## 3. Protect cached history

**Location:** `candlebench/bars.py:330`

Fetch chunks are accumulated independently. When one chunk fails but others
succeed, the successful subset can replace an existing, more complete cache.
Cache files are also written directly to their destination, so an interrupted
write can damage the previous file.

Write replacements through temporary files and retain existing valid history
when a refresh is incomplete. Record missing chunks and make partial coverage
visible. Any merge must preserve source separation and deterministic handling
of duplicate timestamps.

**Acceptance criteria:** Partial downloads and interrupted writes preserve
previously valid data. Coverage gaps remain explicit, and a successful refresh
publishes a validated, readable cache.

## 4. Improve cost provenance and reporting

**Location:** `candlebench/quotes.py:146`; `candlebench/runner.py:263`

A missing quote bucket borrows the mean of the symbol's available buckets.
That is an imputed cost for the missing time period. The runner counts the
session as quoted, without distinguishing those substitutions. A synthetic
session with only an opening-bucket quote reported `quoted_share = 1.0` and a
description of observed quotes throughout the session.

The reported `charged_bps` is also an average over session bars, rather than
the entry and exit legs on which trades actually paid costs. Sessions with
no trades contribute to this reported average.

Carry cost provenance through lookup and execution. Report exact bucket
coverage separately from imputation, estimation, and fixed fallback. Calculate
the average cost charged from executed trade legs, keeping any market-wide
bar average separately labeled.

**Acceptance criteria:** Incomplete quote tables disclose substitutions. The
reported average charged cost matches executed legs; a run with no trades
does not present a market-bar average as a cost actually paid.

## 5. Consolidate bootstrap calculations

**Location:** `candlebench/metrics.py:284` and `candlebench/metrics.py:342`

`summarise` calculates bootstrap evidence for each row. `attach_baselines`
then performs a joint bootstrap and replaces that evidence. The production
runner therefore computes results it subsequently discards.

Separate descriptive aggregation from inference and calculate production
evidence once in the joint date-clustered pass. Retain shared date weights,
paired control comparisons, sample-size requirements, and correction rules.
Avoiding unnecessary work will also change random-number consumption; define
and record the resulting reproducibility behavior explicitly.

**Acceptance criteria:** Production inference runs once per sample phase.
Deterministic fixtures retain the intended statistical behavior. Benchmark
representative runs before claiming a speedup.

## 6. Record automatic run manifests

**Location:** `candlebench/leaderboard.py:302`; existing manifests in
`docs/experiments/`

Ordinary reports record configuration, trials, and seeds, but omit code and
dependency versions and input hashes. Refreshing the cache or quote table can
change a replay. Saved session charts also read the current cache, which may
no longer match the data used by the saved run.

Extend the manifest pattern used by the documented experiments to ordinary
runs. Record code revision, working-tree state, relevant dependency versions,
and bar/quote fingerprints. Detect changed inputs before presenting a saved
chart as a reconstruction of the original run.

**Acceptance criteria:** A saved run identifies its inputs and implementation.
Changed or missing inputs produce an explicit replay or chart limitation.
Historical reports remain readable with provenance marked unavailable.

## 7. Add run preflight checks

Summarize discovery and validation date coverage, missing intervals, requested
trial allocation, and whether the available data can satisfy the configured
evidence floors. Reuse existing validation and sampling rules so the summary
agrees with execution.

**Acceptance criteria:** Before execution, users can see impossible splits and
known coverage limits. Clearly distinguish data sufficiency from whether a
pattern will generate enough trades or pass discovery.

## 8. Preserve unavailable earnings evidence

**Location:** `stock.py:396`

An unavailable earnings calendar, including a provider exception, becomes a
zero-scored metric labeled `none scheduled`. This counts missing data as an
evaluated failure and conflicts with the rating's coverage convention.

Distinguish an unavailable calendar from a successfully retrieved calendar
without an upcoming event. Exclude unavailable evidence from the score and
coverage count.

**Acceptance criteria:** Provider errors and unavailable calendars produce an
unscored metric. A confirmed absence remains distinguishable in the output.

## Next implementation scope

Start with items 1–3. Add focused regression coverage for invalid settings,
save failures, consistent run publication, and partial cache refreshes. Follow
with cost provenance and the bootstrap consolidation, then reproducibility
and preflight improvements. No implementation changes were made as part of
this review.
