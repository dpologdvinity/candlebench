"""Data layer tests.

Yahoo intraday data contains occasional bad bars. A bar whose high sits below
its own body would fabricate pattern geometry that never traded, so validation
is tested as a correctness requirement rather than tidiness.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest

from candlebench import bars


def frame(rows, index=None) -> pd.DataFrame:
    if index is None:
        index = pd.date_range("2026-09-15 13:30", periods=len(rows), freq="1min", tz="UTC")
    return pd.DataFrame(rows, columns=list(bars.BAR_COLUMNS), index=index)


GOOD = [100.0, 100.5, 99.5, 100.2, 1000.0]


def test_a_clean_frame_survives_validation():
    clean, dropped = bars.validate(frame([GOOD, GOOD]))
    assert len(clean) == 2
    assert dropped == 0


@pytest.mark.parametrize(
    "bad,reason",
    [
        ([100.0, 100.1, 99.5, 100.2, 1000.0], "high below the body"),
        ([100.0, 100.5, 100.3, 100.2, 1000.0], "low above the body"),
        ([100.0, 99.0, 99.5, 100.2, 1000.0], "high below low"),
        ([100.0, 100.5, 99.5, 100.2, 0.0], "zero volume"),
        ([None, 100.5, 99.5, 100.2, 1000.0], "missing price"),
    ],
)
def test_impossible_bars_are_dropped(bad, reason):
    clean, dropped = bars.validate(frame([GOOD, bad, GOOD]))
    assert len(clean) == 2, reason
    assert dropped == 1


def test_duplicate_timestamps_keep_one_bar():
    """DST transitions and retried chunks both produce repeats."""
    idx = pd.DatetimeIndex(["2026-09-15 13:30", "2026-09-15 13:30"], tz="UTC")
    clean, dropped = bars.validate(frame([GOOD, GOOD], index=idx))
    assert len(clean) == 1
    assert dropped == 1


def test_an_empty_frame_validates_to_empty():
    clean, dropped = bars.validate(frame([]))
    assert clean.empty
    assert dropped == 0


def test_extended_hours_bars_are_excluded():
    """Pre- and post-market bars are thin enough to be mostly spread artefacts."""
    idx = pd.DatetimeIndex(
        ["2026-09-15 12:00", "2026-09-15 13:30", "2026-09-15 19:59", "2026-09-15 20:30"],
        tz="UTC",
    )  # 08:00, 09:30, 15:59 and 16:30 Eastern
    kept = bars.regular_hours(frame([GOOD] * 4, index=idx))
    assert len(kept) == 2


def test_sessions_split_on_the_eastern_calendar_date():
    """Not UTC, which would cut a session in the middle of the afternoon."""
    idx = pd.DatetimeIndex(
        ["2026-09-15 14:00", "2026-09-15 19:00", "2026-09-16 14:00"], tz="UTC"
    )
    out = bars.sessions(frame([GOOD] * 3, index=idx))
    assert len(out) == 2
    assert [len(v) for v in out.values()] == [2, 1]


def test_a_cache_miss_names_the_fetch_command(tmp_path):
    """So the user is told what to do rather than that a path is absent."""
    with pytest.raises(FileNotFoundError, match="candlebench fetch"):
        bars.load("AAPL", "1m", tmp_path)


def test_one_minute_history_is_fetched_in_chunks_within_the_per_request_cap():
    """Yahoo allows only 8 days of 1m data per request."""
    windows = bars._windows("1m", datetime(2026, 9, 30, tzinfo=timezone.utc))
    assert all((end - start).days <= 8 for start, end in windows)
    assert (windows[-1][1] - windows[0][0]).days == 28


def test_coarser_intervals_fetch_their_whole_span_in_one_request():
    windows = bars._windows("5m", datetime(2026, 9, 30, tzinfo=timezone.utc))
    assert len(windows) == 1
    assert (windows[0][1] - windows[0][0]).days == 59


@pytest.mark.parametrize("interval", bars.SUPPORTED_INTERVALS)
def test_every_lookback_stays_inside_yahoos_limit(interval):
    """Requesting exactly the limit fails once a long warm-up has elapsed.

    Yahoo compares a request against its own clock, so a window built at the
    documented maximum is already over it by the time a later interval's
    request lands. Observed live: 15m at exactly 60 days was rejected after
    the 1m chunks had been downloading for a minute.
    """
    limit = 8 if interval == "1m" else 60
    assert bars.INTERVAL_MAX_LOOKBACK_DAYS[interval] < 60
    assert bars.INTERVAL_CHUNK_DAYS.get(interval, 59) < limit


def test_the_clock_is_re_read_for_each_interval(tmp_path):
    """So a slow first interval cannot push a later one past its limit."""
    seen = []

    def record(tickers, interval, start, end):
        seen.append((interval, end))
        return frame([GOOD])

    bars.warm_cache(
        ["AAPL"], ["1m", "5m"], tmp_path, throttle_s=0.0, batch_size=1,
        retries=0, backoff_s=0.0, download=record,
    )
    ends = {interval: end for interval, end in seen}
    assert ends["5m"] >= ends["1m"]


def test_warm_cache_records_a_failure_without_aborting(tmp_path):
    """One delisted symbol must not cost the other forty-nine."""

    def download(tickers, interval, start, end):
        if "BAD" in tickers:
            raise RuntimeError("no data found for BAD")
        return frame([GOOD] * 3)

    report = bars.warm_cache(
        ["BAD"], ["5m"], tmp_path, throttle_s=0.0, batch_size=1,
        retries=0, backoff_s=0.0, download=download,
    )
    assert ("BAD", "5m") in report.failures
    assert "no data found" in report.summary()


def test_warm_cache_writes_a_readable_parquet_file(tmp_path):
    def download(tickers, interval, start, end):
        return frame([GOOD] * 3)

    report = bars.warm_cache(
        ["AAPL"], ["5m"], tmp_path, throttle_s=0.0, batch_size=1, download=download
    )
    assert report.written[("AAPL", "5m")] == 3
    assert len(bars.load("AAPL", "5m", tmp_path)) == 3


def test_to_arrays_yields_float_columns():
    out = bars.to_arrays(frame([GOOD, GOOD]))
    assert set(out) == set(bars.BAR_COLUMNS)
    assert out["close"].dtype.kind == "f"
