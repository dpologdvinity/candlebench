"""Sub-minute bars resampled from raw trades.

Neither provider serves a sub-minute bar: Yahoo has no interval below 1m, and
Alpaca's bar endpoint rejects `1Sec`, `5Sec`, `10Sec` and `30Sec` outright. So a
sub-minute bar has to be built from the trade prints, and the two ways to get
that wrong are both silent.

The first is fabricating bars. An interval with no trades in it has no bar, and
forward-filling the previous close would invent a doji at a price nobody traded —
then a detector would find patterns in geometry that never existed. Empty
intervals are dropped.

The second is trusting every print. Some trade conditions are explicitly
ineligible for the consolidated high and low: an average-price print or a trade
reported out of sequence can carry a price far from the market at that instant,
and including it would put a fake shadow on the bar.
"""

from __future__ import annotations

import pandas as pd
import pytest

from candlebench import ticks


def frame(rows: list[tuple[str, float, float]], conditions=None) -> pd.DataFrame:
    """Raw trades as `fetch_trades` returns them: time, price, size."""
    index = pd.DatetimeIndex([pd.Timestamp(t, tz="UTC") for t, _, _ in rows])
    return pd.DataFrame(
        {
            "price": [p for _, p, _ in rows],
            "size": [s for _, _, s in rows],
            "conditions": conditions if conditions is not None else [[] for _ in rows],
        },
        index=index,
    )


# ---------- the OHLCV arithmetic ----------


def test_one_second_of_trades_becomes_one_bar():
    """Open is the first print, close the last, high and low the extremes."""
    trades = frame([
        ("2026-09-15T14:00:00.100Z", 100.0, 10),
        ("2026-09-15T14:00:00.400Z", 101.5, 5),
        ("2026-09-15T14:00:00.700Z", 99.5, 20),
        ("2026-09-15T14:00:00.900Z", 100.5, 1),
    ])
    out = ticks.resample(trades, 1)
    assert len(out) == 1
    row = out.iloc[0]
    assert row["open"] == 100.0
    assert row["high"] == 101.5
    assert row["low"] == 99.5
    assert row["close"] == 100.5
    assert row["volume"] == 36


def test_trades_split_across_the_boundary_land_in_their_own_bars():
    trades = frame([
        ("2026-09-15T14:00:00.900Z", 100.0, 10),
        ("2026-09-15T14:00:01.000Z", 200.0, 20),
    ])
    out = ticks.resample(trades, 1)
    assert list(out["open"]) == [100.0, 200.0]
    assert list(out["volume"]) == [10, 20]


@pytest.mark.parametrize("seconds,expected", [(1, 3), (5, 1), (10, 1)])
def test_the_interval_width_decides_how_many_bars(seconds, expected):
    trades = frame([
        ("2026-09-15T14:00:00.000Z", 100.0, 1),
        ("2026-09-15T14:00:01.000Z", 101.0, 1),
        ("2026-09-15T14:00:02.000Z", 102.0, 1),
    ])
    assert len(ticks.resample(trades, seconds)) == expected


def test_the_bars_carry_the_cache_column_names_and_order():
    from candlebench import bars as bars_module

    out = ticks.resample(frame([("2026-09-15T14:00:00Z", 100.0, 1)]), 1)
    assert list(out.columns) == list(bars_module.BAR_COLUMNS)
    assert str(out.index.tz) == "UTC"


# ---------- what must not be invented ----------


def test_an_interval_with_no_trades_has_no_bar():
    """A forward-filled bar is a fabricated bar.

    Carrying the previous close forward would produce a doji at a price nobody
    traded, and every detector in this project measures exactly that geometry.
    A gap in the series is the truth: nothing traded.
    """
    trades = frame([
        ("2026-09-15T14:00:00.000Z", 100.0, 1),
        ("2026-09-15T14:00:05.000Z", 101.0, 1),   # four seconds with no prints
    ])
    out = ticks.resample(trades, 1)
    assert len(out) == 2
    gap = (out.index[1] - out.index[0]).total_seconds()
    assert gap == 5


def test_no_bar_has_zero_volume():
    """`bars.validate` drops zero-volume bars, so emitting them is pure waste."""
    trades = frame([
        ("2026-09-15T14:00:00.000Z", 100.0, 1),
        ("2026-09-15T14:00:09.000Z", 101.0, 1),
    ])
    out = ticks.resample(trades, 1)
    assert (out["volume"] > 0).all()


def test_an_empty_trade_frame_resamples_to_an_empty_bar_frame():
    from candlebench import bars as bars_module

    out = ticks.resample(frame([]), 1)
    assert out.empty
    assert list(out.columns) == list(bars_module.BAR_COLUMNS)


def test_the_resampled_bars_survive_validation():
    from candlebench import bars as bars_module

    trades = frame([
        ("2026-09-15T14:00:00.100Z", 100.0, 10),
        ("2026-09-15T14:00:00.400Z", 101.5, 5),
        ("2026-09-15T14:00:01.400Z", 99.5, 20),
    ])
    clean, dropped = bars_module.validate(ticks.resample(trades, 1))
    assert len(clean) == 2
    assert dropped == 0


# ---------- prints that must not set the high or low ----------


def test_a_print_ineligible_for_the_price_does_not_set_the_high():
    """An average-price print is not a price anybody could have traded at.

    Condition `W` is a volume-weighted average price report. Including it would
    put a shadow on the bar reaching a price that never existed in the market at
    that instant, which is precisely the geometry a shadow-based detector reads.
    """
    trades = frame(
        [
            ("2026-09-15T14:00:00.100Z", 100.0, 10),
            ("2026-09-15T14:00:00.200Z", 500.0, 10),   # average-price report
            ("2026-09-15T14:00:00.300Z", 100.5, 10),
        ],
        conditions=[[], ["W"], []],
    )
    out = ticks.resample(trades, 1)
    assert out.iloc[0]["high"] == 100.5


def test_an_ineligible_print_still_counts_toward_volume():
    """Price and volume follow different rules, and the data says so.

    Reproducing the provider's own bars needs every print in the volume and only
    the eligible ones in the price. Filtering both — the obvious implementation,
    written first here — matched volume on 0 of 5 bars and came out 21% light.
    """
    trades = frame(
        [
            ("2026-09-15T14:00:00.100Z", 100.0, 10),
            ("2026-09-15T14:00:00.200Z", 500.0, 10),
            ("2026-09-15T14:00:00.300Z", 100.5, 10),
        ],
        conditions=[[], ["W"], []],
    )
    assert ticks.resample(trades, 1).iloc[0]["volume"] == 30


def test_each_price_excluded_condition_is_named():
    """The exclusion list is a measured decision, not an accident."""
    assert ticks.PRICE_EXCLUDED_CONDITIONS == frozenset({"W", "4", "I", "7", "V"})
    for code in ticks.PRICE_EXCLUDED_CONDITIONS:
        assert isinstance(code, str) and len(code) == 1


def test_an_odd_lot_does_not_set_the_extreme():
    """Odd lots are over half of a liquid name's prints and are not last-sale
    eligible. Excluding them is what took OHLC agreement from 68/75 to 74/75."""
    trades = frame(
        [
            ("2026-09-15T14:00:00.100Z", 100.0, 100),
            ("2026-09-15T14:00:00.200Z", 300.0, 1),    # odd lot, wild price
        ],
        conditions=[[], ["I"]],
    )
    row = ticks.resample(trades, 1).iloc[0]
    assert row["high"] == 100.0
    assert row["volume"] == 101


def test_an_ordinary_print_is_kept():
    trades = frame(
        [("2026-09-15T14:00:00.100Z", 100.0, 10)],
        conditions=[["@", "T"]],
    )
    assert len(ticks.resample(trades, 1)) == 1


def test_trades_arriving_out_of_order_are_sorted_first():
    """Open and close are the first and last by time, not by arrival."""
    trades = frame([
        ("2026-09-15T14:00:00.900Z", 99.0, 1),
        ("2026-09-15T14:00:00.100Z", 100.0, 1),
    ])
    row = ticks.resample(trades, 1).iloc[0]
    assert row["open"] == 100.0
    assert row["close"] == 99.0


# ---------- the intervals this makes possible ----------


def test_each_sub_minute_interval_has_a_width_in_seconds():
    assert ticks.SECONDS == {"1s": 1, "5s": 5, "10s": 10, "30s": 30}


def test_a_minute_interval_is_not_a_tick_interval():
    """1m and above come from the bar endpoint, which is far cheaper."""
    assert "1m" not in ticks.SECONDS
    assert not ticks.is_sub_minute("1m")
    assert ticks.is_sub_minute("5s")


# ---------- fetching, and which source can serve it ----------


def trade_page(rows, nxt=None):
    page = {"trades": rows}
    if nxt:
        page["next_page_token"] = nxt
    return page


def raw(t, p, s, c=None):
    return {"t": t, "p": p, "s": s, "c": c if c is not None else ["@"], "x": "D", "z": "C"}


def test_a_sub_minute_interval_is_fetched_from_trades_and_resampled():
    """The caller asks for bars; whether they came from trades is invisible."""
    from candlebench import alpaca, bars as bars_module

    calls = []

    def request(params):
        calls.append(dict(params))
        return trade_page({"AAPL": [
            raw("2026-09-15T14:00:00.100Z", 100.0, 10),
            raw("2026-09-15T14:00:00.900Z", 101.0, 5),
            raw("2026-09-15T14:00:01.100Z", 99.0, 7),
        ]})

    frame = alpaca.download(
        ["AAPL"], "1s",
        pd.Timestamp("2026-09-15 14:00", tz="UTC").to_pydatetime(),
        pd.Timestamp("2026-09-15 14:01", tz="UTC").to_pydatetime(),
        request=request,
    )
    extracted = bars_module._extract(frame, "AAPL")
    assert len(extracted) == 2
    assert extracted.iloc[0]["open"] == 100.0
    assert extracted.iloc[0]["high"] == 101.0
    assert extracted.iloc[0]["volume"] == 15
    # No bar timeframe is requested: this is the trades endpoint.
    assert "timeframe" not in calls[0]


def test_a_sub_minute_fetch_follows_every_page():
    from candlebench import alpaca

    replies = [
        trade_page({"AAPL": [raw("2026-09-15T14:00:00.100Z", 100.0, 1)]}, nxt="n"),
        trade_page({"AAPL": [raw("2026-09-15T14:00:02.100Z", 102.0, 1)]}),
    ]
    seen = []

    def request(params):
        seen.append(dict(params))
        return replies.pop(0)

    alpaca.download(
        ["AAPL"], "1s",
        pd.Timestamp("2026-09-15 14:00", tz="UTC").to_pydatetime(),
        pd.Timestamp("2026-09-15 14:01", tz="UTC").to_pydatetime(),
        request=request,
    )
    assert len(seen) == 2
    assert seen[1]["page_token"] == "n"


def test_only_alpaca_can_serve_a_sub_minute_interval():
    from candlebench import bars as bars_module

    assert "1s" in bars_module.SOURCES["alpaca"].intervals
    assert "1s" not in bars_module.SOURCES["yfinance"].intervals
    assert "1m" in bars_module.SOURCES["yfinance"].intervals


def test_asking_yfinance_for_a_sub_minute_interval_is_rejected_with_the_reason():
    from dataclasses import replace

    from candlebench import config as config_module

    cfg = config_module.load()
    broken = replace(
        cfg, run=replace(cfg.run, source="yfinance", intervals=("1s",), lookback_days=0)
    )
    with pytest.raises(ValueError) as caught:
        config_module.validate(broken)
    assert "1s" in str(caught.value)
    assert "alpaca" in str(caught.value)


def test_a_sub_minute_run_on_the_whole_universe_is_refused_with_the_arithmetic():
    """A liquid name prints about a million trades a day, paged at 10,000.

    Fifty symbols over a year is on the order of a million requests against a
    200-per-minute budget. Letting a default start that is worse than refusing
    it, so the refusal states the arithmetic instead of just saying no.
    """
    from dataclasses import replace

    from candlebench import config as config_module, ticks as ticks_module

    cfg = config_module.load()
    broken = replace(
        cfg,
        run=replace(cfg.run, source="alpaca", intervals=("1s",), lookback_days=365),
    )
    with pytest.raises(ValueError) as caught:
        config_module.validate(broken)
    message = str(caught.value)
    assert "request" in message
    assert str(ticks_module.MAX_SUB_MINUTE_SYMBOL_DAYS) in message


def test_a_sub_minute_run_small_enough_to_finish_is_allowed():
    from dataclasses import replace

    from candlebench import config as config_module

    cfg = config_module.load()
    ok = replace(
        cfg,
        run=replace(cfg.run, source="alpaca", intervals=("1s",), lookback_days=2),
        universe=replace(cfg.universe, symbols=("AAPL",), sample_size=1),
    )
    assert config_module.validate(ok).run.intervals == ("1s",)
