"""Breakdowns and the equity curve.

An aggregate hides the two explanations that most often account for an apparent
intraday edge: one ticker carrying the whole result, and the opening auction.
Both are visible only per group, which is why these exist.

The equity curve has a second job beyond being drawn. It must order trades by
exactly the rule `metrics._max_drawdown_r` uses, because a curve whose visible
worst drawdown disagreed with the reported `max_drawdown_r` would undermine both
numbers. One test here asserts the two agree.
"""

from __future__ import annotations

import pandas as pd
import pytest

from candlebench import metrics, trades
from tests.test_trades import make_trade


def frame_of(rows):
    return trades.to_frame(rows)


@pytest.fixture
def mixed():
    """Two symbols with opposite outcomes, spread across the session."""
    return frame_of(
        [
            make_trade(symbol="AAA", net_r=2.0, entry_minute=5, exit_reason="target"),
            make_trade(symbol="AAA", net_r=2.0, entry_minute=200, exit_reason="target"),
            make_trade(symbol="AAA", net_r=2.0, entry_minute=380, exit_reason="target"),
            make_trade(symbol="BBB", net_r=-1.0, entry_minute=10, exit_reason="stop"),
            make_trade(symbol="BBB", net_r=-1.0, entry_minute=300, exit_reason="stop"),
        ]
    )


# ---------- by symbol ----------


def test_a_breakdown_by_symbol_separates_the_winner_from_the_loser(mixed):
    """The question an aggregate cannot answer: is this edge one ticker?"""
    rows = {r["key"]: r for r in trades.breakdown(mixed, "symbol")}
    assert set(rows) == {"AAA", "BBB"}
    assert rows["AAA"]["expectancy_r"] == pytest.approx(2.0)
    assert rows["AAA"]["trades"] == 3
    assert rows["AAA"]["win_rate"] == pytest.approx(1.0)
    assert rows["BBB"]["expectancy_r"] == pytest.approx(-1.0)
    assert rows["BBB"]["total_r"] == pytest.approx(-2.0)


def test_each_breakdown_row_carries_its_own_exit_mix(mixed):
    rows = {r["key"]: r for r in trades.breakdown(mixed, "symbol")}
    assert rows["AAA"]["exit_mix"] == {"target": pytest.approx(1.0)}
    assert rows["BBB"]["exit_mix"] == {"stop": pytest.approx(1.0)}


def test_a_breakdown_is_sorted_by_its_key(mixed):
    assert [r["key"] for r in trades.breakdown(mixed, "symbol")] == ["AAA", "BBB"]


# ---------- by time of day ----------


@pytest.mark.parametrize(
    "minute,bucket",
    [
        (0, "open"), (29, "open"), (30, "midday"),
        (359, "midday"), (360, "close"), (389, "close"),
    ],
)
def test_the_time_of_day_boundaries_land_where_they_are_documented(minute, bucket):
    """A 390-minute session: the first 30 minutes and the last 30."""
    assert trades.time_bucket(minute) == bucket


def test_a_breakdown_by_time_of_day_groups_the_open_separately(mixed):
    rows = {r["key"]: r for r in trades.breakdown(mixed, "time_of_day")}
    assert set(rows) == {"open", "midday", "close"}
    assert rows["open"]["trades"] == 2  # minutes 5 and 10
    assert rows["close"]["trades"] == 1  # minute 380
    assert rows["midday"]["trades"] == 2


def test_the_buckets_come_back_in_session_order_not_alphabetical(mixed):
    assert [r["key"] for r in trades.breakdown(mixed, "time_of_day")] == list(
        trades.TIME_BUCKETS
    )


def test_a_trade_with_no_recorded_minute_is_labelled_not_guessed(mixed):
    rows = frame_of([make_trade(entry_minute=None)])
    assert trades.breakdown(rows, "time_of_day")[0]["key"] == "unknown"


# ---------- other keys ----------


def test_a_breakdown_by_exit_reason_counts_each_reason(mixed):
    rows = {r["key"]: r for r in trades.breakdown(mixed, "exit_reason")}
    assert rows["target"]["trades"] == 3
    assert rows["stop"]["trades"] == 2


def test_a_breakdown_by_window_reports_each_period(mixed):
    rows = frame_of(
        [make_trade(window=0, net_r=1.0), make_trade(window=1, net_r=-1.0)]
    )
    out = {r["key"]: r for r in trades.breakdown(rows, "window")}
    assert out[0]["expectancy_r"] == pytest.approx(1.0)
    assert out[1]["expectancy_r"] == pytest.approx(-1.0)


def test_an_unknown_breakdown_key_is_refused(mixed):
    with pytest.raises(ValueError, match="cannot break down by"):
        trades.breakdown(mixed, "phase_of_the_moon")


def test_a_breakdown_can_be_filtered_to_one_pattern_and_interval(mixed):
    rows = frame_of(
        [
            make_trade(pattern="hammer", interval="1m", symbol="AAA", net_r=1.0),
            make_trade(pattern="hammer", interval="5m", symbol="BBB", net_r=1.0),
            make_trade(pattern="shooting_star", interval="1m", symbol="CCC", net_r=1.0),
        ]
    )
    out = trades.breakdown(rows, "symbol", pattern="hammer", interval="1m")
    assert [r["key"] for r in out] == ["AAA"]


def test_an_empty_selection_breaks_down_to_nothing(mixed):
    assert trades.breakdown(mixed, "symbol", pattern="no_such_pattern") == []


# ---------- the equity curve ----------


def ordered_rows():
    """Trades deliberately out of chronological order in the frame."""
    return [
        make_trade(session=pd.Timestamp("2026-09-16").date(), entry_index=5, net_r=-3.0),
        make_trade(session=pd.Timestamp("2026-09-14").date(), entry_index=9, net_r=1.0),
        make_trade(session=pd.Timestamp("2026-09-14").date(), entry_index=2, net_r=4.0),
        make_trade(session=pd.Timestamp("2026-09-15").date(), entry_index=1, net_r=-2.0),
    ]


def test_the_curve_is_ordered_chronologically_whatever_the_frame_order():
    curve = trades.equity_curve(frame_of(ordered_rows()), "hammer", "1m")
    # 4.0, then +1.0, then -2.0, then -3.0
    assert curve["points"] == [
        pytest.approx(4.0), pytest.approx(5.0), pytest.approx(3.0), pytest.approx(0.0)
    ]


def test_the_curve_and_the_reported_drawdown_cannot_disagree():
    """Two code paths, one ordering rule. This is the assertion that binds them."""
    rows = ordered_rows()
    curve = trades.equity_curve(frame_of(rows), "hammer", "1m")
    assert curve["max_drawdown_r"] == pytest.approx(metrics._max_drawdown_r(rows))
    assert curve["max_drawdown_r"] == pytest.approx(5.0)


def test_the_curve_reports_the_period_it_covers():
    curve = trades.equity_curve(frame_of(ordered_rows()), "hammer", "1m")
    assert curve["trades"] == 4
    assert curve["first_session"] == "2026-09-14"
    assert curve["last_session"] == "2026-09-16"


def test_a_pattern_with_no_trades_has_an_empty_curve_not_a_zero_one():
    curve = trades.equity_curve(frame_of(ordered_rows()), "evening_star", "1m")
    assert curve["points"] == []
    assert curve["trades"] == 0
    assert curve["max_drawdown_r"] is None
