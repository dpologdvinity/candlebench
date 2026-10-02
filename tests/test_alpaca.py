"""The Alpaca bar source.

Yahoo caps intraday history at 28-59 days, which is one market regime and the
caveat that bounds every number this project reports. Alpaca's free tier serves
consolidated (SIP) bars back to 2016, which is the only way that caveat gets
fixed.

No test here touches the network: `download` takes the request callable, so the
query it builds and the pagination it performs are both assertable against
recorded response shapes.
"""

from __future__ import annotations

import pandas as pd
import pytest

from candlebench import alpaca, bars


def response(symbol_rows: dict[str, list[dict]], token: str | None = None) -> dict:
    out = {"bars": symbol_rows}
    if token:
        out["next_page_token"] = token
    return out


def bar(t: str, o=100.0, h=101.0, l=99.0, c=100.5, v=1000.0, n=10, vw=100.2) -> dict:
    return {"t": t, "o": o, "h": h, "l": l, "c": c, "v": v, "n": n, "vw": vw}


@pytest.fixture
def recorder():
    """A stand-in for the HTTP call that records every query it is handed."""

    class Recorder:
        def __init__(self):
            self.queries: list[dict] = []
            self.replies: list[dict] = []

        def __call__(self, params: dict) -> dict:
            self.queries.append(dict(params))
            return self.replies.pop(0) if self.replies else response({})

    return Recorder()


# ---------- credentials ----------


def test_credentials_come_only_from_the_environment(monkeypatch):
    monkeypatch.setenv(alpaca.ENV_KEY, "k")
    monkeypatch.setenv(alpaca.ENV_SECRET, "s")
    assert alpaca.credentials() == ("k", "s")


def test_missing_credentials_explain_what_to_set(monkeypatch):
    """The config and the browser are deliberately not options.

    A key in the TOML would be committed; a key from the browser would be echoed
    into a report. Both are permanent mistakes, so the environment is the only
    source.
    """
    monkeypatch.delenv(alpaca.ENV_KEY, raising=False)
    monkeypatch.delenv(alpaca.ENV_SECRET, raising=False)
    with pytest.raises(RuntimeError) as caught:
        alpaca.credentials()
    assert alpaca.ENV_KEY in str(caught.value)
    assert alpaca.ENV_SECRET in str(caught.value)


def test_a_half_configured_environment_is_refused(monkeypatch):
    monkeypatch.setenv(alpaca.ENV_KEY, "k")
    monkeypatch.delenv(alpaca.ENV_SECRET, raising=False)
    with pytest.raises(RuntimeError, match=alpaca.ENV_SECRET):
        alpaca.credentials()


# ---------- the query ----------


def test_an_interval_with_no_alpaca_timeframe_is_refused():
    with pytest.raises(ValueError, match="1s"):
        alpaca.timeframe("1s")


@pytest.mark.parametrize(
    "interval,expected",
    [("1m", "1Min"), ("5m", "5Min"), ("15m", "15Min"), ("30m", "30Min"), ("1h", "1Hour")],
)
def test_each_supported_interval_maps_to_a_timeframe(interval, expected):
    assert alpaca.timeframe(interval) == expected


def test_the_request_asks_for_split_adjusted_prices(recorder):
    """Raw prices put a fabricated gap where a split was.

    Measured against the live API: AAPL opens at 503.50 on 2020-08-28 and 128.05
    on 2020-08-31 under `adjustment=raw`, an apparent 75% collapse that is purely
    the 4:1 split. Under `adjustment=split` the same two bars read 125.88 and
    128.05 — the real move. A detector run over raw multi-year data would score
    that fake bar as a pattern.
    """
    recorder.replies = [response({"AAPL": [bar("2026-09-15T14:00:00Z")]})]
    alpaca.download(["AAPL"], "1m", _utc("2026-09-15"), _utc("2026-09-16"), request=recorder)
    assert recorder.queries[0]["adjustment"] == alpaca.ADJUSTMENT == "split"


def test_dividend_adjustment_is_not_used(recorder):
    """`adjustment=all` rewrites historical prices for every later dividend.

    That moves the body and shadow of a bar away from what actually traded,
    which is the geometry every detector measures. Splits alone are a share-count
    discontinuity and must be removed; dividends must not be.
    """
    assert alpaca.ADJUSTMENT != "all"


def test_the_request_carries_the_interval_window_and_batch(recorder):
    recorder.replies = [response({"AAPL": [bar("2026-09-15T14:00:00Z")]})]
    alpaca.download(
        ["AAPL", "MSFT"], "5m", _utc("2026-09-15"), _utc("2026-09-16"), request=recorder
    )
    query = recorder.queries[0]
    assert query["symbols"] == "AAPL,MSFT"
    assert query["timeframe"] == "5Min"
    assert query["limit"] == alpaca.MAX_LIMIT
    assert query["start"].startswith("2026-09-15")
    assert query["end"].startswith("2026-09-16")


# ---------- pagination ----------


def test_every_page_is_followed(recorder):
    """A full day of 1m bars for ten symbols exceeds one page."""
    recorder.replies = [
        response({"AAPL": [bar("2026-09-15T14:00:00Z")]}, token="p2"),
        response({"AAPL": [bar("2026-09-15T14:01:00Z")]}, token="p3"),
        response({"AAPL": [bar("2026-09-15T14:02:00Z")]}),
    ]
    frame = alpaca.download(["AAPL"], "1m", _utc("2026-09-15"), _utc("2026-09-16"), request=recorder)
    assert len(recorder.queries) == 3
    assert recorder.queries[1]["page_token"] == "p2"
    assert recorder.queries[2]["page_token"] == "p3"
    assert len(frame) == 3


def test_the_first_request_sends_no_page_token(recorder):
    recorder.replies = [response({"AAPL": [bar("2026-09-15T14:00:00Z")]})]
    alpaca.download(["AAPL"], "1m", _utc("2026-09-15"), _utc("2026-09-16"), request=recorder)
    assert "page_token" not in recorder.queries[0]


# ---------- the frame it returns ----------


def test_the_frame_is_shaped_like_the_yfinance_one(recorder):
    """`bars._extract` reads both, so one cache format serves either source."""
    recorder.replies = [
        response({
            "AAPL": [bar("2026-09-15T14:00:00Z", o=1.0), bar("2026-09-15T14:01:00Z", o=2.0)],
            "MSFT": [bar("2026-09-15T14:00:00Z", o=3.0)],
        })
    ]
    frame = alpaca.download(
        ["AAPL", "MSFT"], "1m", _utc("2026-09-15"), _utc("2026-09-16"), request=recorder
    )
    assert isinstance(frame.columns, pd.MultiIndex)
    assert set(frame.columns.get_level_values(0)) == {"AAPL", "MSFT"}

    extracted = bars._extract(frame, "AAPL")
    assert list(extracted.columns) == list(bars.BAR_COLUMNS)
    assert extracted["open"].dropna().tolist() == [1.0, 2.0]
    assert bars._extract(frame, "MSFT")["open"].dropna().tolist() == [3.0]


def test_the_index_is_tz_aware_utc(recorder):
    recorder.replies = [response({"AAPL": [bar("2026-09-15T14:00:00Z")]})]
    frame = alpaca.download(["AAPL"], "1m", _utc("2026-09-15"), _utc("2026-09-16"), request=recorder)
    assert str(frame.index.tz) == "UTC"


def test_an_empty_response_is_an_empty_frame_not_an_error(recorder):
    recorder.replies = [response({})]
    frame = alpaca.download(["AAPL"], "1m", _utc("2026-09-15"), _utc("2026-09-16"), request=recorder)
    assert frame.empty


def test_a_symbol_absent_from_the_response_is_simply_absent(recorder):
    """One delisted name must not cost the other forty-nine."""
    recorder.replies = [response({"AAPL": [bar("2026-09-15T14:00:00Z")]})]
    frame = alpaca.download(
        ["AAPL", "GONE"], "1m", _utc("2026-09-15"), _utc("2026-09-16"), request=recorder
    )
    assert bars._extract(frame, "GONE").empty
    assert not bars._extract(frame, "AAPL").empty


def test_the_cached_bars_survive_validation(recorder):
    """The whole point of matching the shape: nothing downstream changes."""
    recorder.replies = [
        response({"AAPL": [bar("2026-09-15T14:00:00Z"), bar("2026-09-15T14:01:00Z")]})
    ]
    frame = alpaca.download(["AAPL"], "1m", _utc("2026-09-15"), _utc("2026-09-16"), request=recorder)
    clean, dropped = bars.validate(bars._utc_index(bars._extract(frame, "AAPL")))
    assert len(clean) == 2
    assert dropped == 0


def _utc(day: str):
    return pd.Timestamp(f"{day} 00:00", tz="UTC").to_pydatetime()


# ---------- wiring into the cache ----------


def test_each_source_caches_under_its_own_directory():
    """One 1m file half from Yahoo and half from Alpaca would be undetectable.

    The two providers disagree on prices by design — Alpaca is split-adjusted,
    Yahoo with `auto_adjust=False` is not — so a mixed file would put a
    fabricated gap wherever the sources met.
    """
    from dataclasses import replace

    from candlebench.config import Config, RunConfig

    yahoo = Config(run=replace(RunConfig(), cache_dir="/tmp/c", source="yfinance"))
    alp = Config(run=replace(RunConfig(), cache_dir="/tmp/c", source="alpaca"))
    assert str(yahoo.cache_path) == "/tmp/c"
    assert str(alp.cache_path) == "/tmp/c/alpaca"


def test_an_unknown_source_is_rejected():
    from dataclasses import replace

    from candlebench import config as config_module

    cfg = config_module.load()
    broken = replace(cfg, run=replace(cfg.run, source="bloomberg"))
    with pytest.raises(ValueError, match="run.source"):
        config_module.validate(broken)


def test_a_lookback_beyond_what_yahoo_serves_is_rejected():
    """Asking Yahoo for two years of 1m silently returns 28 days.

    Accepting the number and quietly measuring something else is the one failure
    this project cannot tolerate.
    """
    from dataclasses import replace

    from candlebench import config as config_module

    cfg = config_module.load()
    broken = replace(cfg, run=replace(cfg.run, source="yfinance", lookback_days=730))
    with pytest.raises(ValueError, match="28"):
        config_module.validate(broken)


def test_the_same_lookback_is_fine_on_alpaca():
    from dataclasses import replace

    from candlebench import config as config_module

    cfg = config_module.load()
    ok = replace(cfg, run=replace(cfg.run, source="alpaca", lookback_days=730))
    assert config_module.validate(ok).run.lookback_days == 730


def test_the_default_lookback_is_each_provider_s_own():
    assert bars.lookback_days("yfinance", "1m", 0) == bars.INTERVAL_MAX_LOOKBACK_DAYS["1m"]
    assert bars.lookback_days("yfinance", "1h", 0) == bars.INTERVAL_MAX_LOOKBACK_DAYS["1h"]
    assert bars.lookback_days("alpaca", "1m", 0) == alpaca.DEFAULT_LOOKBACK_DAYS
    assert bars.lookback_days("alpaca", "1m", 730) == 730


def test_warming_the_cache_from_alpaca_uses_the_alpaca_downloader(tmp_path):
    """The source decides the downloader; everything after it is unchanged."""
    seen = {}

    def fake(symbols, interval, start, end):
        seen["interval"] = interval
        seen["days"] = (end - start).days
        index = pd.date_range("2026-09-15 13:30", periods=3, freq="1min", tz="UTC")
        frame = pd.DataFrame(
            {"Open": [1.0, 2.0, 3.0], "High": [2.0, 3.0, 4.0], "Low": [0.5, 1.5, 2.5],
             "Close": [1.5, 2.5, 3.5], "Volume": [10.0, 20.0, 30.0]},
            index=index,
        )
        return pd.concat({symbols[0]: frame}, axis=1)

    report = bars.warm_cache(
        ("AAA",), ("1m",), tmp_path, throttle_s=0.0, download=fake,
        source="alpaca", lookback_days=90,
    )
    assert report.written[("AAA", "1m")] == 3
    assert seen["interval"] == "1m"
    assert bars.cache_file(tmp_path, "AAA", "1m").exists()


def test_an_alpaca_fetch_reaches_further_back_than_yahoo_allows(tmp_path):
    """The whole reason the source exists."""
    spans = []

    def fake(symbols, interval, start, end):
        spans.append((end - start).days)
        return pd.DataFrame()

    bars.warm_cache(
        ("AAA",), ("1m",), tmp_path, throttle_s=0.0, download=fake,
        source="alpaca", lookback_days=400, retries=0, backoff_s=0.0,
    )
    assert sum(spans) >= 400
    assert sum(spans) > bars.INTERVAL_MAX_LOOKBACK_DAYS["1m"]


# ---------- the 15-minute SIP restriction ----------


def test_an_alpaca_window_stops_short_of_the_restricted_period():
    """Ending at `now` fails the whole request and loses the newest chunk.

    Measured against the live API: a window ending now returns
    `HTTP 403 subscription does not permit querying recent SIP data`, and because
    the error is for the request rather than for the restricted rows, the entire
    final chunk is lost — a 120-day fetch came back with 62 sessions ending a
    month early.
    """
    from datetime import datetime, timezone

    now = datetime(2026, 10, 2, 15, 0, tzinfo=timezone.utc)
    windows = bars._windows("1m", now, "alpaca", 60)
    latest = max(end for _, end in windows)
    assert latest <= now - alpaca.SIP_DELAY
    assert alpaca.SIP_DELAY.total_seconds() >= 15 * 60


def test_a_yfinance_window_still_reaches_the_present():
    """Yahoo has no such restriction, so nothing is given up there."""
    from datetime import datetime, timezone

    now = datetime(2026, 10, 2, 15, 0, tzinfo=timezone.utc)
    windows = bars._windows("1m", now, "yfinance", 0)
    assert max(end for _, end in windows) == now


def test_the_requested_span_is_still_covered_despite_the_lag():
    from datetime import datetime, timezone

    now = datetime(2026, 10, 2, 15, 0, tzinfo=timezone.utc)
    windows = bars._windows("1m", now, "alpaca", 90)
    span = max(e for _, e in windows) - min(s for s, _ in windows)
    assert span.days >= 90


# ---------- cross-symbol alignment padding ----------


def test_padding_from_other_symbols_is_not_counted_as_malformed():
    """A multi-symbol response outer-joins on timestamp.

    A symbol with no trade in a given minute gets an all-NaN row there, purely
    because another symbol did trade. Those rows are not bars and must not be
    reported as malformed data — a fetch summary saying it dropped 38,272
    malformed bars would make a user distrust bars that are in fact fine.
    """
    index = pd.DatetimeIndex(
        ["2026-09-15T14:00:00Z", "2026-09-15T14:01:00Z", "2026-09-15T14:02:00Z"]
    )
    busy = pd.DataFrame(
        {"Open": [1.0, 2.0, 3.0], "High": [1.0, 2.0, 3.0], "Low": [1.0, 2.0, 3.0],
         "Close": [1.0, 2.0, 3.0], "Volume": [10.0, 10.0, 10.0]},
        index=index,
    )
    quiet = busy.iloc[[1]]            # traded in only one of the three minutes
    frame = pd.concat({"BUSY": busy, "QUIET": quiet}, axis=1)

    extracted = bars._extract(frame, "QUIET")
    assert len(extracted) == 1
    _, dropped = bars.validate(bars._utc_index(extracted))
    assert dropped == 0


def test_extended_hours_bars_are_not_cached(recorder):
    """yfinance is fetched with `prepost=False`; Alpaca must match it.

    Nothing in this project ever reads an extended-hours bar: `bars.sessions`
    restricts to 09:30-16:00 before anything sees a price, and the spread
    estimate reads those session frames too. Caching them would store 2.2x the
    bars, re-read and re-validate them on every single trial, and leave the two
    sources holding different universes so their cache counts could not be
    compared.
    """
    rows = [
        bar("2026-09-15T12:00:00Z"),   # 08:00 ET, pre-market
        bar("2026-09-15T13:30:00Z"),   # 09:30 ET, the open
        bar("2026-09-15T19:59:00Z"),   # 15:59 ET, the last regular minute
        bar("2026-09-15T20:00:00Z"),   # 16:00 ET, after the close
        bar("2026-09-15T23:00:00Z"),   # 19:00 ET, post-market
    ]
    recorder.replies = [response({"AAPL": rows})]
    frame = alpaca.download(["AAPL"], "1m", _utc("2026-09-15"), _utc("2026-09-16"), request=recorder)

    kept = bars._extract(frame, "AAPL")
    assert len(kept) == 2
    local = kept.index.tz_convert(bars.MARKET_TZ).strftime("%H:%M").tolist()
    assert local == ["09:30", "15:59"]


def test_the_cached_index_stays_utc_after_the_hours_filter(recorder):
    """The cache format is UTC; filtering must not leave it in market time."""
    recorder.replies = [response({"AAPL": [bar("2026-09-15T13:30:00Z")]})]
    frame = alpaca.download(["AAPL"], "1m", _utc("2026-09-15"), _utc("2026-09-16"), request=recorder)
    assert str(frame.index.tz) == "UTC"
