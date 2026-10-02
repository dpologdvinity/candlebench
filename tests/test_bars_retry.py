"""Fetch retry tests.

Yahoo signals throttling by returning an empty frame rather than raising, so a
transient throttle looks exactly like a window with no bars. Both are retried.
This was found by observation: a 1m fetch's chunked requests, immediately
followed by a 15m request, got an empty frame for every symbol even though the
same 15m request succeeded on its own moments later.
"""

from __future__ import annotations

import pandas as pd

from candlebench import bars
from tests.test_bars import GOOD, frame


def test_a_transient_empty_frame_is_retried(tmp_path):
    calls = []

    def flaky(tickers, interval, start, end):
        calls.append(interval)
        if len(calls) == 1:
            return pd.DataFrame()  # throttled, which Yahoo reports as empty
        return frame([GOOD] * 3)

    report = bars.warm_cache(
        ["AAPL"], ["5m"], tmp_path, throttle_s=0.0, batch_size=1,
        retries=3, backoff_s=0.0, download=flaky,
    )
    assert report.written[("AAPL", "5m")] == 3
    assert not report.failures
    assert len(calls) == 2


def test_a_transient_error_is_retried(tmp_path):
    calls = []

    def flaky(tickers, interval, start, end):
        calls.append(interval)
        if len(calls) == 1:
            raise RuntimeError("connection reset")
        return frame([GOOD] * 2)

    report = bars.warm_cache(
        ["AAPL"], ["5m"], tmp_path, throttle_s=0.0, batch_size=1,
        retries=3, backoff_s=0.0, download=flaky,
    )
    assert report.written[("AAPL", "5m")] == 2
    assert not report.failures


def test_retries_are_bounded_and_the_last_error_is_reported(tmp_path):
    calls = []

    def broken(tickers, interval, start, end):
        calls.append(interval)
        raise RuntimeError("delisted")

    report = bars.warm_cache(
        ["GONE"], ["5m"], tmp_path, throttle_s=0.0, batch_size=1,
        retries=2, backoff_s=0.0, download=broken,
    )
    assert len(calls) == 3  # the first attempt plus two retries
    assert report.failures[("GONE", "5m")] == "delisted"


def test_persistent_emptiness_is_reported_as_such(tmp_path):
    """Distinguished from an exception, because the cause is different."""

    def empty(tickers, interval, start, end):
        return pd.DataFrame()

    report = bars.warm_cache(
        ["GONE"], ["5m"], tmp_path, throttle_s=0.0, batch_size=1,
        retries=1, backoff_s=0.0, download=empty,
    )
    assert "after retries" in report.failures[("GONE", "5m")]


def test_one_failing_symbol_does_not_cost_the_others(tmp_path):
    def selective(tickers, interval, start, end):
        if tickers == ["BAD"]:
            raise RuntimeError("no data for BAD")
        return frame([GOOD] * 3)

    report = bars.warm_cache(
        ["BAD", "GOODSYM"], ["5m"], tmp_path, throttle_s=0.0, batch_size=1,
        retries=0, backoff_s=0.0, download=selective,
    )
    assert ("BAD", "5m") in report.failures
    assert report.written[("GOODSYM", "5m")] == 3
