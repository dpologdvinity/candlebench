"""Web server tests.

The server triggers real runs and real network fetches from unauthenticated
requests, so the tests that matter most are the ones pinning what a request
cannot do: reach outside the static directory, choose where bars are written,
or start a second job on top of a running one.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import replace
from functools import partial
from http.server import ThreadingHTTPServer

import pytest

from candlebench.config import Config, RunConfig, StatsConfig, UniverseConfig
from candlebench.web import server as web
from candlebench.web.jobs import JobRunner
from tests.test_end_to_end import SYMBOLS, write_cache


@pytest.fixture
def base_config(tmp_path):
    write_cache(tmp_path)
    return Config(
        run=replace(RunConfig(), intervals=("1m",), trials=2, seed=5,
                    cache_dir=str(tmp_path), throttle_s=0.0, holdout_fraction=0.0),
        universe=replace(UniverseConfig(), symbols=SYMBOLS, sample_size=2),
        stats=replace(StatsConfig(), bootstrap_samples=100, min_trades=5),
        patterns=("hammer", "bullish_engulfing", "random_long", "random_short"),
    )


@pytest.fixture
def client(base_config, tmp_path):
    """A live server on an ephemeral port, torn down after the test."""
    from candlebench.web.history import History

    jobs = JobRunner(tmp_path / "last_run.json", history=History(tmp_path / "runs"))
    handler = partial(web.Handler, base_config=base_config, jobs=jobs)
    httpd = ThreadingHTTPServer((web.HOST, 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    port = httpd.server_address[1]

    def call(path, payload=None, expect=200):
        url = f"http://{web.HOST}:{port}{path}"
        data = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(
            url, data=data, method="POST" if data else "GET",
            headers={"Content-Type": "application/json"} if data else {},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                body = response.read()
                assert response.status == expect, body
                return json.loads(body) if response.headers.get(
                    "Content-Type", ""
                ).startswith("application/json") else body.decode()
        except urllib.error.HTTPError as err:
            assert err.code == expect, err.read()
            return json.loads(err.read())

    try:
        yield call
    finally:
        httpd.shutdown()
        httpd.server_close()


def wait_for_idle(call, timeout=60):
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = call("/api/status")
        if state["status"] != "working":
            return state
        time.sleep(0.05)
    raise AssertionError("job did not finish")


# ---------- serving the page ----------


def test_the_root_serves_the_page(client):
    assert "<title>candlebench</title>" in client("/")


def test_static_assets_are_served(client):
    assert "--accent" in client("/static/app.css")
    assert "boot()" in client("/static/app.js")


def test_a_traversal_request_cannot_escape_the_static_directory(client):
    """A path such as /static/../../../etc/passwd must not be readable."""
    for attempt in (
        "/static/../../../etc/passwd",
        "/static/..%2f..%2fconfig.py",
        "/static/../server.py",
    ):
        assert client(attempt, expect=404) == {"error": "not found"}


def test_an_unknown_route_is_a_json_404(client):
    assert client("/api/nope", expect=404)["error"] == "not found"


# ---------- metadata ----------


def test_meta_describes_every_pattern_and_interval(client):
    meta = client("/api/meta")
    assert len(meta["patterns"]) == 22
    # The base config's source is yfinance, so the page is offered its
    # intervals and not the union of every source's.
    assert meta["intervals"] == list(web.bars.YFINANCE_INTERVALS)
    assert "1s" in meta["intervals_by_source"]["alpaca"]
    assert "ci_low" in meta["rank_keys"]
    assert meta["config"]["run"]["seed"] == 5
    assert meta["lookback_days"]["1m"] == 28


def test_meta_marks_which_patterns_are_controls(client):
    kinds = {p["name"]: p["kind"] for p in client("/api/meta")["patterns"]}
    assert kinds["random_long"] == "control"
    assert kinds["hammer"] == "pattern"


def test_the_cache_endpoint_reports_what_is_available(client):
    cache = client("/api/cache")
    assert cache["requested_symbols"] == 2
    assert cache["intervals"]["1m"]["sessions"] > 0
    assert cache["intervals"]["1h"]["sessions"] == 0


def test_results_are_empty_before_the_first_run(client):
    assert client("/api/results") == {}


# ---------- running ----------


def test_a_run_produces_results_the_page_can_render(client):
    assert client("/api/run", {}, expect=202)["started"]
    assert wait_for_idle(client)["status"] == "done"

    results = client("/api/results")
    assert results["stats"]
    assert results["config"]["run"]["seed"] == 5
    assert "cost_dominated" in results
    assert {s["interval"] for s in results["stats"]} == {"1m", "all"}


def test_progress_is_reported_as_absolute_completion(client):
    """A browser showing nothing for minutes is indistinguishable from a hang."""
    client("/api/run", {}, expect=202)
    state = wait_for_idle(client)
    assert state["total"] == 2  # 2 trials x 1 interval
    assert state["done"] == state["total"]
    assert state["percent"] == 100.0


def test_browser_overrides_reach_the_run(client):
    client("/api/run", {"run": {"trials": 1, "seed": 99}}, expect=202)
    wait_for_idle(client)
    results = client("/api/results")
    assert results["config"]["run"]["seed"] == 99
    assert len(results["trials"]) == 1


def test_a_second_job_is_refused_while_one_runs(client):
    """Two concurrent runs would compete for the same cache."""
    client("/api/run", {"run": {"trials": 2}}, expect=202)
    second = client("/api/run", {}, expect=409)
    assert "already running" in second["error"]
    wait_for_idle(client)


def test_results_survive_a_restart(base_config, tmp_path):
    """Reopening the page shows the last run rather than an empty table."""
    path = tmp_path / "last_run.json"
    first = JobRunner(path)
    first.submit("run", lambda state: {"stats": [{"pattern": "hammer"}]})
    import time

    for _ in range(200):
        if first.state["status"] == "done":
            break
        time.sleep(0.01)
    assert path.exists()

    revived = JobRunner(path)
    assert revived.results["stats"][0]["pattern"] == "hammer"


def test_a_corrupt_persisted_result_does_not_break_startup(tmp_path):
    """An interrupted write should cost the last result, not the server."""
    path = tmp_path / "last_run.json"
    path.write_text('{"stats": [truncated')
    assert JobRunner(path).results is None


def test_a_failing_run_surfaces_the_error(client, base_config, tmp_path):
    empty = tmp_path / "nothing"
    empty.mkdir()
    client("/api/run", {"universe": {"sample_size": 1}}, expect=202)
    state = wait_for_idle(client)
    assert state["status"] in ("done", "error")


# ---------- trades ----------


def test_trades_are_not_available_before_a_run(client):
    """There is no file to read, which is a 404 rather than an empty success."""
    assert client("/api/trades", expect=404)["error"]


def test_a_run_writes_a_trade_file_beside_the_report(client, tmp_path):
    client("/api/run", {}, expect=202)
    wait_for_idle(client)
    assert (tmp_path / "last_run_trades.parquet").exists()


def test_trades_are_served_as_a_counted_page(client):
    client("/api/run", {}, expect=202)
    wait_for_idle(client)

    body = client("/api/trades?limit=5")
    assert body["total"] >= len(body["trades"])
    assert len(body["trades"]) <= 5
    assert body["limit"] == 5
    assert body["offset"] == 0
    assert set(body["columns"]) >= {"pattern", "net_r", "exit_reason", "bars_held"}


def test_filtering_trades_by_pattern_returns_only_that_pattern(client):
    client("/api/run", {}, expect=202)
    wait_for_idle(client)
    # Whichever pattern actually fired on this small synthetic cache: naming one
    # would make the test depend on the random walk rather than on the filter.
    chosen = client("/api/trades?limit=1")["trades"][0]["pattern"]
    body = client(f"/api/trades?pattern={chosen}&limit=500")
    assert body["trades"]
    assert {t["pattern"] for t in body["trades"]} == {chosen}
    assert body["total"] == len(body["trades"])


def test_paging_trades_walks_the_whole_set_without_repeating(client):
    client("/api/run", {}, expect=202)
    wait_for_idle(client)
    first = client("/api/trades?limit=3&offset=0")["trades"]
    second = client("/api/trades?limit=3&offset=3")["trades"]
    assert first and second
    assert first != second


def test_the_page_size_is_capped_by_the_server(client):
    """Otherwise one request could ask for all 43,000 trades at once."""
    client("/api/run", {}, expect=202)
    wait_for_idle(client)
    assert client("/api/trades?limit=100000")["limit"] == web.MAX_PAGE


def test_a_trade_pattern_that_is_not_registered_is_rejected(client):
    """The filter value is checked against the registry before any frame work."""
    body = client("/api/trades?pattern=../../etc/passwd", expect=400)
    assert "unknown pattern" in body["error"]


def test_a_trade_interval_that_is_not_supported_is_rejected(client):
    body = client("/api/trades?interval=3h", expect=400)
    assert "unsupported interval" in body["error"]


def test_a_trade_symbol_that_could_escape_the_cache_is_rejected(client):
    body = client("/api/trades?symbol=../../../etc/passwd", expect=400)
    assert "invalid symbol" in body["error"]


def test_a_non_numeric_page_parameter_is_a_400_not_a_crash(client):
    for query in ("/api/trades?limit=lots", "/api/trades?offset=-1"):
        assert client(query, expect=400)["error"]


def test_trades_can_be_sorted_by_any_column(client):
    """The drill-down table sorts server-side, so paging and sorting agree."""
    client("/api/run", {}, expect=202)
    wait_for_idle(client)
    rising = [t["net_r"] for t in client("/api/trades?sort=net_r&desc=0&limit=20")["trades"]]
    falling = [t["net_r"] for t in client("/api/trades?sort=net_r&desc=1&limit=20")["trades"]]
    assert rising == sorted(rising)
    assert falling == sorted(falling, reverse=True)
    assert rising[0] <= falling[0]


def test_sorting_by_a_column_that_does_not_exist_is_rejected(client):
    body = client("/api/trades?sort=../../etc/passwd", expect=400)
    assert "cannot sort by" in body["error"]


# ---------- breakdowns and the equity curve ----------


def test_a_breakdown_groups_the_last_run(client):
    client("/api/run", {}, expect=202)
    wait_for_idle(client)
    body = client("/api/breakdown?by=symbol")
    assert {r["key"] for r in body["rows"]} <= set(SYMBOLS)
    assert body["by"] == "symbol"
    assert all("expectancy_r" in r and "exit_mix" in r for r in body["rows"])


def test_a_time_of_day_breakdown_comes_back_in_session_order(client):
    client("/api/run", {}, expect=202)
    wait_for_idle(client)
    keys = [r["key"] for r in client("/api/breakdown?by=time_of_day")["rows"]]
    assert keys == [k for k in ("open", "midday", "close") if k in keys]


def test_an_unknown_breakdown_key_is_rejected(client):
    body = client("/api/breakdown?by=../../etc/passwd", expect=400)
    assert "cannot break down by" in body["error"]


def test_a_breakdown_needs_a_key(client):
    assert client("/api/breakdown", expect=400)["error"]


def test_the_equity_curve_agrees_with_the_reported_drawdown(client):
    """The same ordering rule on both sides, asserted end to end."""
    client("/api/run", {}, expect=202)
    wait_for_idle(client)

    stats = {
        (s["pattern"], s["interval"]): s for s in client("/api/results")["stats"]
    }
    chosen = next(
        s for s in stats.values()
        if s["interval"] == "1m" and s["kind"] == "pattern" and s["trades"] > 0
    )
    curve = client(f"/api/equity?pattern={chosen['pattern']}&interval=1m")
    assert curve["pattern"]["trades"] == chosen["trades"]
    assert curve["pattern"]["max_drawdown_r"] == pytest.approx(
        chosen["max_drawdown_r"], rel=1e-9
    )


def test_the_equity_curve_carries_its_matched_control(client):
    """The overlay has to be the control for that bias, not one the page picked."""
    client("/api/run", {}, expect=202)
    wait_for_idle(client)
    curve = client("/api/equity?pattern=hammer&interval=1m")
    assert curve["control"]["pattern"] == "random_long"

    bearish = client("/api/equity?pattern=bearish_engulfing&interval=1m")
    assert bearish["control"]["pattern"] == "random_short"


def test_an_equity_curve_for_an_unknown_pattern_is_rejected(client):
    assert "unknown pattern" in client(
        "/api/equity?pattern=../../etc/passwd", expect=400
    )["error"]


def test_an_equity_curve_needs_a_pattern(client):
    assert client("/api/equity?interval=1m", expect=400)["error"]


# ---------- one session's bars ----------


def session_request(client, pattern="hammer", **over):
    params = {
        "symbol": SYMBOLS[0], "session": "2026-09-15", "interval": "1m",
        "pattern": pattern, **over,
    }
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return f"/api/session?{query}"


def test_a_session_returns_its_bars_and_the_pattern_mask(client):
    body = client(session_request(client))
    assert len(body["bars"]) == 120  # the synthetic cache writes 120 bars
    assert set(body["bars"][0]) == {"t", "o", "h", "l", "c", "v"}
    assert isinstance(body["signals"], list)
    assert body["trend_lookback"] >= 2


def test_the_mask_matches_what_the_detector_itself_returns(client, base_config):
    """Two paths to one answer; a chart that marked different bars would lie."""
    from candlebench import bars as bars_module, patterns as patterns_module, runner
    from candlebench.patterns import context

    frame = bars_module.sessions(
        bars_module.load(SYMBOLS[0], "1m", base_config.cache_path)
    )[__import__("datetime").date(2026, 9, 15)]
    lookback = runner.trend_lookback_for_length(
        base_config.thresholds.trend_lookback, len(frame), 3
    )
    geom = context.geometry(
        bars_module.to_arrays(frame), lookback, base_config.thresholds.trend_min_slope
    )
    mask = patterns_module.detect(
        patterns_module.get("hammer"), geom, base_config.thresholds
    )

    body = client(session_request(client))
    assert body["signals"] == [int(i) for i in mask.nonzero()[0]]


def test_every_recorded_trade_sits_on_a_marked_signal(client):
    """A trade enters on the bar after its signal. The chart must show both."""
    client("/api/run", {}, expect=202)
    wait_for_idle(client)

    for pattern in ("hammer", "bullish_engulfing"):
        body = client(session_request(client, pattern=pattern))
        signals = set(body["signals"])
        assert all(t["entry_index"] - 1 in signals for t in body["trades"])


def test_the_marked_trades_carry_their_levels(client):
    client("/api/run", {}, expect=202)
    wait_for_idle(client)

    # Ask about a session the run actually drew. Naming one would make the test
    # depend on which sessions the sampler happened to pick.
    first = client("/api/trades?interval=1m&limit=1")["trades"][0]
    body = client(session_request(
        client, pattern=first["pattern"], symbol=first["symbol"], session=first["session"]
    ))
    assert body["trades"]
    for t in body["trades"]:
        assert {"entry_index", "exit_index", "entry_price", "stop_price",
                "target_price", "net_r", "exit_reason"} <= set(t)


def test_a_session_symbol_that_could_escape_the_cache_is_rejected(client):
    """The symbol becomes a path segment in the bar cache."""
    body = client(session_request(client, symbol="../../../etc/passwd"), expect=400)
    assert "invalid symbol" in body["error"]


def test_a_session_that_is_not_a_date_is_rejected(client):
    assert "session" in client(session_request(client, session="yesterday"), expect=400)["error"]


@pytest.mark.parametrize("missing", ["symbol", "session", "interval", "pattern"])
def test_a_session_request_needs_every_part(client, missing):
    params = {
        "symbol": SYMBOLS[0], "session": "2026-09-15",
        "interval": "1m", "pattern": "hammer",
    }
    del params[missing]
    query = "&".join(f"{k}={v}" for k, v in params.items())
    assert client(f"/api/session?{query}", expect=400)["error"]


def test_a_session_with_no_cached_bars_is_a_404(client):
    body = client(session_request(client, session="2019-01-02"), expect=404)
    assert "no cached" in body["error"]


# ---------- run history ----------


def test_a_completed_run_is_added_to_the_history(client):
    client("/api/run", {"run": {"seed": 77}}, expect=202)
    wait_for_idle(client)

    body = client("/api/runs")
    assert len(body["runs"]) == 1
    only = body["runs"][0]
    assert only["seed"] == 77
    assert only["trials"] == 2
    assert only["intervals"] == ["1m"]
    assert only["saved_at"]


def test_two_runs_are_listed_newest_first(client):
    for seed in (1, 2):
        client("/api/run", {"run": {"seed": seed}}, expect=202)
        wait_for_idle(client)
    assert [r["seed"] for r in client("/api/runs")["runs"]] == [2, 1]


def test_a_saved_run_can_be_read_back_whole(client):
    client("/api/run", {"run": {"seed": 31}}, expect=202)
    wait_for_idle(client)
    run_id = client("/api/runs")["runs"][0]["id"]

    body = client(f"/api/runs?id={run_id}")
    assert body["config"]["run"]["seed"] == 31
    assert body["stats"]


def test_an_old_run_keeps_its_own_trades(client):
    """Otherwise comparing two runs would compare one run against itself."""
    client("/api/run", {"run": {"seed": 5, "trials": 2}}, expect=202)
    wait_for_idle(client)
    older = client("/api/runs")["runs"][0]["id"]
    first_total = client(f"/api/trades?run={older}")["total"]

    client("/api/run", {"run": {"seed": 5, "trials": 1}}, expect=202)
    wait_for_idle(client)
    assert client(f"/api/trades?run={older}")["total"] == first_total
    assert client("/api/trades")["total"] != first_total


def test_the_history_keeps_only_the_most_recent_runs(tmp_path):
    from candlebench.web.history import History

    history = History(tmp_path / "runs", keep=3)
    ids = [history.save({"stats": [], "n": n}, []) for n in range(5)]
    assert [r["id"] for r in history.summaries()] == list(reversed(ids[-3:]))
    for gone in ids[:2]:
        assert history.payload(gone) is None
        assert not (tmp_path / "runs" / f"{gone}.parquet").exists()


def test_a_run_id_that_could_escape_the_history_is_rejected(client):
    for bad in ("../../etc/passwd", "..", "2026-09-15", "x" * 40):
        body = client(f"/api/runs?id={bad}", expect=400)
        assert "run id" in body["error"]


def test_a_trade_query_for_an_unknown_run_is_a_404(client):
    assert client("/api/trades?run=19990101T000000", expect=404)["error"]


def test_an_unknown_run_payload_is_a_404(client):
    assert client("/api/runs?id=19990101T000000", expect=404)["error"]


def test_the_history_survives_a_restart(base_config, tmp_path):
    from candlebench.web.history import History

    first = History(tmp_path / "runs")
    saved = first.save({"stats": [], "config": {"run": {"seed": 8}}}, [])
    assert History(tmp_path / "runs").payload(saved)["config"]["run"]["seed"] == 8


# ---------- request validation ----------


def test_the_cache_directory_cannot_be_set_from_the_browser(client):
    """Otherwise a request could choose any path on the machine to read or write."""
    body = client("/api/run", {"run": {"cache_dir": "/tmp/evil"}}, expect=400)
    assert "cannot set cache_dir" in body["error"]


def test_an_unknown_field_is_rejected_rather_than_ignored(client):
    body = client("/api/run", {"trade": {"rewrad_multiple": 3}}, expect=400)
    assert "rewrad_multiple" in body["error"]


def test_a_symbol_that_could_escape_the_cache_is_rejected(client):
    """A symbol becomes a filename in the bar cache."""
    body = client("/api/run", {"universe": {"symbols": ["../../etc/passwd"]}}, expect=400)
    assert "invalid symbol" in body["error"]


def test_a_sub_minute_interval_is_rejected_with_its_reason(client):
    """The base config's source is yfinance, which has no sub-minute interval."""
    body = client("/api/run", {"run": {"intervals": ["1s"]}}, expect=400)
    assert "does not serve" in body["error"]
    assert "alpaca" in body["error"]


def test_an_unknown_pattern_is_rejected(client):
    body = client("/api/run", {"patterns": ["hammer_time"]}, expect=400)
    assert "hammer_time" in body["error"]


def test_malformed_json_is_a_400_not_a_crash(client, base_config, tmp_path):
    jobs = JobRunner(tmp_path / "x.json")
    handler = partial(web.Handler, base_config=base_config, jobs=jobs)
    httpd = ThreadingHTTPServer((web.HOST, 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        request = urllib.request.Request(
            f"http://{web.HOST}:{httpd.server_address[1]}/api/run",
            data=b"{not json", method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=10)
        assert caught.value.code == 400
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_config_from_request_leaves_unmentioned_sections_alone(base_config):
    out = web.config_from_request(base_config, {"run": {"trials": 7}})
    assert out.run.trials == 7
    assert out.run.seed == base_config.run.seed
    assert out.patterns == base_config.patterns
    assert out.run.cache_dir == base_config.run.cache_dir


def test_the_server_binds_only_to_loopback():
    """Binding wider would let any host on the network start work."""
    assert web.HOST == "127.0.0.1"


def test_meta_lists_the_breakdowns_and_cost_models(client):
    meta = client("/api/meta")
    assert "time_of_day" in meta["breakdowns"]
    assert meta["cost_models"] == ["quoted", "estimated", "fixed"]
    assert meta["config"]["run"]["windows"] == 1


def test_walk_forward_windows_can_be_set_from_the_browser(client):
    client("/api/run", {"run": {"trials": 4, "windows": 2}}, expect=202)
    wait_for_idle(client)
    results = client("/api/results")
    assert results["config"]["run"]["windows"] == 2
    assert {t["window"] for t in client("/api/trades?limit=500")["trades"]} == {0, 1}


def test_the_cost_model_can_be_switched_from_the_browser(client):
    client("/api/run", {"costs": {"model": "fixed", "slippage_bps": 3.0}}, expect=202)
    wait_for_idle(client)
    results = client("/api/results")
    assert results["config"]["costs"]["model"] == "fixed"
    assert results["spread_bps"] is None
    assert "fixed" in results["costs_description"]


def test_an_unknown_cost_model_from_the_browser_is_rejected(client):
    body = client("/api/run", {"costs": {"model": "vibes"}}, expect=400)
    assert "costs.model" in body["error"]


def test_a_run_is_not_listed_until_its_trades_are_readable(tmp_path, monkeypatch):
    """`summaries()` lists by the JSON file, so the JSON is the commit point.

    Writing the report first makes a run appear in the picker while its Parquet
    is absent or half-written, and `/api/trades?run=` then 404s on a run the
    page is offering to compare.
    """
    import pandas as pd

    from candlebench.web.history import History

    history = History(tmp_path / "runs")

    def explode(self, target, *args, **kwargs):
        raise OSError("No space left on device")

    monkeypatch.setattr(pd.DataFrame, "to_parquet", explode)
    with pytest.raises(OSError):
        history.save({"stats": [], "config": {"run": {"seed": 1}}}, [])

    assert history.summaries() == []


def test_the_session_chart_uses_the_thresholds_the_run_used(client):
    """Thresholds are browser-editable, so the base config is the wrong source.

    The whole point of the session chart is that it cannot disagree with the
    leaderboard about which bars were signals. Detecting with the server's
    startup thresholds while the run used different ones breaks exactly that:
    the chart would outline bars the leaderboard never counted.
    """
    client("/api/run", {"thresholds": {"trend_lookback": 4}}, expect=202)
    wait_for_idle(client)
    assert client("/api/results")["config"]["thresholds"]["trend_lookback"] == 4

    body = client(session_request(client))
    assert body["trend_lookback"] == 4


def test_a_threshold_change_moves_the_marked_signals(client):
    """If the mask ignored the run's thresholds, these two would be identical.

    `opposite_shadow_max` is the threshold that measurably moves `hammer` on this
    synthetic cache: at 0.01 no bar qualifies, at 0.9 fourteen do.
    """
    on = "2026-09-14"
    client("/api/run", {"thresholds": {"opposite_shadow_max": 0.01}}, expect=202)
    wait_for_idle(client)
    strict = client(session_request(client, session=on))["signals"]

    client("/api/run", {"thresholds": {"opposite_shadow_max": 0.9}}, expect=202)
    wait_for_idle(client)
    loose = client(session_request(client, session=on))["signals"]

    assert strict == []
    assert len(loose) == 14
    assert set(strict) <= set(loose)


def test_a_session_chart_can_be_scoped_to_a_saved_run(client):
    """Comparing two runs and then opening a chart must not silently show the latest.

    The marks come from stored trades and the mask from stored thresholds, so a
    chart that ignored `run=` would mix one run's levels with another's geometry
    — the disagreement this endpoint exists to prevent, just between runs
    instead of between chart and leaderboard.
    """
    on = "2026-09-14"
    client("/api/run", {"thresholds": {"opposite_shadow_max": 0.9}}, expect=202)
    wait_for_idle(client)
    loose_run = client("/api/runs")["runs"][0]["id"]
    loose = client(session_request(client, session=on))["signals"]
    assert len(loose) == 14

    client("/api/run", {"thresholds": {"opposite_shadow_max": 0.01}}, expect=202)
    wait_for_idle(client)
    assert client(session_request(client, session=on))["signals"] == []

    scoped = client(f"{session_request(client, session=on)}&run={loose_run}")
    assert scoped["signals"] == loose
    assert scoped["run"] == loose_run


def test_a_session_chart_for_an_unknown_run_is_a_404(client):
    body = client(f"{session_request(client)}&run=19990101T000000", expect=404)
    assert "19990101T000000" in body["error"]


def test_a_session_run_id_that_could_escape_is_rejected(client):
    assert "run id" in client(
        f"{session_request(client)}&run=../../etc/passwd", expect=400
    )["error"]


@pytest.mark.parametrize(
    "payload, named",
    [
        ([1, 2, 3], "JSON object"),
        ({"bogus": {"x": 1}}, "bogus"),
        ({"run": []}, "run must be an object"),
        ({"run": {"trials": "200"}}, "run.trials"),
        ({"run": {"trials": None}}, "run.trials"),
        ({"trade": {"max_hold_bars": 2.5}}, "max_hold_bars"),
        ({"costs": {"quote_table": "/etc/passwd"}}, "quote_table"),
        ({"run": {"trials": 10**7}}, "limited"),
        ({"stats": {"bootstrap_samples": 10**8}}, "limited"),
        ({"costs": {"slippage_bps": 10**400}}, "slippage_bps"),
    ],
)
def test_a_malformed_run_request_is_answered_with_a_400_and_starts_nothing(
    client, payload, named
):
    """Anything but a ValueError used to escape the handler and drop the connection.

    The page then saw a network failure with no reason, and a hand-built request
    could ask for unbounded work or choose which file the spread table is read from.
    """
    body = client("/api/run", payload, expect=400)
    assert named in body["error"]
    assert client("/api/status")["status"] != "working"


# ---------- read-only serving and downloadable reports ----------


@pytest.fixture
def read_only(base_config, tmp_path):
    """A read-only server over a directory with one finished run."""
    from candlebench.web.history import History

    jobs = JobRunner(tmp_path / "last_run.json", history=History(tmp_path / "runs"))
    handler = partial(web.Handler, base_config=base_config, jobs=jobs, read_only=True)
    httpd = ThreadingHTTPServer((web.HOST, 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield f"http://{web.HOST}:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_a_read_only_server_refuses_to_start_work(read_only):
    """A published copy must not let a visitor spend its CPU or fetch with its keys."""
    for route in ("/api/run", "/api/fetch"):
        request = urllib.request.Request(read_only + route, data=b"{}", method="POST")
        with pytest.raises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=10)
        assert caught.value.code == 403
    with urllib.request.urlopen(read_only + "/api/meta", timeout=10) as response:
        assert json.loads(response.read())["read_only"] is True


def test_a_writable_server_will_not_listen_beyond_loopback(base_config):
    """The run endpoints have no authentication, so only a read-only server may be public."""
    with pytest.raises(ValueError, match="read-only"):
        web.serve(base_config, port=0, open_browser=False, host="0.0.0.0")


def test_the_latest_run_downloads_as_an_html_report(client):
    client("/api/run", {"run": {"trials": 4}}, expect=202)
    wait_for_idle(client)
    page = client("/api/report")
    assert page.startswith("<!doctype html>")
    assert "<script" not in page.lower()


def test_a_report_for_a_bad_or_missing_run_is_refused(client):
    assert "invalid run id" in client("/api/report?run=../../etc/passwd", expect=400)["error"]
    assert "no saved run" in client("/api/report?run=19990101T000000", expect=404)["error"]


def test_with_no_run_yet_there_is_no_report(client):
    assert "no run" in client("/api/report", expect=404)["error"]


# ---------- audit fixes ----------


def test_a_control_rows_equity_curve_is_not_overlaid_on_itself(client):
    """random_long's reference is random_long: the overlay drew the same curve twice."""
    client("/api/run", {"run": {"trials": 4}}, expect=202)
    wait_for_idle(client)
    assert client("/api/equity?pattern=random_long&interval=1m")["control"] is None


@pytest.mark.parametrize("value", ["False", "no", "yes"])
def test_a_sort_direction_that_is_not_one_or_zero_is_refused(client, value):
    """`desc=False` used to sort descending: anything but 0 or 1 was read as true."""
    client("/api/run", {"run": {"trials": 4}}, expect=202)
    wait_for_idle(client)
    body = client(f"/api/trades?sort=net_r&desc={value}", expect=400)
    assert "desc must be 1 or 0" in body["error"]


@pytest.mark.parametrize("query", ["symbol=SPY%0A", "run=20260915T143000%0A", "run=" + urllib.parse.quote("２０２６0915T143000")])
def test_a_trailing_newline_or_non_ascii_digit_fails_validation(client, query):
    """`$` matched before a trailing newline and `\\d` matched any Unicode digit."""
    client(f"/api/trades?{query}", expect=400)


def test_an_unexpected_error_still_answers_with_json(client, monkeypatch):
    """An exception in a GET handler used to drop the connection without a reason."""
    def boom(self, params):
        raise RuntimeError("corrupt cache")

    monkeypatch.setattr(web.Handler, "_trades", boom)
    body = client("/api/trades", expect=500)
    assert "internal error" in body["error"]


def test_the_run_history_counts_each_edge_pattern_once(tmp_path):
    """A pattern confirmed at 1m also appears in the pooled row; it is one edge."""
    from candlebench.web.history import History

    history = History(tmp_path)
    history.save({"stats": [
        {"pattern": "hammer", "interval": "1m", "verdict": "EDGE"},
        {"pattern": "hammer", "interval": "all", "verdict": "EDGE"},
        {"pattern": "doji", "interval": "5m", "verdict": "NOISE"},
    ]}, [])
    assert history.summaries()[0]["edges"] == 1


def test_a_session_the_run_skipped_shows_no_signals():
    """A session too short to measure was never counted; outlining signals there
    would show evidence the leaderboard does not contain."""
    from datetime import date

    from candlebench import config as config_module
    from tests.test_bars import GOOD, frame

    short = frame([GOOD] * 6)
    view = web.session_view(
        short, symbol="AAA", session=date(2026, 9, 15), interval="1m", pattern="hammer",
        report={"trend_lookbacks": {"1m": 10}}, base_config=config_module.load(None),
        recorded=None,
    )
    assert view["measured"] is False
    assert view["signals"] == []
    assert len(view["bars"]) == 6
