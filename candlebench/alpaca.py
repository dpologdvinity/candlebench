"""Bars from Alpaca's market data API.

Yahoo caps intraday history at 28 days of 1m and 59 of everything coarser. That
cap is the caveat that bounds every number this project reports: a pattern
measured over that window has been tested against one market regime, not across
conditions. Alpaca's free tier serves bars back to 2016, which is the only way
to fix it without paying for data.

Three findings from probing the live API decided the shape of this module.

**The free tier serves consolidated volume, not IEX.** Measured against
yfinance's consolidated 1m bar volume over the same minutes: the default feed
returned 102.56% for AAPL, 99.70% for MSFT and 102.99% for KO, identical in
every case to an explicit `feed=sip`, while `feed=iex` returned 4.47%, 5.50% and
7.08%. (Over 100% is expected — raw prints include trade conditions the
consolidated volume tape excludes.) So no `feed` is requested and the full tape
is what arrives. Had it been IEX-only, bars built from 4% of the tape would have
measured IEX microstructure and reported it as the market.

**Split adjustment is a correctness requirement, dividend adjustment is not.**
Under `adjustment=raw` AAPL opens at 503.50 on 2020-08-28 and 128.05 on
2020-08-31 — an apparent 75% collapse that is entirely the 4:1 split, and a bar
a detector would happily score. Under `adjustment=split` the same two bars read
125.88 and 128.05, the real move. `adjustment=all` additionally rewrites every
historical price for later dividends, which moves the body and shadows away from
what actually traded, and bar geometry is precisely what every detector
measures. So: splits removed, dividends left alone.

**The 15-minute restriction binds on exactly one chunk, and loudly.** A request
for data five minutes old returns `HTTP 403 subscription does not permit querying
recent SIP data`; one for twenty minutes old succeeds. Backtests read days or
years back, so the restriction is irrelevant to the *data* — but a fetch window
ending at `now` is inside it, and the API refuses the whole request rather than
the restricted rows. The first real 120-day fetch came back with 62 sessions
ending a month early, with one failure logged per symbol, because the newest
chunk died entirely. `SIP_DELAY` is why that no longer happens.

Credentials come from the environment and nowhere else. A key in the TOML would
be committed; a key accepted from the browser would be echoed into a saved
report. Both are permanent mistakes.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

import pandas as pd

ENV_KEY = "ALPACA_API_KEY"
ENV_SECRET = "ALPACA_SECRET_KEY"

BARS_URL = "https://data.alpaca.markets/v2/stocks/bars"
TRADES_URL = "https://data.alpaca.markets/v2/stocks/trades"

# Alpaca's bar endpoint has no sub-minute timeframe: 1Sec, 5Sec, 10Sec and 30Sec
# are all rejected with "invalid timeframe". Sub-minute bars therefore have to be
# resampled from raw trades, which is a different endpoint and a different
# module.
TIMEFRAMES = {
    "1m": "1Min",
    "2m": "2Min",
    "5m": "5Min",
    "15m": "15Min",
    "30m": "30Min",
    "1h": "1Hour",
}

# Splits removed, dividends left alone. See the module docstring for the
# measurement behind this.
ADJUSTMENT = "split"

MAX_LIMIT = 10_000

# Alpaca's documented history start for equities.
HISTORY_START = "2016-01-01"

# How far back a fetch reaches when the config does not say. The provider
# maximum is about a decade, and a decade of 1m bars for fifty symbols is tens
# of millions of rows — too much to download because a default said nothing.
DEFAULT_LOOKBACK_DAYS = 365

# Free tier is 200 requests per minute.
RATE_LIMIT_PER_MINUTE = 200

# How far back a request must stop short of the present. The free tier refuses
# recent SIP data, and it refuses the whole *request* rather than the restricted
# rows: a window ending now returns HTTP 403 and the entire chunk is lost. A
# 120-day fetch measured this the hard way, coming back with 62 sessions ending a
# month early because the newest chunk failed for every symbol.
#
# 16 minutes rather than the documented 15: a warm-up runs for minutes, so a
# window built right on the boundary is inside it by the time a later request
# lands — the same lesson `INTERVAL_MAX_LOOKBACK_DAYS` records for Yahoo.
SIP_DELAY = timedelta(minutes=16)


def credentials() -> tuple[str, str]:
    """The key and secret, from the environment only."""
    key, secret = os.environ.get(ENV_KEY), os.environ.get(ENV_SECRET)
    missing = [name for name, value in ((ENV_KEY, key), (ENV_SECRET, secret)) if not value]
    if missing:
        raise RuntimeError(
            f"{' and '.join(missing)} not set. Alpaca bars need a free API key: "
            f"create one at alpaca.markets, then export {ENV_KEY} and "
            f"{ENV_SECRET} in your shell profile. They are read from the "
            "environment only, never from the config file or the browser, so a "
            "key cannot be committed or echoed into a saved report."
        )
    return key, secret


def timeframe(interval: str) -> str:
    """Alpaca's name for one of our intervals."""
    if interval not in TIMEFRAMES:
        raise ValueError(
            f"interval {interval!r} has no Alpaca bar timeframe. "
            f"available: {', '.join(TIMEFRAMES)}. sub-minute bars are not served "
            "by the bar endpoint and must be resampled from trades."
        )
    return TIMEFRAMES[interval]


def _http(params: dict) -> dict:
    """One GET against the bar endpoint."""
    return _get(BARS_URL, params)


def _http_trades(params: dict) -> dict:
    """One GET against the trade endpoint."""
    return _get(TRADES_URL, params)


def _get(endpoint: str, params: dict) -> dict:
    """One GET, with credentials from the environment."""
    key, secret = credentials()
    url = f"{endpoint}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(
        url, headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        body = exc.read() or b"{}"
        try:
            message = json.loads(body).get("message", "")
        except json.JSONDecodeError:
            message = body.decode(errors="replace")[:200]
        # The key never appears here: the message comes from the response body,
        # and the URL carries no credentials.
        raise RuntimeError(f"Alpaca returned HTTP {exc.code}: {message}") from None


def download(
    symbols: list[str] | tuple[str, ...],
    interval: str,
    start: datetime,
    end: datetime,
    request=None,
) -> pd.DataFrame:
    """Bars for several symbols, shaped the way `bars._extract` already reads.

    Returning a yfinance-shaped frame — a (symbol, field) column MultiIndex over
    a UTC index — is what lets this be dropped into `bars.warm_cache` as its
    `download` callable, so the cache format, the validation and everything
    downstream stay exactly as they were.

    Sub-minute intervals come from the trade endpoint and are resampled; every
    other interval comes from the bar endpoint. The caller cannot tell which,
    which is the point.
    """
    from candlebench import ticks

    if ticks.is_sub_minute(interval):
        return _download_resampled(symbols, interval, start, end, request or _http_trades)
    return _download_bars(symbols, interval, start, end, request or _http)


def _download_bars(symbols, interval, start, end, request) -> pd.DataFrame:
    params = {
        "symbols": ",".join(symbols),
        "timeframe": timeframe(interval),
        "start": _stamp(start),
        "end": _stamp(end),
        "limit": MAX_LIMIT,
        "adjustment": ADJUSTMENT,
    }

    collected: dict[str, list[dict]] = {}
    token: str | None = None
    while True:
        page = request({**params, "page_token": token} if token else params)
        for symbol, rows in (page.get("bars") or {}).items():
            collected.setdefault(symbol, []).extend(rows)
        token = page.get("next_page_token")
        if not token:
            break

    return _frame(collected)


def _download_resampled(symbols, interval, start, end, request) -> pd.DataFrame:
    """Trades for several symbols, resampled into sub-minute bars."""
    from candlebench import ticks

    params = {
        "symbols": ",".join(symbols),
        "start": _stamp(start),
        "end": _stamp(end),
        "limit": MAX_LIMIT,
    }

    collected: dict[str, list[dict]] = {}
    page_key: str | None = None
    while True:
        page = request({**params, "page_token": page_key} if page_key else params)
        for symbol, rows in (page.get("trades") or {}).items():
            collected.setdefault(symbol, []).extend(rows)
        page_key = page.get("next_page_token")
        if not page_key:
            break

    seconds = ticks.SECONDS[interval]
    pieces = {}
    for symbol, rows in collected.items():
        resampled = ticks.resample(_trade_frame(rows), seconds)
        if not resampled.empty:
            pieces[symbol] = resampled.rename(
                columns={c: c.capitalize() for c in resampled.columns}
            )
    if not pieces:
        return pd.DataFrame()
    return _regular_hours_only(pd.concat(pieces, axis=1))


def _trade_frame(rows: list[dict]) -> pd.DataFrame:
    """Alpaca trade rows as the frame `ticks.resample` reads."""
    if not rows:
        return pd.DataFrame(columns=["price", "size", "conditions"])
    frame = pd.DataFrame(
        {
            "price": [row["p"] for row in rows],
            "size": [row["s"] for row in rows],
            "conditions": [row.get("c") or [] for row in rows],
        },
        index=pd.DatetimeIndex(
            pd.to_datetime([row["t"] for row in rows], utc=True, format="ISO8601")
        ),
    )
    return frame


def _stamp(moment: datetime) -> str:
    return pd.Timestamp(moment).tz_convert("UTC").isoformat().replace("+00:00", "Z")


def _frame(collected: dict[str, list[dict]]) -> pd.DataFrame:
    """Alpaca's per-symbol row lists as one yfinance-shaped frame."""
    if not collected:
        return pd.DataFrame()

    pieces = {}
    for symbol, rows in collected.items():
        if not rows:
            continue
        frame = pd.DataFrame(rows)
        frame.index = pd.DatetimeIndex(pd.to_datetime(frame["t"], utc=True))
        pieces[symbol] = frame.rename(
            columns={"o": "Open", "h": "High", "l": "Low", "c": "Close", "v": "Volume"}
        )[["Open", "High", "Low", "Close", "Volume"]]

    if not pieces:
        return pd.DataFrame()
    return _regular_hours_only(pd.concat(pieces, axis=1))


def _regular_hours_only(frame: pd.DataFrame) -> pd.DataFrame:
    """Drop bars outside 09:30-16:00 Eastern, matching `prepost=False`.

    The Yahoo source asks for regular hours only, and nothing in this project
    ever reads an extended-hours bar: `bars.sessions` restricts the window
    before any price is measured, and the spread estimate reads those same
    session frames. Caching them would store 2.2x the rows, re-read and
    re-validate them on every trial, and leave the two sources holding
    different universes of bars so their cache counts could not be compared.

    The index stays UTC; only the comparison happens in market time.
    """
    from candlebench.bars import MARKET_TZ, SESSION_CLOSE, SESSION_OPEN

    if frame.empty:
        return frame
    local = frame.index.tz_convert(MARKET_TZ)
    minutes = local.hour * 60 + local.minute
    opens = SESSION_OPEN[0] * 60 + SESSION_OPEN[1]
    closes = SESSION_CLOSE[0] * 60 + SESSION_CLOSE[1]
    return frame[(minutes >= opens) & (minutes < closes)]
