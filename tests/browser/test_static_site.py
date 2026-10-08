"""The static site answers every dashboard query exactly as the server does.

The same synthetic run is served twice: by the real server, and as files from
`candlebench.site`. Each query the page can make is sent to both, and the
answers must be identical. A divergence would mean the published copy shows
numbers the run never produced.
"""
from __future__ import annotations

import functools
import shutil
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pytest

pytest.importorskip("playwright", reason="browser tests require: pip install -e '.[browser]'")
from playwright.sync_api import expect  # noqa: E402

from candlebench import bars, cli, leaderboard, runner, site, trades  # noqa: E402
from candlebench.web import server as web  # noqa: E402
from candlebench.web.history import History  # noqa: E402
from candlebench.web.jobs import JobResult, JobRunner  # noqa: E402

pytestmark = pytest.mark.browser


def _serve(handler):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


@pytest.fixture(scope="module")
def both(tmp_path_factory):
    root = tmp_path_factory.mktemp("static")
    cfg = cli.demo_config(root / "cache", trials=24)
    bars.warm_cache(cfg.universe.symbols, cfg.run.intervals, cfg.cache_path, 0.0, source="synthetic")
    result = runner.run(cfg)
    payload = leaderboard.payload(result, cfg)

    jobs = JobRunner(root / "server" / "last_run.json", history=History(root / "server" / "runs"))
    jobs.submit("run", lambda state: JobResult(payload, result.stored_trades))
    jobs._thread.join()
    live, live_url = _serve(functools.partial(web.Handler, base_config=cfg, jobs=jobs))

    out = site.export(root / "site", payload, trades.to_frame(result.stored_trades), prices=True,
                      base_config=cfg)
    files, files_url = _serve(functools.partial(SimpleHTTPRequestHandler, directory=str(out)))
    try:
        yield payload, live_url, files_url
    finally:
        for httpd in (live, files):
            httpd.shutdown()
            httpd.server_close()
        shutil.rmtree(root, ignore_errors=True)


def _queries(payload):
    names = ["hammer", "bearish_engulfing", "random_long"]
    out = []
    for interval in ["", *payload["config"]["run"]["intervals"]]:
        iv = f"&interval={interval}" if interval else ""
        for name in names:
            out.append(f"/api/equity?pattern={name}{iv}&sample=discovery")
            for by in trades.BREAKDOWNS:
                out.append(f"/api/breakdown?by={by}&pattern={name}{iv}&sample=discovery")
            out.append(f"/api/trades?pattern={name}{iv}&sample=discovery&limit=100&offset=0")
        # Across patterns, the page must rebuild the server's stored order.
        out.append(f"/api/trades?sample=discovery{iv}&limit=100&offset=150")
        out.append(f"/api/trades?sample=discovery{iv}&limit=100&offset=0&sort=net_r&desc=1")
        for sort in ("net_r", "symbol", "entry_minute", "session", "exit_reason"):
            for desc in (0, 1):
                out.append(f"/api/trades?pattern=hammer{iv}&sample=discovery&limit=50&offset=50"
                           f"&sort={sort}&desc={desc}")
    trial = payload["trials"][0]
    for interval in payload["config"]["run"]["intervals"]:
        for name in ("hammer", "tweezer_top", "random_short"):
            out.append(f"/api/session?symbol={trial['symbol']}&session={trial['session']}"
                       f"&interval={interval}&pattern={name}&sample=discovery")
    return out


def test_every_query_the_page_makes_gets_the_servers_answer(browser, both):
    payload, live_url, files_url = both
    page = browser.new_page()
    try:
        page.goto(files_url + "/index.html")
        expect(page.locator("#table tbody tr").first).to_be_visible(timeout=20000)
        mismatches, with_trades, with_bars = [], 0, 0
        for query in _queries(payload):
            published = page.evaluate("q => api(q)", query)
            served = page.request.get(live_url + query).json()
            if published != served:
                mismatches.append(query)
            with_trades += bool(served.get("trades"))
            with_bars += bool(served.get("bars"))
        assert not mismatches, f"{len(mismatches)} differ, first: {mismatches[0]}"
        # Both sides once agreed on an empty comparison curve.
        hammer = page.evaluate("q => api(q)", "/api/equity?pattern=hammer&sample=discovery")
        assert hammer["control"]["kind"] == "matched" and hammer["control"]["points"]
        # Agreement on empty answers would prove nothing.
        assert with_trades > 20 and with_bars >= 6
    finally:
        page.close()


def test_the_published_page_is_read_only_and_works_without_a_server(browser, both):
    _, _, files_url = both
    page = browser.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    try:
        page.goto(files_url + "/index.html")
        expect(page.locator("#banner")).to_contain_text("static copy")
        expect(page.locator("#run")).to_be_hidden()
        page.locator("#table tbody tr[data-pattern]").first.click()
        expect(page.locator("#chart-equity svg")).to_be_visible(timeout=20000)
        expect(page.locator("#chart-session svg")).to_be_visible(timeout=20000)
        expect(page.locator("#trades-count")).to_contain_text("of")
        expect(page.locator("#download-report")).to_have_attribute("href", "report.html")
        assert not errors
    finally:
        page.close()


def test_a_failed_trade_download_is_reported_not_shown_as_no_trades(browser, both):
    """Unavailable is not zero: only a missing pattern file means no trades."""
    _, _, files_url = both
    page = browser.new_page()
    try:
        page.route("**/data/trades/*.json", lambda route: route.fulfill(status=500, body="boom"))
        page.goto(files_url + "/index.html")
        page.locator("#table tbody tr[data-pattern]").first.click()
        expect(page.locator("#trades-count")).to_contain_text("not part of this published run",
                                                              timeout=20000)
        expect(page.locator("#trades-count")).not_to_contain_text("of 0")
    finally:
        page.close()
