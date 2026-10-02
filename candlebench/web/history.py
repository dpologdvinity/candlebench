"""Saved runs, so two of them can be compared.

The one question a single stored result cannot answer is "did changing that
setting matter". Keeping the last few runs makes it answerable: each run is a
report plus its trades, under a timestamp id.

The id becomes a filename, so it is restricted to a timestamp shape and checked
before it is ever joined to a path. That is the same posture `config.SYMBOL_PATTERN`
takes for symbols, and for the same reason — the value arrives from the browser.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from candlebench import trades as trade_store

# `YYYYmmddTHHMMSS`, and nothing else. A run id reaches the filesystem, so the
# check is a whitelist rather than a search for anything suspicious.
RUN_ID = re.compile(r"^\d{8}T\d{6}$")

KEEP = 10


class History:
    """The last `keep` completed runs on disk, newest first."""

    def __init__(self, root: Path, keep: int = KEEP):
        self.root = Path(root)
        self.keep = keep

    # ---------- reading ----------

    def _ids(self) -> list[str]:
        if not self.root.exists():
            return []
        found = [p.stem for p in self.root.glob("*.json") if RUN_ID.match(p.stem)]
        return sorted(found, reverse=True)

    def summaries(self) -> list[dict]:
        """Enough of each run to pick one from a list, newest first."""
        out = []
        for run_id in self._ids():
            payload = self.payload(run_id)
            if payload is None:
                continue
            config = payload.get("config", {})
            run = config.get("run", {})
            stats = payload.get("stats", [])
            out.append({
                "id": run_id,
                "saved_at": _timestamp(run_id),
                "trials": run.get("trials"),
                "seed": run.get("seed"),
                "windows": run.get("windows"),
                "intervals": list(run.get("intervals") or []),
                "patterns": len(config.get("patterns") or []),
                "sessions_evaluated": payload.get("sessions_evaluated"),
                "spread_bps": payload.get("spread_bps"),
                "reward_multiple": (config.get("trade") or {}).get("reward_multiple"),
                "edges": sum(1 for s in stats if s.get("verdict") == "EDGE"),
            })
        return out

    def payload(self, run_id: str) -> dict | None:
        """One saved report, or None when the id names no readable run."""
        path = self._path(run_id, ".json")
        if path is None or not path.exists():
            return None
        try:
            return json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return None

    def trades_frame(self, run_id: str):
        path = self._path(run_id, ".parquet")
        if path is None or not path.exists():
            return None
        try:
            return trade_store.read(path)
        except (OSError, ValueError):
            return None

    def _path(self, run_id: str, suffix: str) -> Path | None:
        if not RUN_ID.match(run_id or ""):
            return None
        return self.root / f"{run_id}{suffix}"

    # ---------- writing ----------

    def save(self, payload: dict, rows: list, now: datetime | None = None) -> str:
        """Store one run and evict anything past `keep`. Returns its id."""
        self.root.mkdir(parents=True, exist_ok=True)
        run_id = self._free_id(now or datetime.now(timezone.utc))
        (self.root / f"{run_id}.json").write_text(json.dumps(payload, default=str))
        trade_store.write(rows or [], self.root / f"{run_id}.parquet")
        self._evict()
        return run_id

    def _free_id(self, now: datetime) -> str:
        """A timestamp id not already taken, and never below one already issued.

        Two runs can finish inside one second, and the second must not overwrite
        the first, so the clock advances until the name is free. It also starts
        past the newest id on disk: eviction frees the oldest names, and reusing a
        freed name would make a newer run sort as an older one.
        """
        from datetime import timedelta

        candidate = now.strftime("%Y%m%dT%H%M%S")
        newest = (self._ids() or [None])[0]
        if newest is not None and newest >= candidate:
            now = _parse(newest) + timedelta(seconds=1)

        while True:
            run_id = now.strftime("%Y%m%dT%H%M%S")
            if not (self.root / f"{run_id}.json").exists():
                return run_id
            now += timedelta(seconds=1)

    def _evict(self) -> None:
        for run_id in self._ids()[self.keep:]:
            for suffix in (".json", ".parquet"):
                (self.root / f"{run_id}{suffix}").unlink(missing_ok=True)


def _parse(run_id: str) -> datetime:
    return datetime.strptime(run_id, "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)


def _timestamp(run_id: str) -> str:
    return _parse(run_id).isoformat()
