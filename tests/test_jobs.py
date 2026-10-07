"""Publishing a finished run.

A run's report and its trades are one result. If saving either fails, the page
must keep showing the previous complete run, say that the save failed, and
accept the next run — not serve numbers that were never written, or wait forever
on a job that died after its work was done.
"""

from __future__ import annotations

import time

import pytest

from candlebench import trades as trade_store
from candlebench.web.history import History
from candlebench.web.jobs import JobResult, JobRunner
from tests.test_trades import make_trade


def wait(jobs: JobRunner, timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = jobs.state
        if state["status"] != "working":
            return state
        time.sleep(0.01)
    raise AssertionError("job never left 'working'")


def run(jobs: JobRunner, label: str, symbol: str) -> dict:
    assert jobs.submit("run", lambda state: JobResult({"label": label}, [make_trade(symbol=symbol)]))
    return wait(jobs)


@pytest.fixture
def jobs(tmp_path):
    return JobRunner(tmp_path / "last_run.json", history=History(tmp_path / "runs"))


def test_a_completed_run_publishes_its_report_and_trades_together(jobs):
    assert run(jobs, "first", "AAA")["status"] == "done"
    assert jobs.results == {"label": "first"}
    assert set(jobs.trades_frame()["symbol"]) == {"AAA"}
    assert len(jobs.history.summaries()) == 1


@pytest.mark.parametrize("failing", ["report", "trades", "history"])
def test_a_failed_save_keeps_the_previous_run_and_frees_the_runner(jobs, monkeypatch, failing):
    """A save that raised used to leave the job 'working' with an unsaved report served.

    Every later run was refused with 409 until the server restarted, and the
    latest trades could already belong to the run whose report never landed.
    """
    run(jobs, "first", "AAA")

    def boom(*_args, **_kwargs):
        raise OSError("disk full")

    if failing == "report":
        monkeypatch.setattr(jobs, "_persist", boom)
    elif failing == "trades":
        monkeypatch.setattr(trade_store, "write", boom)
    else:
        monkeypatch.setattr(jobs.history, "save", boom)

    state = run(jobs, "second", "BBB")
    assert state["status"] == "error"
    assert "disk full" in state["error"]
    assert jobs.results == {"label": "first"}
    assert set(jobs.trades_frame()["symbol"]) == {"AAA"}
    assert JobRunner(jobs._results_path).results == {"label": "first"}

    monkeypatch.undo()
    assert run(jobs, "third", "CCC")["status"] == "done"
    assert jobs.results == {"label": "third"}
    assert set(jobs.trades_frame()["symbol"]) == {"CCC"}


def test_no_staging_file_is_left_behind_after_a_failed_save(jobs, monkeypatch, tmp_path):
    run(jobs, "first", "AAA")
    monkeypatch.setattr(jobs, "_persist", lambda *_: (_ for _ in ()).throw(OSError("x")))
    run(jobs, "second", "BBB")
    leftovers = [p.name for p in tmp_path.iterdir() if "staged" in p.name or p.suffix == ".tmp"]
    assert leftovers == []


def test_a_history_report_is_written_atomically(tmp_path, monkeypatch):
    """A history report written in place and interrupted would list a run with no readable report."""
    history = History(tmp_path / "runs")
    history.save({"x": 1}, [make_trade()])
    assert not list((tmp_path / "runs").glob("*.tmp"))
