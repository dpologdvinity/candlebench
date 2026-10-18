"""`TradeTable`, the columnar form a run keeps its trades in.

A run's statistics, its trade file and its dashboard all read trades from
these columns, and the tests and small selections read them back as `Trade`
objects. Each rule here is a place the two forms could quietly disagree.
"""

from __future__ import annotations

from dataclasses import fields
from datetime import date

import numpy as np
import pytest

from candlebench import engine, metrics, trades
from candlebench.engine import Trade, TradeTable


def make(**overrides) -> Trade:
    values = {
        "pattern": "hammer", "interval": "1m", "symbol": "AAA", "session": date(2026, 3, 2),
        "trial_index": 0, "direction": 1, "entry_index": 5, "exit_index": 9,
        "entry_price": 100.0, "exit_price": 101.0, "stop_price": 99.0, "target_price": 102.0,
        "risk_per_share": 1.0, "exit_reason": "timeout", "gross_r": 1.0, "net_r": 0.9,
        "return_pct": 0.009,
    }
    values.update(overrides)
    return Trade(**values)


def varied() -> list[Trade]:
    return [
        make(),
        make(symbol="BBB", entry_minute=12, cost_bps=1.25, sample="validation", window=2),
        make(session=date(2025, 12, 31), entry_minute=None, cost_bps=None, matched=True),
        make(interval="5m", exit_reason="stop", net_r=-1.1, gross_r=-1.0, direction=-1),
    ]


def test_the_table_has_every_trade_field_in_trade_order():
    """`to_frame` and `rows` both rely on the two field lists matching."""
    assert [f.name for f in fields(TradeTable)] == [f.name for f in fields(Trade)]


def test_trades_survive_a_round_trip_through_a_table_unchanged():
    """Including a missing minute and cost, which the table holds as NaN."""
    original = varied()
    assert TradeTable.from_trades(original).rows() == original


def test_a_table_iterates_and_indexes_as_trade_objects():
    original = varied()
    table = TradeTable.from_trades(original)
    assert list(table) == original
    assert table[1] == original[1]
    assert table[-1] == original[-1]
    assert len(table) == len(original)


def test_concat_keeps_row_order_and_accepts_trade_lists():
    original = varied()
    joined = TradeTable.concat([TradeTable.from_trades(original[:2]), [], original[2:]])
    assert joined.rows() == original
    assert len(TradeTable.concat([])) == 0


def test_chronological_order_agrees_with_the_sort_key_on_objects():
    """Drawdown is measured along this order and the browser draws the curve
    along `trades.chronological`; if the two disagreed, so would the numbers.
    An unknown minute sorts last, and ties keep their row order."""
    rng = np.random.default_rng(4)
    original = [
        make(session=date(2026, 3, int(rng.integers(1, 4))),
             entry_minute=None if rng.random() < 0.2 else int(rng.integers(0, 5)),
             symbol=str(rng.choice(["AAA", "BBB", "aaa", "Z"])),
             interval=str(rng.choice(["1m", "5m", "15m"])),
             entry_index=int(rng.integers(0, 3)), net_r=float(i))
        for i in range(300)
    ]
    table = TradeTable.from_trades(original)
    expected = sorted(original, key=engine.chronological_key)
    assert table.take(table.chronological_order()).rows() == expected


def test_date_totals_add_in_row_order_like_a_loop():
    """`bincount` must give the same float sums as adding one trade at a
    time, or every bootstrap draw downstream would move."""
    rng = np.random.default_rng(9)
    days = [date(2026, 1, d) for d in range(1, 8)]
    original = [make(session=days[int(rng.integers(0, 7))], net_r=float(rng.normal(0, 3)))
                for _ in range(2000)]
    expected = np.zeros(len(days))
    for t in original:
        expected[days.index(t.session)] += t.net_r
    sums, counts = metrics._date_totals(TradeTable.from_trades(original), days)  # pylint: disable=protected-access
    assert sums.tolist() == expected.tolist()
    assert counts.sum() == len(original)


def test_a_trade_on_a_date_outside_the_list_is_an_error_not_a_misfiled_sum():
    with pytest.raises(KeyError):
        metrics._date_totals([make(session=date(2026, 5, 5))], [date(2026, 5, 4)])  # pylint: disable=protected-access


def test_the_trade_frame_is_the_same_from_a_table_or_from_objects():
    """The frame is what the dashboard and the static site read."""
    original = varied()
    from_table = trades.to_frame(TradeTable.from_trades(original))
    assert from_table.equals(trades.to_frame(original))
    assert from_table["entry_minute"].isna().tolist() == [True, False, True, True]
    assert from_table["session"].tolist()[2] == "2025-12-31"


def test_tables_compare_by_content_as_trade_lists_did():
    """Comparing two runs' trades checks determinism; identity comparison
    would call two identical runs different. NaN equals NaN here, as None
    equalled None in the lists."""
    original = varied()
    assert TradeTable.from_trades(original) == TradeTable.from_trades(original)
    changed = original[:3] + [make(net_r=0.5)]
    assert TradeTable.from_trades(original) != TradeTable.from_trades(changed)


def test_a_slice_of_a_table_is_a_table_of_those_rows():
    original = varied()
    assert TradeTable.from_trades(original)[1:3].rows() == original[1:3]
