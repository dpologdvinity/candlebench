"""What a round trip costs.

The project's headline finding is that trading costs exceed whatever edge the
classic patterns carry. That made `slippage_bps = 1.0` — a flat guess applied to
every symbol, session and timeframe alike — the least evidenced number in the
system and the one the conclusion rested on.

`quotes.py` now observes that spread from real NBBO quotes, and is what any
claim about costs should rest on. This module is the credential-free fallback:
it *infers* the spread from the bars already in the cache, using the Corwin and
Schultz (2012) high-low estimator. Measured against the observed table it reads
low — 1.28 bps per leg against 1.83 across 50 symbols — so a conclusion drawn
from it understates the cost of trading. The idea: a bar's own high-low
range contains the spread once, while a two-bar range contains it once as well
but spans twice the variance. Comparing the two separates the spread from the
volatility.

    beta  = ln(H_t/L_t)^2 + ln(H_t+1/L_t+1)^2
    gamma = ln(max(H_t,H_t+1)/min(L_t,L_t+1))^2
    k     = 3 - 2*sqrt(2)
    alpha = (sqrt(2*beta) - sqrt(beta))/k - sqrt(gamma/k)
    S     = 2*(exp(alpha) - 1)/(1 + exp(alpha))

`S` is the proportional *round-trip* effective spread. The estimator is noisy on
a single pair and can come out negative, which the paper handles by clamping to
zero; a negative spread would pay the trader for crossing it. A session whose
estimate clamps to zero is treated as unmeasured rather than as free, and falls
back to the configured fixed cost.

No new data is needed for any of this, which is the point: the cost assumption
becomes a measurement over the bars already on disk.

One measured caveat shapes how the estimate is used. Run on each timeframe's own
bars, the estimator returns a round-trip spread that climbs monotonically with
bar length — about 1.8 bps at 1m, 4.6 at 5m, 8.1 at 15m, 11.3 at 30m and 15.1 at
1h over the same symbols and days. A spread cannot depend on how finely you slice
the bars you look at; a trader holding an hour pays the same spread to get in as
one holding a minute. The climb is the estimator's known bias, which scales with
the volatility inside a bar, and it survives every aggregation: pooling beta and
gamma before solving, or averaging the raw pair estimates and clamping once, both
reduce the level and leave the slope.

So the spread is estimated once per symbol and session from the *narrowest*
enabled interval's bars and charged unchanged at every timeframe. Letting each
timeframe price itself would have charged roughly ten times as much at 1h as at
1m and made the coarse intervals cost-dominated by construction — which would
have manufactured this project's central finding instead of testing it.
"""

from __future__ import annotations

import math

import numpy as np

# The constant from the paper's derivation, 3 - 2*sqrt(2).
K = 3 - 2 * math.sqrt(2)

# "quoted" observes the spread from NBBO quotes (see `quotes.py`) and is the
# most direct of the three; "estimated" infers it from high-low ranges; "fixed"
# charges a flat number. Quoted is preferred where a table exists because the
# estimator reads high: 1.21 bps per leg against an observed 0.45 for AAPL.
MODELS = ("quoted", "estimated", "fixed")

# Above this round-trip spread the estimator has stopped measuring a spread and
# is reporting its own saturation: `S = 2*(exp(a) - 1)/(1 + exp(a))` asymptotes
# to 2.0, so two degenerate bars can "estimate" 200% of price, and charging that
# would double the entry price and label it measured.
#
# 2% is deliberately generous. Measured over 9,348 real symbol-sessions across
# 50 liquid symbols and five timeframes, the worst 1m estimate is under 50 bps
# and the worst at any timeframe is 103 bps, on a 1h session of seven bars. A
# tighter ceiling would substitute the flat guess for a real if noisy
# measurement, which is the opposite of why the estimator is here; this one only
# catches the degenerate regime.
MAX_PLAUSIBLE_SPREAD = 0.02


def corwin_schultz(high: np.ndarray, low: np.ndarray) -> float | None:
    """Proportional round-trip spread estimated from one session's bars.

    Returns the mean of the per-pair estimates with negatives clamped to zero,
    or None when fewer than two usable bars remain. A result of exactly zero
    means every pair clamped, which the caller should read as unmeasured.
    """
    high = np.asarray(high, dtype=np.float64)
    low = np.asarray(low, dtype=np.float64)

    # A non-positive or non-finite price makes the log undefined, and a high
    # below its low cannot be real. Dropping those bars keeps the adjacent pairs
    # formed from bars that remain, which is what the estimator assumes.
    usable = (
        np.isfinite(high) & np.isfinite(low) & (high > 0) & (low > 0) & (high >= low)
    )
    high, low = high[usable], low[usable]
    if len(high) < 2:
        return None

    first_high, second_high = high[:-1], high[1:]
    first_low, second_low = low[:-1], low[1:]

    beta = np.log(first_high / first_low) ** 2 + np.log(second_high / second_low) ** 2
    gamma = (
        np.log(np.maximum(first_high, second_high) / np.minimum(first_low, second_low))
        ** 2
    )

    alpha = (np.sqrt(2 * beta) - np.sqrt(beta)) / K - np.sqrt(gamma / K)
    spread = 2 * (np.exp(alpha) - 1) / (1 + np.exp(alpha))
    return float(np.maximum(spread, 0.0).mean())


def one_way_fraction(cost_cfg, high: np.ndarray, low: np.ndarray) -> tuple[float, float | None]:
    """The cost to charge per leg, and the round-trip spread it came from.

    Returns `(fraction, estimate)`. `estimate` is None whenever the fixed model
    was used, either because it was configured or because the bars could not be
    estimated — the caller reports it so the cost assumption stays visible.

    A leg pays half the round trip, which is what `slippage_bps` has always
    meant: `simulate` charges it once on entry and once on exit.
    """
    fixed = cost_cfg.slippage_bps / 10_000.0
    if cost_cfg.model == "fixed":
        return fixed, None

    estimate = corwin_schultz(high, low)
    # None, zero because every pair clamped, or so large the estimator has
    # saturated rather than measured. All three are unmeasured, not free and not
    # enormous, so all three fall back and are counted as fallbacks.
    if not estimate or estimate > MAX_PLAUSIBLE_SPREAD:
        return fixed, None
    return estimate / 2.0, estimate
