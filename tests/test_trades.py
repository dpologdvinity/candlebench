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


# ---------- the dtype map must keep up with Trade ----------


def test_a_column_with_no_pinned_dtype_is_refused_loudly():
    """A field added to `Trade` must not reach the frame with a guessed dtype.

    `COLUMNS` is derived from `Trade`, so a new field appears there
    automatically, while `_DTYPES` is written by hand. The two drifting apart is
    silent on the populated path — pandas infers something — and the inference
    depends on the first run's data. A nullable int read as float64 would make
    every comparison against the stored frame fail somewhere far from the cause.
    """
    gapped = {k: v for k, v in trades._DTYPES.items() if k != "net_r"}
    with pytest.raises(ValueError, match="no pinned dtype.*net_r"):
        trades.check_dtype_coverage(trades.COLUMNS, gapped)


def test_the_shipped_dtype_map_covers_every_column():
    assert trades.check_dtype_coverage(trades.COLUMNS, trades._DTYPES) is None


@pytest.mark.parametrize(
    "returns,expected",
    [([-1.0], 1.0), ([-1.0, -2.0, -3.0], 6.0), ([-2.0, 1.0, 3.0], 2.0), ([1.0, 2.0], 0.0)],
)
def test_equity_drawdown_includes_initial_equity_zero(returns, expected):
    rows = [make_trade(net_r=value, entry_index=i) for i, value in enumerate(returns)]
    curve = trades.equity_curve(trades.to_frame(rows), "hammer", "1m")
    assert curve["max_drawdown_r"] == pytest.approx(expected)
    assert len(curve["points"]) == len(returns)
    assert curve["points"][-1] == pytest.approx(sum(returns))


def test_empty_equity_drawdown_is_unavailable():
    curve = trades.equity_curve(trades.to_frame([]), "hammer")
    assert curve["max_drawdown_r"] is None
    assert curve["points"] == []


def test_pooled_trades_are_ordered_by_clock_time_not_bar_number():
    """Bar 24 at 5m is 10:30; bar 30 at 1m is 10:00. Ordering pooled trades by bar
    index put the later trade first, so the pooled curve and drawdown followed an
    order nobody traded in. The frame and the leaderboard must agree on the fix."""
    from candlebench import metrics

    late = make_trade(interval="5m", entry_index=24, entry_minute=120, net_r=-1.0)
    early = make_trade(interval="1m", entry_index=30, entry_minute=30, net_r=2.0)
    ordered = trades.chronological(trades.to_frame([late, early]))
    assert list(ordered["interval"]) == ["1m", "5m"]
    assert metrics._max_drawdown_r([late, early]) == pytest.approx(1.0)
    curve = trades.equity_curve(trades.to_frame([late, early]), "hammer")
    assert curve["points"] == [2.0, 1.0]
    assert curve["max_drawdown_r"] == pytest.approx(1.0)


def test_a_trade_with_no_clock_minute_sorts_last_in_its_session_on_both_paths():
    from candlebench import metrics

    unknown = make_trade(entry_index=1, entry_minute=None, net_r=-1.0)
    known = make_trade(entry_index=5, entry_minute=5, net_r=1.0)
    assert list(trades.chronological(trades.to_frame([unknown, known]))["net_r"]) == [1.0, -1.0]
    assert metrics._max_drawdown_r([unknown, known]) == pytest.approx(1.0)


def test_matched_controls_never_appear_as_a_patterns_own_trades():
    """They share the pattern's name; every table, breakdown and curve of the
    pattern would otherwise mix its trades with its controls."""
    own = make_trade(net_r=2.0)
    control = make_trade(net_r=-1.0, matched=True)
    frame = trades.to_frame([own, control])
    assert list(trades.query(frame, pattern="hammer")["net_r"]) == [2.0]
    assert list(trades.query(frame, pattern="hammer", matched=True)["net_r"]) == [-1.0]
    assert [row["trades"] for row in trades.breakdown(frame, "symbol", pattern="hammer")] == [1]
    assert trades.equity_curve(frame, "hammer", matched=True)["points"] == [-1.0]


def test_a_run_stores_its_matched_controls_beside_its_trades():
    from candlebench.runner import RunResult

    result = RunResult(stats=[], trials=[], symbols=(), sessions_evaluated=0, skipped_sessions=0,
                       warnings=[], trades=[make_trade()], matched_trades=[make_trade(matched=True)])
    assert [t.matched for t in result.stored_trades] == [False, True]
