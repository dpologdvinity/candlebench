"""Trade-level storage and querying.

The aggregates in `metrics` answer "does this pattern work". They cannot answer
"why", or "is this one ticker", or "is this the opening auction", because those
questions need the individual trades. A full run produces about 43,000 of them,
which is roughly 9.5 MiB as JSON against the 97 KiB of the aggregate report, so
they are written to Parquet and served a page at a time rather than embedded in
the report payload.

`session` is stored as an ISO string rather than a date or a timestamp. It is
the first key every chronological ordering sorts on, and an ISO string sorts
chronologically while surviving a Parquet round trip without a dtype to argue
about.
"""

from __future__ import annotations

from dataclasses import fields
from pathlib import Path

import numpy as np
import pandas as pd

from candlebench.engine import CHRONOLOGICAL, Trade

# Column order of the trade frame: every `Trade` field, then the one derived
# value worth storing rather than recomputing at each reader.
COLUMNS: tuple[str, ...] = tuple(f.name for f in fields(Trade)) + ("bars_held",)

# Dtypes are pinned so an empty frame and a populated one are the same shape.
# Without this, `to_frame([])` would produce all-object columns and a later
# concat or comparison against a real frame would fail on dtype rather than on
# content.
_DTYPES: dict[str, str] = {
    "pattern": "object",
    "interval": "object",
    "symbol": "object",
    "session": "object",
    "trial_index": "int64",
    "direction": "int64",
    "entry_index": "int64",
    "exit_index": "int64",
    "entry_price": "float64",
    "exit_price": "float64",
    "stop_price": "float64",
    "target_price": "float64",
    "risk_per_share": "float64",
    "exit_reason": "object",
    "gross_r": "float64",
    "net_r": "float64",
    "return_pct": "float64",
    "window": "int64",
    # Nullable, because a caller that supplied no bar clock records no minute.
    "entry_minute": "Int64",
    "bars_held": "int64",
}


def to_frame(rows: list[Trade]) -> pd.DataFrame:
    """One row per trade, in `COLUMNS` order with pinned dtypes."""
    if not rows:
        return pd.DataFrame({name: pd.Series(dtype=_DTYPES[name]) for name in COLUMNS})

    frame = pd.DataFrame(
        {
            name: [
                _cell(getattr(trade, name), name)
                for trade in rows
            ]
            for name in COLUMNS
        }
    )
    return frame.astype(_DTYPES)


def _cell(value, name: str):
    return value.isoformat() if name == "session" else value


def write(rows: list[Trade], path: str | Path) -> Path:
    """Write the trade frame to Parquet, creating the directory if needed."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    to_frame(rows).to_parquet(path, index=False)
    return path


def read(path: str | Path) -> pd.DataFrame:
    """Read a trade frame back, normalised to `COLUMNS` order and dtypes."""
    frame = pd.read_parquet(path)
    missing = [name for name in COLUMNS if name not in frame.columns]
    if missing:
        raise ValueError(f"trade file {path} is missing column(s): {', '.join(missing)}")
    return frame[list(COLUMNS)].astype(_DTYPES)


# A regular session runs 09:30 to 16:00, so 390 minutes. "open" is the first 30
# of them and "close" the last 30 — the stretches where auction effects are the
# usual explanation for an intraday pattern appearing to work.
SESSION_MINUTES = 390
EDGE_MINUTES = 30
TIME_BUCKETS = ("open", "midday", "close")

BREAKDOWNS = ("symbol", "time_of_day", "window", "exit_reason")


def chronological(frame: pd.DataFrame) -> pd.DataFrame:
    """Sort trades into the order they were traded in.

    Shares `engine.CHRONOLOGICAL` with `metrics._max_drawdown_r`, so the two
    orderings cannot drift apart.
    """
    return frame.sort_values(list(CHRONOLOGICAL), kind="stable")


def time_bucket(minute) -> str:
    """Which part of the session a minute falls in."""
    if minute is None or pd.isna(minute):
        return "unknown"
    if minute < EDGE_MINUTES:
        return "open"
    if minute >= SESSION_MINUTES - EDGE_MINUTES:
        return "close"
    return "midday"


def _group_keys(frame: pd.DataFrame, by: str) -> pd.Series:
    if by == "time_of_day":
        return frame["entry_minute"].map(time_bucket)
    return frame[by]


def _bucket_order(by: str, keys) -> list:
    """Session order for time buckets, sorted order for everything else."""
    if by != "time_of_day":
        return sorted(keys)
    known = [b for b in TIME_BUCKETS if b in keys]
    return known + (["unknown"] if "unknown" in keys else [])


def breakdown(
    frame: pd.DataFrame,
    by: str,
    *,
    pattern: str | None = None,
    interval: str | None = None,
) -> list[dict]:
    """Aggregate trades by one grouping key.

    An aggregate over every trade hides the two explanations that most often
    account for an apparent intraday edge: one ticker carrying the whole result,
    and the opening auction. Neither is visible without grouping.
    """
    if by not in BREAKDOWNS:
        raise ValueError(
            f"cannot break down by {by!r}. valid: {', '.join(BREAKDOWNS)}"
        )

    selected = query(frame, pattern=pattern, interval=interval)
    if selected.empty:
        return []

    keys = _group_keys(selected, by)
    out = []
    for key in _bucket_order(by, set(keys)):
        group = selected[keys == key]
        r = group["net_r"].to_numpy()
        reasons = group["exit_reason"]
        out.append({
            "key": key,
            "trades": int(len(group)),
            "win_rate": float((r > 0).mean()),
            "expectancy_r": float(r.mean()),
            "total_r": float(r.sum()),
            "exit_mix": {
                reason: count / len(group)
                for reason, count in sorted(reasons.value_counts().items())
            },
        })
    return out


def equity_curve(frame: pd.DataFrame, pattern: str, interval: str | None = None) -> dict:
    """Cumulative net R over one pattern's trades, in the order they happened.

    `max_drawdown_r` is computed from this very series rather than recomputed
    independently, so the deepest decline the chart shows is the number the
    leaderboard reports.
    """
    selected = chronological(query(frame, pattern=pattern, interval=interval))
    if selected.empty:
        return {
            "pattern": pattern,
            "interval": interval,
            "trades": 0,
            "points": [],
            "max_drawdown_r": None,
            "first_session": None,
            "last_session": None,
        }

    equity = selected["net_r"].cumsum().to_numpy()
    peak = np.maximum.accumulate(equity)
    return {
        "pattern": pattern,
        "interval": interval,
        "trades": int(len(selected)),
        "points": [float(v) for v in equity],
        "max_drawdown_r": float(np.max(peak - equity)),
        "first_session": str(selected["session"].iloc[0]),
        "last_session": str(selected["session"].iloc[-1]),
    }


def query(
    frame: pd.DataFrame,
    *,
    pattern: str | None = None,
    interval: str | None = None,
    symbol: str | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> pd.DataFrame:
    """Filter and page a trade frame. The frame passed in is never modified."""
    mask = pd.Series(True, index=frame.index)
    for column, value in (
        ("pattern", pattern),
        ("interval", interval),
        ("symbol", symbol),
    ):
        if value is not None:
            mask &= frame[column] == value

    selected = frame[mask]
    stop = None if limit is None else offset + limit
    return selected.iloc[offset:stop]
