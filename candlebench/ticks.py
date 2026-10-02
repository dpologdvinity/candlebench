"""Sub-minute bars, resampled from raw trade prints.

Neither provider serves a sub-minute bar. Yahoo has no interval below 1m, and
Alpaca's bar endpoint rejects `1Sec`, `5Sec`, `10Sec` and `30Sec` with "invalid
timeframe". So a sub-minute bar has to be built from the trades themselves.

Two ways to get that wrong, both silent.

**Fabricating bars.** An interval with no prints in it has no bar. Forward-filling
the previous close is the conventional thing to do and would be wrong here: it
invents a doji at a price nobody traded, and a doji is a pattern this project
detects. Twenty detectors read exactly the body-and-shadow geometry that a
forward-filled bar fabricates, so empty intervals are dropped and the series is
left with holes. A hole is the truth.

**Trusting every print for price.** Some trade conditions are ineligible for the
consolidated high and low, and including them hangs a shadow off the bar
reaching a price that was never available in the market.

Which conditions those are was settled by measurement rather than by reading the
tape rules: trades were resampled to 1m and compared against the provider's own
1Min bars over 171,317 prints and 75 bars across five symbols. Two findings came
out of it, and the second is the one that matters.

Excluding `{W, 4, I, 7, V}` reproduces the provider's OHLC on 74 of 75 bars.
Excluding only `{W, 4, I}` manages 68. Adding the other documented-ineligible
codes — `Z, U, P, R, C, N, M, O, 6, 9` — changes nothing on this sample, so they
are left out: excluding a code that is in fact eligible would drop a legitimate
extreme and shrink a bar's range, which is the same class of error in the
opposite direction. One bar in 75 still disagrees, and that is disclosed rather
than papered over.

**Volume and price follow different rules.** Reproducing the provider's volume
needs every print counted, including the ones excluded from the price. Filtering
both — the obvious implementation, and the one written first here — matched
volume on 0 of 5 bars and came out 21% light. So volume sums all prints in the
interval while the open, high, low and close come from the eligible ones only.

A warning about scale, which is a property of the data and not of this code: a
liquid large-cap prints on the order of a million trades a day, and the endpoint
pages at 10,000. One symbol-day is therefore around a hundred requests against a
200-per-minute budget. Sub-minute work is for a handful of symbols over a handful
of days, and `config.validate` says so rather than letting a default start an
hours-long download.
"""

from __future__ import annotations

import pandas as pd

# `bars` is imported inside the functions below rather than here: `bars`
# registers this module as a source at its own import time, so a module-level
# import either way round would be circular.

# The sub-minute intervals this module can build.
SECONDS = {"1s": 1, "5s": 5, "10s": 10, "30s": 30}

# Conditions whose prints may not set the open, high, low or close. Each is
# ineligible because its price is not one that was standing in the market:
#   W  average-price trade        — a computed price, not a traded one
#   4  derivatively priced        — priced off another instrument
#   I  odd lot                    — excluded from the consolidated last sale
#   7  qualified contingent trade — one leg of a package
#   V  contingent trade           — likewise
# Measured to reproduce the provider's own bars on 74 of 75; see the module
# docstring for what was tried and rejected.
PRICE_EXCLUDED_CONDITIONS = frozenset({"W", "4", "I", "7", "V"})

# The most symbol-days a sub-minute fetch may ask for. A liquid large-cap prints
# on the order of a million trades a day and the endpoint pages at 10,000, so one
# symbol-day is roughly a hundred requests against a 200-per-minute budget.
# Fifty symbols over a year would be on the order of a million requests; a
# default must not be able to start that, so `config.validate` refuses it and
# states the arithmetic.
MAX_SUB_MINUTE_SYMBOL_DAYS = 20

# Mirrors the provider's limits, quoted in the refusal so the arithmetic is
# checkable rather than asserted.
PAGE_LIMIT = 10_000
RATE_LIMIT_PER_MINUTE = 200


def is_sub_minute(interval: str) -> bool:
    return interval in SECONDS


def resample(trades: pd.DataFrame, seconds: int) -> pd.DataFrame:
    """OHLCV bars of `seconds` width from a frame of trade prints.

    `trades` is indexed by trade time and carries `price`, `size` and
    `conditions`. The result carries `bars.BAR_COLUMNS` over a UTC index, which
    is the cache's own shape, so nothing downstream can tell where it came from.

    Intervals with no eligible prints produce no row.
    """
    from candlebench.bars import BAR_COLUMNS

    empty = pd.DataFrame(
        {name: pd.Series(dtype="float64") for name in BAR_COLUMNS},
        index=pd.DatetimeIndex([], tz="UTC", name="timestamp"),
    )
    if trades is None or trades.empty:
        return empty

    # Sorted first: open and close are the first and last print by time, and a
    # feed may publish out of order.
    ordered = trades.sort_index()
    eligible = ordered[~ordered["conditions"].map(_price_ineligible)]
    if eligible.empty:
        return empty

    # Price from the eligible prints, volume from all of them. Measured against
    # the provider's own bars: filtering both came out 21% light on volume.
    prices = eligible.resample(f"{seconds}s")["price"]
    sizes = ordered.resample(f"{seconds}s")["size"]
    out = pd.DataFrame({
        "open": prices.first(),
        "high": prices.max(),
        "low": prices.min(),
        "close": prices.last(),
        "volume": sizes.sum(),
    })

    # `resample` emits a row per interval in the span, including the ones nobody
    # traded in. Those are the fabricated bars; they go.
    out = out[out["volume"] > 0].dropna(subset=["open"])
    out.index.name = "timestamp"
    return out[list(BAR_COLUMNS)] if len(out) else empty


def _price_ineligible(conditions) -> bool:
    if conditions is None or not len(conditions):
        return False
    return any(code in PRICE_EXCLUDED_CONDITIONS for code in conditions)
