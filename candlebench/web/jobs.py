"""Background job state for the web server.

A run over 200 trials and five intervals takes minutes, so the browser cannot
wait on a request. One job runs at a time: a second concurrent run would
compete for the same bar cache and produce two reports whose provenance nobody
could later reconstruct.

The last completed run is persisted, so reopening the page shows results
instead of an empty table.
"""

from __future__ import annotations

import json
import threading
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from candlebench import trades as trade_store


@dataclass
class JobResult:
    """What a finished job publishes.

    The trades are kept out of `payload` deliberately. `payload` is the same
    structure the JSON report and the page both read, and a full run's trades
    are roughly a hundred times its size, so they travel to Parquet instead.
    """

    payload: dict
    trades: list | None = None


@dataclass
class JobState:
    """What the browser polls while a run or fetch is in flight."""

    kind: str = "idle"  # idle | run | fetch
    status: str = "idle"  # idle | working | done | error
    message: str = ""
    done: int = 0
    total: int = 0
    error: str = ""

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "status": self.status,
            "message": self.message,
            "done": self.done,
            "total": self.total,
            "percent": round(100 * self.done / self.total, 1) if self.total else 0.0,
            "error": self.error,
        }


class JobRunner:
    """Serialises background work and holds the latest result."""

    def __init__(
        self, results_path: Path, trades_path: Path | None = None, history=None
    ):
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._state = JobState()
        self._results: dict | None = None
        self._results_path = Path(results_path)
        self.history = history
        self._trades_path = Path(trades_path) if trades_path else self._results_path.with_name(
            f"{self._results_path.stem}_trades.parquet"
        )
        self._load_persisted()

    @property
    def trades_path(self) -> Path:
        return self._trades_path

    def trades_frame(self):
        """The last run's trades, or None when no run has written any.

        Read from disk on each call rather than held in memory: the frame is
        several MiB, every reader wants a different slice of it, and a stale
        copy that disagreed with the report would be worse than a re-read.
        """
        if not self._trades_path.exists():
            return None
        try:
            return trade_store.read(self._trades_path)
        except (OSError, ValueError):
            return None

    def _load_persisted(self) -> None:
        if not self._results_path.exists():
            return
        try:
            self._results = json.loads(self._results_path.read_text())
        except (OSError, json.JSONDecodeError):
            # A truncated file from an interrupted write is not worth failing
            # startup over; the user can simply run again.
            self._results = None

    @property
    def state(self) -> dict:
        with self._lock:
            return self._state.as_dict()

    @property
    def results(self) -> dict | None:
        with self._lock:
            return self._results

    def busy(self) -> bool:
        with self._lock:
            return self._state.status == "working"

    def submit(self, kind: str, work: Callable[[JobState], object]) -> bool:
        """Start `work` in a thread. Returns False if a job is already running.

        `work` receives the live JobState so it can report progress, and returns
        a `JobResult`, a bare payload dict, or None for work that produces no
        report of its own.
        """
        with self._lock:
            if self._state.status == "working":
                return False
            self._state = JobState(kind=kind, status="working", message="starting")

        def target() -> None:
            try:
                outcome = work(self._state)
                payload = outcome.payload if isinstance(outcome, JobResult) else outcome
                if isinstance(outcome, JobResult) and outcome.trades is not None:
                    trade_store.write(outcome.trades, self._trades_path)
                    # Archived as well as published, so two runs can be compared
                    # later. The latest result stays where it was; the history is
                    # an addition, not a replacement.
                    if self.history is not None:
                        self.history.save(outcome.payload, outcome.trades)
            except Exception as exc:
                with self._lock:
                    self._state.status = "error"
                    self._state.error = f"{type(exc).__name__}: {exc}"
                    self._state.message = "failed"
                traceback.print_exc()
                return

            with self._lock:
                if payload is not None:
                    self._results = payload
                    self._persist(payload)
                self._state.status = "done"
                self._state.message = "complete"
                self._state.done = self._state.total

        self._thread = threading.Thread(target=target, daemon=True)
        self._thread.start()
        return True

    def _persist(self, payload: dict) -> None:
        """Write via a temporary file so a crash cannot leave a partial result."""
        self._results_path.parent.mkdir(parents=True, exist_ok=True)
        temp = self._results_path.with_suffix(".tmp")
        temp.write_text(json.dumps(payload, default=str))
        temp.replace(self._results_path)
