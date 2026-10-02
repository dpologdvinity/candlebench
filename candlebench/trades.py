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

import pandas as pd

from candlebench.engine import Trade

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
