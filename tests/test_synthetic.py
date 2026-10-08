"""The synthetic source and the offline demo.

The demo is the first thing a visitor runs, and the synthetic walk is the null
the method is calibrated against, so both must be deterministic, look like
real bars to every validator, and need no network.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import numpy as np
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


# ---------- planted edges, for measuring detection power ----------


def _signals(frame, name):
    from candlebench import patterns
    from candlebench.config import Thresholds
    from candlebench.patterns import context

    geom = context.geometry(bars.to_arrays(frame), synthetic.PLANT_TREND_LOOKBACK,
                            Thresholds().trend_min_slope)
    return np.flatnonzero(patterns.detect(patterns.get(name), geom, Thresholds()))


def test_a_planted_session_matches_bar_by_bar_generation():
    """The planted drift after a signal can create or remove later signals.

    The fixed-point solution must equal what a generator produces walking
    forward one bar at a time, deciding each bar's signal only from the bars it
    could have seen, through the identical construction.
    """
    from candlebench import patterns
    from candlebench.config import Thresholds
    from candlebench.patterns import context

    planted = synthetic.Planted("bullish_engulfing", drift=0.8, bars=3)
    spec = patterns.get(planted.pattern)
    thresholds = Thresholds()
    checked_signals = 0
    for day in (date(2026, 9, 15), date(2026, 9, 16), date(2026, 9, 17)):
        build, sigma = synthetic._parts("SYNA", day)
        drift = np.zeros(synthetic.SESSION_MINUTES)
        for i in range(synthetic.SESSION_MINUTES):
            geom = context.geometry(build(drift), synthetic.PLANT_TREND_LOOKBACK,
                                    thresholds.trend_min_slope)
            if patterns.detect(spec, geom, thresholds)[i]:
                after = slice(i + 1, min(synthetic.SESSION_MINUTES, i + 1 + planted.bars))
                drift[after] += spec.direction * planted.drift * sigma[after]
                checked_signals += 1
        reference = build(drift)
        fast = synthetic.session("SYNA", day, planted)
        for column in ("open", "high", "low", "close"):
            np.testing.assert_array_equal(fast[column].to_numpy(), reference[column])
    assert checked_signals >= 3


def test_a_zero_drift_plant_changes_nothing():
    day = date(2026, 9, 15)
    plain = synthetic.session("SYNB", day)
    pd.testing.assert_frame_equal(
        plain, synthetic.session("SYNB", day, synthetic.Planted("hammer", drift=0.0)))


def test_a_planted_edge_pays_after_its_signals():
    """Entering at the next open after a planted signal must gain, on average."""
    planted = synthetic.Planted("bullish_engulfing", drift=0.5, bars=5)
    gains, plain_gains = [], []
    for offset in range(40):
        day = date(2026, 3, 2) + timedelta(days=offset)
        if day.weekday() >= 5:
            continue
        for frame, out in ((synthetic.session("SYNC", day, planted), gains),
                           (synthetic.session("SYNC", day), plain_gains)):
            o, c = frame["open"].to_numpy(), frame["close"].to_numpy()
            for i in _signals(frame, planted.pattern):
                if i + 5 < len(c):
                    out.append(np.log(c[i + 5] / o[i + 1]))
    assert len(gains) > 50
    assert np.mean(gains) > np.mean(plain_gains) + 3 * np.std(gains) / np.sqrt(len(gains))
