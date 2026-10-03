"""Quoted spreads, observed rather than estimated.

This project's headline finding is that trading costs exceed whatever edge the
classic candlestick patterns carry, which makes the cost term the number the
whole conclusion rests on. `costs.py` infers it from high-low ranges with the
Corwin-Schultz estimator, because when that was written no better data was to
hand. Alpaca's free tier serves historical NBBO quotes, so the spread can be
*observed* instead of inferred, and this module does that.

Two measurements decided the shape of it, both taken before any of it was built.

**The estimator reads low, which is the opposite of what one symbol suggested.**
AAPL's median quoted half-spread is 0.45 bps against the estimator's 1.21, and on
that basis the estimator looked like it charged nearly three times too much. AAPL
is the most liquid name in this universe and does not generalise. Across all 50
symbols the observed median is 1.52 bps per leg at midday, 2.67 at the open and
up to 16.07 for the widest name. Priced from the full table the run charges 1.83
bps per leg against the estimator's 1.28, and mean 1m expectancy moves -0.074R —
*worse*, not better.

So the project's central finding is stronger than it reported, not weaker: costs
exceed the patterns' edge by a wider margin than the estimator implied. Still no
EDGE under either model.

**The spread is not one number per symbol.** Sampled at 09:31, midday and 15:45
across five symbols and three sessions, the open runs a median 4.0x midday and
as much as 7.7x: AAPL 1.50 against 0.30, XOM 7.11 against 1.33, JPM 12.66
against 1.65 on one session. A trade entered in the first half hour pays several
times what the same trade pays at lunch. So the table is keyed by
`(symbol, time-of-day bucket)` using `trades.TIME_BUCKETS` — the same buckets the
breakdown already groups by — and `engine.simulate` charges each leg at the bar
it actually filled on.

Sampling rather than reading every quote is a deliberate limit. A liquid name
quotes 38 to 65 times a second, so a full session is on the order of a million
quotes and fifty symbols over two years is out of reach on any free tier. The
table instead samples a few seconds inside each bucket across a few sessions and
takes the median, which is stable because the intraday *shape* is stable even
though the level moves. What it cannot capture is a spread that widened on one
specific day, so a volatile session is priced at the symbol's typical cost.
"""

from __future__ import annotations

import json
import statistics
import time as _time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from candlebench import trades

QUOTES_URL = "https://data.alpaca.markets/v2/stocks/quotes"

# Seconds of quotes taken per sample point. A second holds dozens of quotes,
# which is plenty for a median and keeps one request per point.
SAMPLE_SECONDS = 2

# Sample points inside each bucket. Three is enough to survive one point landing
# in a quiet second while keeping a session to nine requests.
SAMPLES_PER_BUCKET = 3

# Minutes from the open at which to sample, per bucket. The open bucket starts at
# minute 1 rather than 0: the very first second of the session is the opening
# auction unwinding, where quotes read 17 to 21 bps per leg and describe the
# auction rather than the continuous market a backtest trades in.
SAMPLE_MINUTES = {
    "open": (1, 10, 25),
    "midday": (60, 180, 300),
    "close": (361, 372, 385),
}

SEPARATOR = "|"


def half_spread_bps(rows: list[dict]) -> float | None:
    """Median half-spread of a batch of quotes, in basis points.

    Half, because a market order crossing the spread pays one side of it per leg,
    which is what `slippage_bps` has always meant here. The median rather than
    the mean: a single crossed or stale quote should not set a session's cost.
    """
    values = [
        (row["ap"] - row["bp"]) / ((row["ap"] + row["bp"]) / 2) * 10_000 / 2
        for row in rows or []
        if row.get("ap") and row.get("bp") and row["ap"] > row["bp"] > 0
    ]
    return statistics.median(values) if values else None


def sample_session(
    symbol: str, session: str, request=None, sleep=None
) -> dict[str, float | None]:
    """One median half-spread per time-of-day bucket for one symbol and session.

    A bucket with no usable quote is None rather than zero: an unobserved spread
    is not a free trade.
    """
    from candlebench import alpaca

    request = request or _http
    # Paced at the provider's own limit and retried when it throttles anyway.
    # A table for 50 symbols over 4 sessions is 1,800 requests; the first
    # attempt at 0.05s apart was 1,200 a minute against a 200 budget and died
    # with HTTP 429 partway through.
    sleep = sleep or (lambda seconds: _time.sleep(seconds))
    out: dict[str, float | None] = {}
    for bucket, minutes in SAMPLE_MINUTES.items():
        gathered: list[dict] = []
        for minute in minutes:
            page = alpaca.with_retry(
                request,
                {
                    "symbols": symbol,
                    "start": _stamp(session, minute),
                    "end": _stamp(session, minute, SAMPLE_SECONDS),
                    "limit": 10_000,
                },
                sleep,
            )
            gathered.extend(_rows_for(page, symbol))
            sleep(alpaca.PAGE_INTERVAL_S)
        out[bucket] = half_spread_bps(gathered)
    return out


def build_table(
    symbols, sessions, request=None, sleep=None
) -> dict[str, float]:
    """Median half-spread per symbol and bucket across several sessions.

    Keys are `"SYMBOL|bucket"` so the table is plain JSON. A symbol with no
    usable quote in any session is absent rather than present with a zero.
    """
    gathered: dict[str, list[float]] = {}
    for symbol in symbols:
        for session in sessions:
            for bucket, value in sample_session(
                symbol, session, request=request, sleep=sleep
            ).items():
                if value is not None:
                    gathered.setdefault(f"{symbol}{SEPARATOR}{bucket}", []).append(value)
    return {key: statistics.median(values) for key, values in sorted(gathered.items())}


def lookup(table: dict[str, float], symbol: str, minute: int | None) -> float | None:
    """The half-spread in bps to charge for `symbol` at `minute` of the session.

    An absent bucket falls back to the mean of that symbol's other buckets, which
    is better than nothing and far better than the fixed guess. An absent symbol
    is None, so the caller can fall back deliberately.
    """
    own = {
        key.split(SEPARATOR, 1)[1]: value
        for key, value in table.items()
        if key.startswith(f"{symbol}{SEPARATOR}")
    }
    if not own:
        return None
    if minute is None:
        return own.get("midday", statistics.mean(own.values()))
    bucket = trades.time_bucket(minute)
    if bucket in own:
        return own[bucket]
    return statistics.mean(own.values())


def write_table(table: dict[str, float], path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f"{path.name}.tmp")
    temp.write_text(json.dumps(table, indent=2, sort_keys=True))
    temp.replace(path)
    return path


def read_table(path: str | Path) -> dict[str, float]:
    """The table, or empty when there is none to read."""
    path = Path(path)
    if not path.exists():
        return {}
    try:
        return {k: float(v) for k, v in json.loads(path.read_text()).items()}
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return {}


# ---------- plumbing ----------


def _rows_for(page: dict, symbol: str) -> list[dict]:
    """Quotes for one symbol out of either response shape.

    The multi-symbol endpoint keys by symbol; the single-symbol one returns a
    bare list. Both are accepted so a recorded response can stand in for either.
    """
    found = (page or {}).get("quotes")
    if isinstance(found, dict):
        return found.get(symbol) or []
    return found or []


def _stamp(session: str, minute: int, extra_seconds: int = 0) -> str:
    """UTC timestamp `minute` minutes after the session open.

    09:30 Eastern is 13:30 UTC during daylight time and 14:30 otherwise. The
    offset is taken from the date so a winter session samples the right window
    rather than an hour of pre-market.
    """
    opened = _session_open_utc(session) + timedelta(minutes=minute, seconds=extra_seconds)
    return opened.isoformat().replace("+00:00", "Z")


def _session_open_utc(session: str) -> datetime:
    import zoneinfo

    day = datetime.fromisoformat(session).date()
    eastern = zoneinfo.ZoneInfo("America/New_York")
    local = datetime(day.year, day.month, day.day, 9, 30, tzinfo=eastern)
    return local.astimezone(timezone.utc)


def _minutes_from_open(stamp: str) -> int:
    """Inverse of `_stamp`, used by tests to check where a sample landed."""
    moment = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    session = moment.astimezone(timezone.utc).date().isoformat()
    return int((moment - _session_open_utc(session)).total_seconds() // 60)


def _http(params: dict) -> dict:
    from candlebench import alpaca

    return alpaca._get(QUOTES_URL, params)
