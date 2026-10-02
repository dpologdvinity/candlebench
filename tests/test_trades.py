"""Trade-level persistence.

Aggregates answer "does this pattern work"; individual trades answer "why", and
until now they were thrown away at the end of `runner.run`. These tests pin the
round trip, because a trade frame that silently loses a field or changes a dtype
would make every breakdown built on it quietly wrong.
"""

from __future__ import annotations

import pathlib
from dataclasses import replace

import pandas as pd
import pytest

from candlebench import bars, runner, trades
from candlebench.engine import Trade
from tests.test_end_to_end import config, write_cache


def make_trade(**overrides) -> Trade:
    base = dict(
        pattern="hammer",
        interval="1m",
        symbol="AAA",
        session=pd.Timestamp("2026-09-14").date(),
        trial_index=0,
        window=0,
        direction=1,
        entry_index=12,
        exit_index=18,
        entry_price=100.0,
        exit_price=102.0,
        stop_price=99.0,
        target_price=102.0,
        risk_per_share=1.0,
        exit_reason="target",
        gross_r=2.0,
        net_r=1.9,
        return_pct=0.02,
        entry_minute=12,
    )
    return Trade(**{**base, **overrides})


# ---------- the frame ----------


def test_an_empty_list_still_produces_the_full_column_set():
    """A pattern with no trades must not produce a frame nothing can read."""
    frame = trades.to_frame([])
    assert list(frame.columns) == list(trades.COLUMNS)
    assert len(frame) == 0


def test_every_trade_field_reaches_the_frame():
    frame = trades.to_frame([make_trade()])
    assert len(frame) == 1
    row = frame.iloc[0]
    assert row["pattern"] == "hammer"
    assert row["session"] == "2026-09-14"
    assert row["bars_held"] == 6
    assert row["entry_minute"] == 12
    assert row["net_r"] == pytest.approx(1.9)
    for field in Trade.__dataclass_fields__:
        assert field in frame.columns


def test_the_session_is_an_iso_string_so_sorting_is_chronological():
    rows = [
        make_trade(session=pd.Timestamp("2026-10-02").date()),
        make_trade(session=pd.Timestamp("2026-09-30").date()),
    ]
    frame = trades.to_frame(rows).sort_values("session")
    assert list(frame["session"]) == ["2026-09-30", "2026-10-02"]


def test_a_parquet_round_trip_preserves_every_value(tmp_path):
    rows = [make_trade(), make_trade(pattern="shooting_star", direction=-1, net_r=-1.0)]
    path = tmp_path / "t.parquet"
    trades.write(rows, path)
    back = trades.read(path)
    pd.testing.assert_frame_equal(back, trades.to_frame(rows))


def test_an_empty_frame_round_trips_too(tmp_path):
    path = tmp_path / "empty.parquet"
    trades.write([], path)
    assert list(trades.read(path).columns) == list(trades.COLUMNS)


# ---------- querying ----------


@pytest.fixture
def frame():
    return trades.to_frame(
        [
            make_trade(pattern="hammer", interval="1m", symbol="AAA"),
            make_trade(pattern="hammer", interval="5m", symbol="BBB"),
            make_trade(pattern="shooting_star", interval="1m", symbol="AAA"),
            make_trade(pattern="shooting_star", interval="1m", symbol="BBB"),
        ]
    )


@pytest.mark.parametrize(
    "kwargs,expected",
    [
        ({"pattern": "hammer"}, 2),
        ({"interval": "1m"}, 3),
        ({"symbol": "AAA"}, 2),
        ({"pattern": "hammer", "interval": "1m"}, 1),
        ({"pattern": "nothing_here"}, 0),
    ],
)
def test_query_filters_by_each_key(frame, kwargs, expected):
    assert len(trades.query(frame, **kwargs)) == expected


def test_query_pages_with_limit_and_offset(frame):
    assert len(trades.query(frame, limit=2)) == 2
    assert len(trades.query(frame, limit=2, offset=3)) == 1
    assert len(trades.query(frame, offset=99)) == 0


def test_query_leaves_the_frame_it_was_given_alone(frame):
    before = len(frame)
    trades.query(frame, pattern="hammer", limit=1)
    assert len(frame) == before


# ---------- the runner keeps them ----------


def test_the_runner_returns_the_trades_it_measured(tmp_path):
    write_cache(tmp_path)
    result = runner.run(config(tmp_path))
    assert result.trades
    per_interval = sum(s.trades for s in result.stats if s.interval != runner.POOLED)
    assert len(result.trades) == per_interval


def test_the_kept_trades_cover_every_interval_that_ran(tmp_path):
    write_cache(tmp_path, intervals=("1m", "5m"))
    cfg = config(tmp_path)
    cfg = replace(cfg, run=replace(cfg.run, intervals=("1m", "5m")))
    result = runner.run(cfg)
    assert {t.interval for t in result.trades} == {"1m", "5m"}


# ---------- clock time, not bar index ----------


def test_minutes_from_open_counts_from_the_session_open(tmp_path):
    write_cache(tmp_path, intervals=("5m",))
    frame = bars.sessions(bars.load("AAA", "5m", tmp_path))
    session = sorted(frame)[0]
    minutes = bars.minutes_from_open(frame[session])
    assert minutes[0] == 0
    assert minutes[1] == 5
    assert minutes.dtype.kind == "i"


def test_a_trade_records_the_clock_minute_of_its_entry(tmp_path):
    """Bar index cannot stand in: dropped bars break index-times-interval."""
    write_cache(tmp_path)
    result = runner.run(config(tmp_path))
    measured = [t for t in result.trades if t.entry_minute is not None]
    assert measured
    # The synthetic cache is contiguous at 1m from the open, so the clock minute
    # and the bar index coincide. A gap would separate them, which is the point.
    assert all(t.entry_minute == t.entry_index for t in measured)


# ---------- a failed write must not destroy the last good one ----------


def test_a_failed_write_leaves_the_previous_trade_file_intact(tmp_path, monkeypatch):
    """A write that dies part way must not take the last good file with it.

    `to_parquet` truncates its target as it opens it, so writing straight to the
    destination means an interrupted write — out of disk, a killed process —
    destroys the previous run's trades, which were readable a moment earlier.
    `jobs._persist` already routes the JSON report through a temporary file for
    exactly this reason; the trade file has to do the same.

    The failure is modelled where it really happens: part of the file is on disk
    before the error, not before the open.
    """
    path = tmp_path / "t.parquet"
    trades.write([make_trade(net_r=1.0)], path)
    good = trades.read(path)

    real = pd.DataFrame.to_parquet

    def die_part_way(self, target, *args, **kwargs):
        pathlib.Path(target).write_bytes(b"PAR1 partial and unreadable")
        raise OSError("No space left on device")

    monkeypatch.setattr(pd.DataFrame, "to_parquet", die_part_way)
    with pytest.raises(OSError):
        trades.write([make_trade(net_r=2.0), make_trade(net_r=3.0)], path)

    monkeypatch.setattr(pd.DataFrame, "to_parquet", real)
    pd.testing.assert_frame_equal(trades.read(path), good)


def test_a_write_leaves_no_temporary_file_behind(tmp_path):
    trades.write([make_trade()], tmp_path / "t.parquet")
    assert [p.name for p in tmp_path.iterdir()] == ["t.parquet"]
