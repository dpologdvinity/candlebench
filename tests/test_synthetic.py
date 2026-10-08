"""The synthetic source and the offline demo.

The demo is the first thing a visitor runs, and the synthetic walk is the null
the method is calibrated against, so both must be deterministic, look like
real bars to every validator, and need no network.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

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


def test_clustering_is_off_by_default_and_leaves_the_plain_market_unchanged():
    """The demo and every published synthetic measurement use the plain market."""
    day = date(2026, 9, 15)
    plain = synthetic.session("SYNC", day)
    pd.testing.assert_frame_equal(plain, synthetic.session("SYNC", day, clustered=False))
    clustered = synthetic.session("SYNC", day, clustered=True)
    assert not np.array_equal(plain["close"].to_numpy(), clustered["close"].to_numpy())
    # Clustering rescales the session's moves, not its level: the first open
    # differs only by its rescaled opening gap.
    assert np.isclose(plain["open"].iloc[0], clustered["open"].iloc[0], rtol=0.005)


def _abs_returns(frame):
    return np.abs(np.diff(np.log(frame["close"].to_numpy())))


def test_clustered_volatility_persists_from_minute_to_minute():
    """A volatile minute must tend to follow a volatile minute, unlike the plain walk."""
    def lag_one(clustered):
        moves = [_abs_returns(synthetic.session("SYND", date(2026, 3, 2) + timedelta(days=d),
                                                clustered=clustered))
                 for d in range(30) if (date(2026, 3, 2) + timedelta(days=d)).weekday() < 5]
        # Divide out the U shape, which correlates neighbouring minutes in both.
        profile = np.mean(moves, axis=0)
        scaled = np.concatenate([m / profile for m in moves])
        return np.corrcoef(scaled[:-1], scaled[1:])[0, 1]

    assert lag_one(True) > lag_one(False) + 0.05


def test_clustered_volatility_persists_from_day_to_day_and_across_symbols():
    """Calm and turbulent days come in runs, and partly together across symbols."""
    days = [date(2025, 1, 1) + timedelta(days=d) for d in range(400)]
    days = [d for d in days if d.weekday() < 5]

    def daily_log_vol(symbol):
        return np.log([np.std(np.diff(np.log(
            synthetic.session(symbol, d, clustered=True)["close"].to_numpy()))) for d in days])

    a, b = daily_log_vol("SYNE"), daily_log_vol("SYNF")
    assert np.corrcoef(a[:-1], a[1:])[0, 1] > 0.5
    assert np.corrcoef(a, b)[0, 1] > 0.15


def test_clustering_keeps_average_volatility_close_to_the_plain_walk():
    days = [date(2025, 1, 1) + timedelta(days=d) for d in range(0, 700, 3)]
    days = [d for d in days if d.weekday() < 5]
    plain = np.mean([np.std(_abs_returns(synthetic.session("SYNG", d))) for d in days])
    clustered = np.mean([np.std(_abs_returns(synthetic.session("SYNG", d, clustered=True)))
                         for d in days])
    assert 0.8 < clustered / plain < 1.25


def test_a_clustered_session_outside_the_volatility_series_is_refused():
    """Before EPOCH a negative index would wrap silently to the series' far end."""
    for day in (date(2019, 6, 3), date(2041, 1, 7)):
        with pytest.raises(ValueError, match="clustered sessions span"):
            synthetic.session("SYNA", day, clustered=True)
    synthetic.session("SYNA", date(2019, 6, 3))  # the plain market has no such limit
