"""The synthetic source and the offline demo.

The demo is the first thing a visitor runs, and the synthetic walk is the null
the method is calibrated against, so both must be deterministic, look like
real bars to every validator, and need no network.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pandas as pd

from candlebench import bars, cli, synthetic


def test_the_same_symbol_and_day_always_give_the_same_bars():
    """A demo or calibration result that changed between runs could not be cited."""
    day = date(2026, 9, 15)
    pd.testing.assert_frame_equal(synthetic.session("SYNA", day), synthetic.session("SYNA", day))
    assert not synthetic.session("SYNA", day).equals(synthetic.session("SYNB", day))


def test_synthetic_bars_survive_validation_untouched():
    """A bar the validator drops would mean the generator draws impossible geometry."""
    frame = synthetic.session("SYNC", date(2026, 9, 15))
    clean, dropped = bars.validate(frame)
    assert dropped == 0
    assert len(clean) == synthetic.SESSION_MINUTES


def test_a_coarse_bar_is_exactly_the_fine_bars_inside_it():
    """Drawn separately, a 5m bar could disagree with the five 1m bars it spans."""
    start = datetime(2026, 9, 15, tzinfo=timezone.utc)
    end = datetime(2026, 9, 16, tzinfo=timezone.utc)
    fine = bars._extract(synthetic.download(["SYND"], "1m", start, end), "SYND")
    coarse = bars._extract(synthetic.download(["SYND"], "5m", start, end), "SYND")
    first = fine.iloc[:5]
    assert coarse.iloc[0]["open"] == first["open"].iloc[0]
    assert coarse.iloc[0]["high"] == first["high"].max()
    assert coarse.iloc[0]["low"] == first["low"].min()
    assert coarse.iloc[0]["close"] == first["close"].iloc[-1]
    assert coarse.iloc[0]["volume"] == first["volume"].sum()


def test_weekends_have_no_sessions():
    saturday = datetime(2026, 9, 19, tzinfo=timezone.utc)
    sunday_night = datetime(2026, 9, 20, 23, tzinfo=timezone.utc)
    assert synthetic.download(["SYNA"], "1m", saturday, sunday_night).empty


def test_the_demo_runs_end_to_end_offline_and_publishes_a_run(tmp_path, monkeypatch):
    """A fresh clone has no cache and no key; the demo must still produce a dashboard run."""
    def refuse(*_args, **_kwargs):
        raise AssertionError("the demo must not touch a real provider")

    from dataclasses import replace

    for name in ("yfinance", "alpaca"):
        monkeypatch.setitem(bars.SOURCES, name, replace(bars.SOURCES[name], download=refuse))
    assert cli.main(["demo", "--cache-dir", str(tmp_path), "--trials", "40", "--no-serve"]) == 0
    published = tmp_path / "synthetic" / "last_run.json"
    assert published.exists()
    assert (tmp_path / "synthetic" / "last_run_trades.parquet").exists()
