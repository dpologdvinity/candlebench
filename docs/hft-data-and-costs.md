# Data and cost measurements for ~/git/hft

October 2, 2026. Written for the AI stock trader in `~/git/hft`, as a companion to
that repo's `docs/stock-analyzer-integration.md`, which already assesses reusing
this repo's *code*. This document covers what was measured against the live
Alpaca API while building this repo's cost model, because three of those
measurements bear directly on that project's current blocker and none of them are
code to be extracted.

Nothing here is implemented in HFT. Every figure was measured in one session and
is reproducible; where a claim is unverified it says so.

## Summary

| Finding | Bearing on HFT |
| --- | --- |
| The free tier serves **full consolidated (SIP)** data on *historical* endpoints | Likely explains `runtime-feed-gap`; could unblock the preflight |
| IEX fills **31.9%** of MCD's 5-second buckets; SIP fills **100%** | Quantifies the gap at that project's exact decision cadence |
| IEX is exchange code `V` **inside** SIP, and filtering reproduces it exactly | One download serves both a dense training tape and a faithful live-feed replica |
| Observed NBBO half-spreads for 50 symbols, per time-of-day bucket | A measured cost input; the open runs ~4x midday |
| Corwin-Schultz **understates** cost on a broad universe | Cautions against estimator-based cost terms |
| Trade-condition eligibility rules, validated at 74/75 | Directly applicable to building 5-second bars |

## 1. The free tier is not IEX-only for history

HFT's handoff and integration assessment both describe its MCD dataset as
"free-IEX". For *real-time* data that is correct. For *historical* endpoints it is
not: the free tier serves full consolidated volume.

Measured against yfinance's consolidated 1m bar volume over the same minutes:

| Symbol | Default feed | Explicit `feed=iex` |
| --- | --- | --- |
| AAPL | 102.56% | 4.47% |
| MSFT | 99.70% | 5.50% |
| KO | 102.99% | 7.08% |

The default feed is byte-identical to an explicit SIP request — same trade count,
same share count. Over 100% is expected: raw prints include trade conditions the
consolidated volume tape excludes.

Two further properties were measured:

- **History reaches 2016.** AAPL trades returned for 2016-06-15.
- **The 15-minute restriction binds only on recent data.** Five minutes old
  returns `HTTP 403 subscription does not permit querying recent SIP data`;
  twenty minutes old succeeds. It never binds on backtest data, but it *always*
  binds on a 5-second live decision.

I had earlier asserted the opposite — that the free tier was IEX-only — and that
claim is what put sub-minute work out of scope in this repo. It was wrong.

## 2. What that means for the feed-gap blocker

`artifacts/mcd-v3/search/research.json` reports `insufficient-data`, reason
`runtime-feed-gap`, with diagnostics showing 42 eligible decisions against 4,577
ineligible and 936 feed gaps in the one development session inspected.

Measured on MCD, one hour of a real session at HFT's 5-second cadence:

```
MCD 2026-09-15 11:00-12:00 ET, 720 buckets of 5 seconds

  SIP (default)   13,254 prints    720/720 buckets have a print (100.0%)      0 empty
  IEX only           509 prints    230/720 buckets have a print ( 31.9%)    490 empty
```

**68% of 5-second buckets on IEX contain no print at all.** That is the right
order of magnitude to produce the observed eligible/ineligible ratio.

This is a hypothesis, not a confirmed diagnosis. I did not read `hft/feed.py` or
`hft/research.py`, so I cannot say HFT's gap definition is the same as "no print
in the bucket", nor that MCD's midday hour is representative of the sessions that
failed. **The test that would settle it:** re-run the existing development
preflight against SIP-sourced MCD sessions and compare the eligible count. If the
42/4,577 ratio moves sharply, the blocker was feed sparsity rather than anything
in the research code — and no memory or data-volume change was ever needed.

## 3. The enabler: IEX is identifiable inside SIP

The obvious objection to training on SIP is train/serve skew. Real-time SIP is not
free, so a model trained on a dense tape would be served a tape with 68% of its
buckets empty. Features would differ in distribution between training and live,
which invalidates a model more quietly than a feed gap does.

That objection has a clean answer. IEX prints carry exchange code `V`, and they
are present *within* the SIP response:

```
exchange codes in SIP:  {'D': 1421, 'T': 237, 'N': 231, 'Z': 194, 'P': 153,
                         'K': 105, 'V': 84, 'U': 52, ...}
exchange codes in IEX:  {'V': 84}

SIP filtered to x == "V": 84 prints
  trade ids match: 84 of 84 IEX prints present in filtered SIP
  reproducible: True
```

One SIP download therefore yields both:

1. **A dense tape** (100% bucket coverage) for training and historical evaluation.
2. **A faithful replica of the live free feed** (`x == "V"`, 31.9% coverage) for
   measuring exactly what the production feed will and will not see.

That turns an unknown skew into a measured one, which is what HFT's evidence
standard asks for. Three uses follow, in increasing ambition:

- **Diagnostic only (cheapest).** Keep training on IEX. Use SIP purely to measure
  how much information the live feed loses — per session, per time of day. This
  answers "is the free feed sufficient for a 5-second contract?" with evidence,
  and that is arguably the most important unanswered question in the project.
- **Train on the replica.** Source from SIP but filter to `x == "V"` everywhere,
  so training and serving see the same feed by construction while the dense tape
  stays available for diagnostics. No skew introduced; the gap problem is not
  solved, only measured.
- **Train on SIP, serve on IEX.** Unblocks the preflight immediately but takes on
  real skew. Only defensible with an explicit measurement of the gap's effect on
  the features and the policy — which the first option supplies.

I would not pick between these. The first is nearly free and informs the others.

**Caveat on reserved dates.** The integration assessment is right to warn about
provenance. If SIP-sourced sessions cover dates already reserved as final tests,
re-downloading the same dates from a denser feed does not make them unseen. The
reservation is on the date, not on the bytes.

## 4. Observed spread costs

This repo now carries a measured half-spread table: 50 liquid symbols, three
time-of-day buckets, sampled from historical NBBO quotes.

| Bucket | Median bps/leg | Min | Max |
| --- | --- | --- | --- |
| open (first 30 min) | 2.67 | 0.13 | 16.07 |
| midday | 1.52 | 0.13 | 4.44 |
| close (last 30 min) | 1.02 | 0.13 | 5.26 |

Sampled at 09:31, midday and 15:45 across five symbols and three sessions, **the
open runs a median 4.0x midday and as much as 7.7x**: AAPL 1.50 against 0.30, XOM
7.11 against 1.33, JPM 12.66 against 1.65 on one session. A cost term that is one
number per symbol prices the open wrong by several multiples, and the open is
where intraday edges usually appear to live.

Source is `candlebench/quotes.py`, written to `.cache/bars/quoted_spreads.json`;
the sampler costs nine requests per symbol-session. HFT already models quote-depth
fills, so this is not a replacement for its executor — it is a cross-check on
whether modelled costs match observed quotes, and a ready input for symbol
selection.

**The leakage warning in the integration assessment applies and is correct.** A
session-wide or universe-wide spread figure used as a *feature* leaks. Used as a
*cost*, fit it on training dates only and keep observed and estimated provenance
separate. The table above is sampled from four sessions in late September 2026;
treat those dates as consumed for anything they inform.

## 5. A caution drawn from my own error

I checked AAPL's quoted half-spread (0.45 bps) against Corwin-Schultz's estimate
(1.21 bps), concluded the estimator overcharged by ~2.7x, and reported that this
repo's central finding was likely overstated by about half.

That was wrong. AAPL is the most liquid name in the universe. Across all 50
symbols the observed median is 1.52 bps/leg at midday and 2.67 at the open;
priced from the full table a run charges **1.83 bps/leg against the estimator's
1.28**, and mean expectancy moves **−0.074R — worse, not better**. The estimator
*understates* cost on a broad universe.

Two transferable lessons:

- **A spot check on your most liquid symbol does not generalise.** MCD is a single
  large-cap; a cost or coverage figure measured on it should not be extended to a
  wider universe without re-measuring.
- **High-low spread estimators are not a safe substitute for quotes** when the
  conclusion depends on the cost term. Separately measured: Corwin-Schultz's
  answer climbs monotonically with bar length — 1.8 bps at 1m to 15.1 at 1h over
  the same symbols and days — which is a bias, since a spread cannot depend on how
  finely you slice the bars. It also collapses toward zero when bars have no
  range, returning 2.028 bps from 1m bars and 0.074 from 1s on the same symbol and
  day.

## 6. Building bars from prints

If HFT builds 5-second bars from trade prints, two rules were established by
comparing resampled bars against the provider's own 1Min bars over 171,317 prints
and 75 bars across five symbols.

**Which prints may set a price.** Excluding conditions `W, 4, I, 7, V` — average
price, derivatively priced, odd lot, and the two contingent-trade codes —
reproduces the provider's OHLC on **74 of 75 bars**. Excluding only `W, 4`
reproduces **0 of 75**, because odd lots are over half a liquid name's prints and
are not last-sale eligible. Adding the other documented ineligible codes changed
nothing on this sample, so they were left out: excluding an eligible code drops a
legitimate extreme, the same error inverted. One bar in 75 still disagrees.

**Volume and price follow different rules.** Reproducing the provider's volume
needs every print counted, including those excluded from the price. Filtering
both — the obvious implementation — matched volume on **0 of 5 bars** and came out
**21% light**.

Note a collision worth guarding against: `V` is both a contingent-trade condition
code and the IEX *exchange* code. They are different fields (`c` and `x`).
Conflating them would either discard the entire IEX feed or admit contingent
trades into the price.

**Print sparsity changes what the geometry means.** On SIP, 30% of AAPL's 1s bars
and 68% of KO's have open = high = low = close, against 0.00% at 1m. Such a bar is
real, not fabricated, but it is a perfect doji — and any feature reading body or
shadow geometry at that resolution is substantially measuring how often a single
eligible print landed in the interval. On IEX, with 68% of 5-second buckets empty
entirely, this is worse. If any of the v3 features are shaped like "body relative
to range", this is worth checking before interpreting them.

## 7. What I did not verify

Stated plainly, because the integration assessment sets the right standard:

- I did not read HFT's source. Every claim about its code comes from `HANDOFF.md`
  and the existing assessment, not from inspection.
- The MCD coverage measurement is one hour of one mid-session day. The open and
  close will differ; other sessions will differ.
- I did not confirm that HFT's `runtime-feed-gap` definition corresponds to "no
  print in the bucket". Section 2's hypothesis stands or falls on that.
- The spread table is four sessions in late September 2026. It is not a
  point-in-time historical series, and using it to price trades from 2024 assumes
  spreads were comparable, which was not tested.
- This repo's own conclusion is that no candlestick pattern earns an edge at any
  timeframe, over two years and 49,062 trades. Nothing here suggests pattern
  features will help HFT; the integration assessment's framing of them as a
  *candidate baseline* rather than an expected improvement is the honest one.

## Suggested order

1. **Measure the feed gap** (section 2). Re-run the existing development preflight
   against SIP-sourced MCD and compare eligible counts. Cheap, and it determines
   whether the current first project addresses the real blocker.
2. **Quantify what the live feed loses** (section 3, diagnostic option). Replay the
   same sessions as SIP and as `x == "V"`, and report the difference in eligible
   decisions and feature distributions. Answers whether a free-feed 5-second
   contract is viable at all.
3. **Cross-check modelled costs against observed quotes** (section 4), on
   development dates only.
4. Only then consider the pattern baseline the integration assessment describes.

Steps 1 and 2 are data questions that could change what HFT does next. Step 4 is a
feature experiment that, on this repo's own two-year evidence, should be expected
to find nothing.

## Reproducing the measurements

Requires `ALPACA_API_KEY` and `ALPACA_SECRET_KEY` in the environment.

```bash
# the spread table (nine requests per symbol-session)
python -m candlebench quotes --sessions 4

# bars from either source, split-adjusted, back to 2016
python -m candlebench fetch        # with [run] source = "alpaca"

# a run priced from observed quotes rather than an estimate
python -m candlebench run          # with [costs] model = "quoted"
```

Relevant modules: `candlebench/quotes.py` (NBBO sampling),
`candlebench/alpaca.py` (feeds, split adjustment, the 15-minute lag),
`candlebench/ticks.py` (print conditions, the volume/price split rule),
`candlebench/costs.py` (the estimator and why it is not preferred).
