"""Quoted spreads, observed rather than estimated.

The project's headline finding is that trading costs exceed whatever edge these
patterns carry, which makes the cost term the number the whole conclusion rests
on. Until now it was Corwin-Schultz: a spread *inferred* from high-low ranges
because no better data was on hand. Alpaca's free tier serves historical NBBO
quotes, so the spread can be observed instead.

Two measurements shaped this module, both taken before any of it was written.

The estimator reads high. Corwin-Schultz over two years of 1m bars charges 1.21
bps per leg; AAPL's median quoted half-spread is 0.45. Substituting the observed
figure moves expectancy by +0.09R and takes seven of 22 patterns off NEGATIVE,
which is why this is worth building rather than a refinement.

The spread is not one number per symbol. Sampled at 09:31, midday and 15:45
across five symbols and three sessions, the open runs a median 4.0x midday and
as much as 7.7x — AAPL 1.50 against 0.30, XOM 7.11 against 1.33. A trade entered
in the first half hour pays several times what the same trade pays at lunch, so
the table is keyed by time-of-day bucket and `simulate` charges per leg at the
bar it actually filled on.
"""

from __future__ import annotations

import json

import pytest

from candlebench import quotes, trades


def quote(bid: float, ask: float, t: str = "2026-09-15T13:31:00.1Z") -> dict:
    return {"t": t, "bp": bid, "ap": ask, "bs": 1, "as": 1}


def page(rows: list[dict]) -> dict:
    return {"quotes": rows}


# ---------- the half-spread of a quote ----------


def test_the_half_spread_is_half_the_relative_quoted_spread():
    """A market order crossing the spread pays half of it per leg."""
    # 100.00 / 100.02 is 2 bps wide on a 100.01 midpoint, so 1 bp per leg.
    assert quotes.half_spread_bps([quote(100.00, 100.02)]) == pytest.approx(0.9999, abs=1e-3)


def test_the_median_is_used_rather_than_the_mean():
    """One crossed or stale quote should not set a session's cost."""
    rows = [quote(100.0, 100.02)] * 9 + [quote(100.0, 140.0)]
    assert quotes.half_spread_bps(rows) == pytest.approx(0.9999, abs=1e-3)


def test_a_crossed_or_locked_quote_is_discarded():
    """ask <= bid is not a spread anybody pays."""
    assert quotes.half_spread_bps([quote(100.02, 100.00)]) is None
    assert quotes.half_spread_bps([quote(100.00, 100.00)]) is None


def test_a_missing_side_is_discarded():
    assert quotes.half_spread_bps([{"t": "x", "bp": 100.0}]) is None
    assert quotes.half_spread_bps([{"t": "x", "ap": 100.0, "bp": 0.0}]) is None


def test_no_quotes_is_unavailable_rather_than_zero():
    assert quotes.half_spread_bps([]) is None


# ---------- sampling a session ----------


def test_each_bucket_is_sampled_inside_its_own_window():
    """The open bucket must be sampled in the open, not merely early."""
    asked = []

    def request(params):
        asked.append(params["start"])
        return page([quote(100.0, 100.02)])

    quotes.sample_session("AAPL", "2026-09-15", request=request)
    minutes = [quotes._minutes_from_open(stamp) for stamp in asked]
    buckets = [trades.time_bucket(m) for m in minutes]
    assert set(buckets) == set(trades.TIME_BUCKETS)
    for bucket in trades.TIME_BUCKETS:
        assert buckets.count(bucket) == quotes.SAMPLES_PER_BUCKET


def test_a_session_sample_returns_one_figure_per_bucket():
    def request(params):
        minute = quotes._minutes_from_open(params["start"])
        wide = trades.time_bucket(minute) == "open"
        return page([quote(100.0, 100.08 if wide else 100.02)])

    out = quotes.sample_session("AAPL", "2026-09-15", request=request)
    assert set(out) == set(trades.TIME_BUCKETS)
    assert out["open"] > out["midday"]
    assert out["open"] == pytest.approx(4.0, abs=0.1)


def test_a_bucket_with_no_quotes_is_unavailable_not_zero():
    def request(params):
        minute = quotes._minutes_from_open(params["start"])
        if trades.time_bucket(minute) == "close":
            return page([])
        return page([quote(100.0, 100.02)])

    out = quotes.sample_session("AAPL", "2026-09-15", request=request)
    assert out["close"] is None
    assert out["midday"] is not None


# ---------- the table ----------


def test_the_table_is_keyed_by_symbol_and_bucket():
    def request(params):
        return page([quote(100.0, 100.02)])

    table = quotes.build_table(("AAPL", "KO"), ("2026-09-15",), request=request, sleep=lambda _: None)
    assert set(table) == {
        f"{s}|{b}" for s in ("AAPL", "KO") for b in trades.TIME_BUCKETS
    }
    assert all(v == pytest.approx(0.9999, abs=1e-3) for v in table.values())


def test_a_symbol_with_no_usable_quotes_is_absent_rather_than_zero():
    def request(params):
        return page([] if params["symbols"] == "GONE" else [quote(100.0, 100.02)])

    table = quotes.build_table(("AAPL", "GONE"), ("2026-09-15",), request=request, sleep=lambda _: None)
    assert not any(k.startswith("GONE|") for k in table)
    assert any(k.startswith("AAPL|") for k in table)


def test_the_table_round_trips_through_disk(tmp_path):
    table = {"AAPL|open": 1.5, "AAPL|midday": 0.3}
    path = tmp_path / "spreads.json"
    quotes.write_table(table, path)
    assert quotes.read_table(path) == table
    assert json.loads(path.read_text())["AAPL|open"] == 1.5


def test_a_missing_table_reads_as_empty_rather_than_raising(tmp_path):
    assert quotes.read_table(tmp_path / "absent.json") == {}


# ---------- looking a cost up ----------


def test_the_cost_depends_on_the_minute_of_the_session():
    """Measured: the open runs a median 4.0x midday, up to 7.7x."""
    table = {"AAPL|open": 1.60, "AAPL|midday": 0.40, "AAPL|close": 0.30}
    assert quotes.lookup(table, "AAPL", 5) == pytest.approx(1.60)
    assert quotes.lookup(table, "AAPL", 200) == pytest.approx(0.40)
    assert quotes.lookup(table, "AAPL", 380) == pytest.approx(0.30)


def test_a_symbol_absent_from_the_table_has_no_cost_to_look_up():
    assert quotes.lookup({"AAPL|open": 1.0}, "MSFT", 5) is None


def test_a_bucket_absent_for_a_present_symbol_falls_back_to_its_other_buckets():
    """Better the symbol's own midday spread than nothing at all."""
    table = {"AAPL|midday": 0.40, "AAPL|close": 0.30}
    assert quotes.lookup(table, "AAPL", 5) == pytest.approx(0.35, abs=1e-9)


def test_an_unknown_minute_uses_the_symbol_s_typical_spread():
    table = {"AAPL|open": 1.60, "AAPL|midday": 0.40, "AAPL|close": 0.30}
    assert quotes.lookup(table, "AAPL", None) == pytest.approx(0.40)
