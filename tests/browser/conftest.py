"""Real Chromium and HTTP-server fixtures; no market-data network is used."""
from __future__ import annotations

import copy
import json
import re
import threading
from dataclasses import dataclass, replace
from datetime import date, timedelta
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import pytest

# Core installs can run pytest without downloading a development-only browser.
# An installed Playwright with a missing Chromium executable fails explicitly.
try:
    from playwright import sync_api as playwright
except ModuleNotFoundError as exc:
    if exc.name != "playwright":
        raise
    playwright = None

from candlebench import leaderboard, trades
from candlebench.config import Config, RunConfig, UniverseConfig
from candlebench.engine import Trade
from candlebench.web import server as web
from candlebench.web.history import History
from candlebench.web.jobs import JobResult, JobRunner

ARTIFACTS = Path("/tmp/candlebench-browser-qa")


def report(config):
    """Pinned aggregates include positive, negative, real zero and unavailable."""
    rows = []
    for interval in ("1m", "all"):
        for name, value, kind in (
            ("hammer", 0.5, "pattern"),
            ("bullish_engulfing", -0.3, "pattern"),
            ("random_long", 0.0, "control"),
            ("dragonfly_doji", None, "pattern"),
        ):
            missing = value is None
            rows.append({
                "pattern": name, "interval": interval, "kind": kind,
                "trades": 0 if missing else 205, "signals": 0 if missing else 250,
                "sessions": 0 if missing else 12,
                "win_rate": None if missing else 0.5, "expectancy_r": value,
                "ci_low": None if missing else value - 0.1,
                "ci_high": None if missing else value + 0.1,
                "baseline_delta_r": value if kind == "pattern" else None,
                "baseline_ci_low": None if missing or kind == "control" else value - 0.15,
                "baseline_ci_high": None if missing or kind == "control" else value + 0.15,
                "p_expectancy_adjusted": None if missing else 0.01,
                "p_delta_adjusted": None if missing or kind == "control" else 0.02,
                "profit_factor": None if missing else 1.0,
                "consistency": None if missing else 0.5,
                "stability": None if missing else 0.5,
                "max_drawdown_r": None if missing else 1.0,
                "verdict": "INSUFFICIENT" if missing else "NOISE",
                "discovery_verdict": "INSUFFICIENT" if missing else "NOISE",
                "validation": None,
            })
    return {
        "config": leaderboard.payload_config(config), "stats": rows,
        "trials": [], "sessions_evaluated": 12, "warnings": [],
        "spread_bps": None, "spread_interval": None,
        "costs_description": "fixed slippage", "cost_dominated": [],
        "validation": {
            "enabled": True, "cutoff": "2026-09-20",
            "discovery_trials": 3, "validation_trials": 1,
            "candidates": [], "evaluated": False,
            "discovery_sessions": 12, "validation_sessions": 0,
            "warning": "Reusing validation to choose settings invalidates confirmation.",
        },
    }


def trade_rows():
    return [Trade(
        pattern="hammer", interval="1m", symbol="AAPL",
        session=date(2026, 9, 1) + timedelta(days=i // 20), trial_index=i,
        direction=1, entry_index=i % 20 + 1, exit_index=i % 20 + 3,
        entry_price=100.0, exit_price=100.0 + (i - 102) / 100,
        stop_price=99.0, target_price=102.0, risk_per_share=1.0,
        exit_reason="timeout", gross_r=(i - 102) / 100,
        net_r=(i - 102) / 100, return_pct=(i - 102) / 100,
        entry_minute=None if i == 0 else i % 20,
    ) for i in range(205)]


@dataclass
class App:
    url: str
    jobs: JobRunner
    config: Config
    release: threading.Event
    requests: list
    failure: str | None = None


@pytest.fixture
def app(tmp_path, monkeypatch):
    config = Config(
        run=replace(RunConfig(), trials=4, intervals=("1m",), cache_dir=str(tmp_path)),
        universe=replace(UniverseConfig(), symbols=("AAPL",), sample_size=1),
        patterns=("hammer", "bullish_engulfing", "dragonfly_doji", "random_long"),
    )
    payload = report(config)
    results = tmp_path / "last_run.json"
    results.write_text(json.dumps(payload))
    history = History(tmp_path / "runs")
    history.save(payload, trade_rows())
    newer = copy.deepcopy(payload)
    newer["config"]["run"]["seed"] += 1
    newer["config"]["trade"]["reward_multiple"] += 1
    history.save(newer, trade_rows())
    jobs = JobRunner(results, history=history)
    trades.write(trade_rows(), jobs.trades_path)
    app = App("", jobs, config, threading.Event(), [])

    def prohibited_market_fetch(*args, **kwargs):
        raise AssertionError("browser tests must never request live market data")

    monkeypatch.setattr(web.bars, "warm_cache", prohibited_market_fetch)
    # Cache state is deterministic and independent of external provider APIs.
    monkeypatch.setattr(web, "describe_cache", lambda c: {
        "requested_symbols": 1,
        "intervals": {iv: {"symbols": 1, "sessions": 12}
                      for iv in web.bars.SOURCES[c.run.source].intervals},
    })

    class FixtureHandler(web.Handler):
        def _start_run(self, body):
            app.requests.append(copy.deepcopy(body))
            try:
                chosen = web.config_from_request(self.base_config, body)
            except ValueError as exc:
                self._json(400, {"error": str(exc)})
                return

            def work(state):
                state.total, state.done, state.message = 4, 1, "fixture discovery"
                if not app.release.wait(timeout=15):
                    raise TimeoutError("browser test did not release background job")
                if app.failure:
                    raise RuntimeError(app.failure)
                return JobResult(report(chosen), trade_rows())

            if self.jobs.submit("run", work):
                self._json(202, {"started": True})
            else:
                self._json(409, {"error": "a job is already running"})

    httpd = ThreadingHTTPServer((web.HOST, 0), partial(
        FixtureHandler, base_config=config, jobs=jobs,
    ))
    app.url = f"http://{web.HOST}:{httpd.server_address[1]}"
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield app
    finally:
        app.release.set()
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
        if jobs._thread:
            jobs._thread.join(timeout=5)


@pytest.fixture(scope="session")
def browser():
    if playwright is None:
        pytest.skip("browser tests require: pip install -e '.[browser]'")
    with playwright.sync_playwright() as driver:
        instance = driver.chromium.launch()
        yield instance
        instance.close()


@pytest.fixture
def page(browser, app, request):
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    page.set_default_timeout(5000)
    page_errors, console_errors, external_requests = [], [], []
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    page.on("console", lambda message: console_errors.append(message.text)
            if message.type == "error" else None)

    def local_only(route):
        if urlparse(route.request.url).hostname != "127.0.0.1":
            external_requests.append(route.request.url)
            route.abort()
        else:
            route.continue_()

    page.route("**/*", local_only)
    yield page
    ARTIFACTS.mkdir(exist_ok=True)
    page.screenshot(path=str(ARTIFACTS / f"{request.node.name}.png"), full_page=True)
    if request.node.name in {
        "test_interval_choice_is_submitted_once_and_progress_completes",
        "test_mobile_controls_and_table_remain_usable",
    }:
        page.screenshot(path=str(ARTIFACTS / f"{request.node.name}-viewport.png"))
    page.close()
    expected = request.node.get_closest_marker("expected_http_error")
    if expected:
        console_errors = [error for error in console_errors if not re.search(
            rf"Failed to load resource:.*status of ({'|'.join(map(str, expected.args))})", error
        )]
    assert not external_requests, f"Unexpected external requests: {external_requests}"
    assert not page_errors, f"Unhandled browser exceptions: {page_errors}"
    assert not console_errors, f"Browser console errors: {console_errors}"
