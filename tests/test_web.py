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
                    cache_dir=str(tmp_path), throttle_s=0.0),
        universe=replace(UniverseConfig(), symbols=SYMBOLS, sample_size=2),
        stats=replace(StatsConfig(), bootstrap_samples=100, min_trades=5),
        patterns=("hammer", "bullish_engulfing", "random_long", "random_short"),
    )


@pytest.fixture
def client(base_config, tmp_path):
    """A live server on an ephemeral port, torn down after the test."""
    jobs = JobRunner(tmp_path / "last_run.json")
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
    assert meta["intervals"][0] == "1m"
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
    body = client("/api/trades?interval=1s", expect=400)
    assert "unsupported interval" in body["error"]


def test_a_trade_symbol_that_could_escape_the_cache_is_rejected(client):
    body = client("/api/trades?symbol=../../../etc/passwd", expect=400)
    assert "invalid symbol" in body["error"]


def test_a_non_numeric_page_parameter_is_a_400_not_a_crash(client):
    for query in ("/api/trades?limit=lots", "/api/trades?offset=-1"):
        assert client(query, expect=400)["error"]


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
    body = client("/api/run", {"run": {"intervals": ["1s"]}}, expect=400)
    assert "tick data provider" in body["error"]


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
