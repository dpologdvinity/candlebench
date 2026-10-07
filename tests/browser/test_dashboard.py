"""Browser-visible behavior over the real local server and background jobs."""
import copy
import re

import pytest

pytest.importorskip("playwright", reason="browser tests require: pip install -e '.[browser]'")
from playwright.sync_api import expect

pytestmark = pytest.mark.browser


def open_dashboard(page, app):
    page.goto(app.url)
    expect(page).to_have_url(app.url + "/")
    expect(page).to_have_title(re.compile("candlebench", re.IGNORECASE))
    expect(page.locator("#table tbody tr")).to_have_count(4)
    expect(page.locator("vite-error-overlay, nextjs-portal")).to_have_count(0)
    expect(page.locator("#cache-hint")).to_contain_text("Cached")
    expect(page.locator("#run")).to_be_enabled()


def test_interval_choice_is_submitted_once_and_progress_completes(page, app):
    open_dashboard(page, app)
    page.locator('#intervals [data-interval="5m"]').click()
    expect(page.locator('#intervals [data-interval="5m"]')).to_have_attribute("aria-pressed", "true")
    page.locator("#trials").fill("8")
    page.locator("#seed").fill("73")
    page.locator("#reward").fill("3")
    page.locator("#cost-model").select_option("fixed")
    page.locator("#slippage").fill("4")
    page.locator("#holdout-fraction").fill("0.25")
    page.locator("#min-sessions").fill("12")
    page.locator("#bootstrap-samples").fill("500")
    page.locator("#experiment-count").fill("3")
    page.locator("#run").click()
    expect(page.locator("#run")).to_be_disabled()
    expect(page.locator("#fetch")).to_be_disabled()
    expect(page.locator("#progress-text")).to_contain_text("fixture discovery")
    assert app.requests[-1]["run"]["intervals"] == ["1m", "5m"]
    assert app.requests[-1]["run"]["trials"] == 8
    assert app.requests[-1]["run"]["seed"] == 73
    assert app.requests[-1]["run"]["holdout_fraction"] == 0.25
    assert app.requests[-1]["stats"]["min_sessions"] == 12
    assert app.requests[-1]["stats"]["bootstrap_samples"] == 500
    assert app.requests[-1]["stats"]["experiment_count"] == 3
    assert app.requests[-1]["trade"]["reward_multiple"] == 3
    assert app.requests[-1]["costs"] == {"model": "fixed", "slippage_bps": 4}
    app.release.set()
    expect(page.locator("#run")).to_be_enabled()
    expect(page.locator("#fetch")).to_be_enabled()
    expect(page.locator("#progress-text")).to_have_text("complete")
    expect(page.locator("#summary-body")).to_contain_text("73")


@pytest.mark.expected_http_error(503)
def test_boot_results_failure_is_visible_without_unhandled_exception(page, app):
    page.route("**/api/results", lambda route: route.fulfill(
        status=503, json={"error": "fixture results unavailable"},
    ))
    page.goto(app.url)
    expect(page.locator("#banner")).to_contain_text("fixture results unavailable")
    expect(page.locator("#run")).to_be_enabled()


def test_leaderboard_sorts_both_directions_with_unavailable_last(page, app):
    open_dashboard(page, app)
    header = page.locator('#table th[data-key="expectancy_r"]')
    header.click()
    expect(page.locator("#table tbody tr").first).to_have_attribute("data-pattern", "hammer")
    expect(page.locator("#table tbody tr").nth(1)).to_have_attribute("data-pattern", "random_long")
    expect(page.locator("#table tbody tr").last).to_have_attribute("data-pattern", "dragonfly_doji")
    # A measurable zero retains its sign and is not rendered as unavailable.
    expect(page.locator('#table tr[data-pattern="random_long"]')).to_contain_text("+0.00")
    expect(page.locator('#table tr[data-pattern="dragonfly_doji"] .na').first).to_have_text("n/a")
    header.click()
    expect(page.locator("#table tbody tr").first).to_have_attribute("data-pattern", "bullish_engulfing")
    expect(page.locator("#table tbody tr").last).to_have_attribute("data-pattern", "dragonfly_doji")


def net_returns(page):
    return [float(value) for value in page.locator(
        "#trades-table tbody tr td:last-child"
    ).all_text_contents()]


def test_trade_sort_is_preserved_across_pages_and_reset_for_new_sort(page, app):
    open_dashboard(page, app)
    page.locator('#table tbody tr[data-pattern="hammer"]').click()
    expect(page.locator("#detail-who")).to_contain_text("hammer")
    expect(page.locator("#trades-count")).to_have_text("Trades 1–100 of 205")
    expect(page.locator("#trades-prev")).to_be_disabled()
    expect(page.locator("#trades-next")).to_be_enabled()
    expect(page.locator("#chart-equity svg")).to_be_visible()
    expect(page.locator("#breakdown-body")).to_contain_text("AAPL")
    expect(page.locator("#trades-table tbody .na")).to_have_text("n/a")
    page.locator('#trades-table th[data-trade-key="net_r"]').click()
    expect(page.locator("#trades-table tbody tr").first.locator("td").last).to_have_text("+1.02")
    first = net_returns(page)
    assert first == sorted(first, reverse=True)
    page.locator("#trades-next").click()
    expect(page.locator("#trades-count")).to_have_text("Trades 101–200 of 205")
    second = net_returns(page)
    assert second == sorted(second, reverse=True)
    assert first[-1] > second[0]
    page.locator("#trades-next").click()
    expect(page.locator("#trades-count")).to_have_text("Trades 201–205 of 205")
    expect(page.locator("#trades-next")).to_be_disabled()
    assert net_returns(page) == [-0.98, -0.99, -1.0, -1.01, -1.02]
    page.locator('#trades-table th[data-trade-key="net_r"]').click()
    expect(page.locator("#trades-count")).to_have_text("Trades 1–100 of 205")
    expect(page.locator("#trades-prev")).to_be_disabled()
    expect(page.locator("#trades-table tbody tr").first.locator("td").last).to_have_text("-1.02")
    page.locator("#detail-close").click()
    expect(page.locator("#detail")).to_be_hidden()


def test_empty_detail_preserves_unavailable_and_no_trade_states(page, app):
    open_dashboard(page, app)
    page.locator('#table tbody tr[data-pattern="dragonfly_doji"]').click()
    expect(page.locator("#trades-count")).to_contain_text("No trades")
    expect(page.locator("#trades-table tbody tr")).to_have_count(0)
    expect(page.locator("#trades-prev")).to_be_disabled()
    expect(page.locator("#trades-next")).to_be_disabled()
    expect(page.locator("#chart-equity")).to_contain_text("no trades")
    expect(page.locator("#breakdown-body")).to_contain_text("No trades to group")


def test_saved_run_comparison_explains_changed_settings_and_direction(page, app):
    open_dashboard(page, app)
    expect(page.locator("#compare")).to_be_visible()
    expect(page.locator("#compare-body")).to_contain_text("seed 42 → 43")
    expect(page.locator("#compare-body")).to_contain_text("reward 2 → 3")
    expect(page.locator("#compare-body tbody tr")).to_have_count(8)
    baseline = page.locator("#run-a").input_value()
    against = page.locator("#run-b").input_value()
    page.locator("#run-a").select_option(against)
    page.locator("#run-b").select_option(baseline)
    expect(page.locator("#compare-body")).to_contain_text("seed 43 → 42")
    expect(page.locator("#compare-body")).to_contain_text("reward 3 → 2")
    expect(page.locator("#compare-body")).not_to_contain_text("NaN")


@pytest.mark.expected_http_error(400)
def test_invalid_configuration_can_be_corrected_and_run_again(page, app):
    open_dashboard(page, app)
    page.locator("#trials").fill("0")
    page.locator("#run").click()
    expect(page.locator("#banner")).to_contain_text("trials")
    expect(page.locator("#run")).to_be_enabled()
    expect(page.locator("#fetch")).to_be_enabled()
    assert app.jobs.state["status"] == "idle"
    page.locator("#trials").fill("4")
    page.locator("#run").click()
    expect(page.locator("#run")).to_be_disabled()
    app.release.set()
    expect(page.locator("#progress-text")).to_have_text("complete")
    expect(page.locator("#banner")).to_be_hidden()


def test_background_job_failure_recovers_without_stale_disabled_buttons(page, app):
    open_dashboard(page, app)
    app.failure = "fixture cache is incomplete"
    page.locator("#run").click()
    expect(page.locator("#run")).to_be_disabled()
    app.release.set()
    expect(page.locator("#banner")).to_contain_text("fixture cache is incomplete")
    expect(page.locator("#run")).to_be_enabled()
    expect(page.locator("#fetch")).to_be_enabled()
    app.failure = None
    app.release.clear()
    page.locator("#run").click()
    expect(page.locator("#run")).to_be_disabled()
    expect(page.locator("#banner")).to_be_hidden()
    app.release.set()
    expect(page.locator("#progress-text")).to_have_text("complete")
    expect(page.locator("#run")).to_be_enabled()


@pytest.mark.expected_http_error(503)
def test_completed_run_results_failure_is_visible_and_recoverable(page, app):
    open_dashboard(page, app)
    page.route("**/api/results", lambda route: route.fulfill(
        status=503, json={"error": "fixture report could not be read"},
    ))
    page.locator("#run").click()
    app.release.set()
    expect(page.locator("#banner")).to_contain_text("fixture report could not be read")
    expect(page.locator("#run")).to_be_enabled()
    expect(page.locator("#fetch")).to_be_enabled()
    page.unroute("**/api/results")
    page.locator("#run").click()
    expect(page.locator("#progress-text")).to_have_text("complete")
    expect(page.locator("#banner")).to_be_hidden()


@pytest.mark.expected_http_error(503)
def test_boot_metadata_failure_is_visible_and_reload_recovers(page, app):
    page.route("**/api/meta", lambda route: route.fulfill(
        status=503, json={"error": "fixture metadata unavailable"},
    ))
    page.goto(app.url)
    expect(page.locator("#banner")).to_contain_text("fixture metadata unavailable")
    page.unroute("**/api/meta")
    page.reload()
    expect(page.locator("#table tbody tr")).to_have_count(4)
    expect(page.locator("#banner")).to_be_hidden()


def test_mobile_controls_and_table_remain_usable(page, app):
    page.set_viewport_size({"width": 390, "height": 844})
    open_dashboard(page, app)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.locator('#table th[data-key="expectancy_r"]').click()
    expect(page.locator("#table tbody tr").first).to_have_attribute("data-pattern", "hammer")
    page.locator('#table tbody tr[data-pattern="hammer"]').click()
    expect(page.locator("#trades-count")).to_have_text("Trades 1–100 of 205")
    expect(page.locator("#trades-next")).to_be_enabled()


def test_validation_evidence_and_drilldown_use_the_reserved_sample(page, app):
    from candlebench import trades

    payload = copy.deepcopy(app.jobs.results)
    payload["validation"].update({
        "evaluated": True, "validation_sessions": 12,
        "candidates": [{"pattern": "hammer", "interval": "all"}],
    })
    for row in payload["stats"]:
        if row["pattern"] == "hammer":
            row["discovery_verdict"] = "EDGE"
            row["validation"] = dict(row, expectancy_r=-0.3, ci_low=-0.5,
                ci_high=-0.1, verdict="NEGATIVE", p_delta_adjusted=0.6)
            row["validation"].pop("validation")
    payload["trials"] = [
        {"symbol": "AAPL", "session": "2026-09-01", "sample": "discovery"},
        {"symbol": "AAPL", "session": "2026-09-20", "sample": "validation"},
    ]
    page.route("**/api/results", lambda route: route.fulfill(json=payload))
    # Exercise production sample filtering against actual stored trade rows.
    frame = trades.read(app.jobs.trades_path)
    reserved = frame.iloc[:3].copy()
    reserved["sample"] = "validation"
    reserved["session"] = "2026-09-20"
    reserved["net_r"] = [-0.4, -0.3, -0.2]
    import pandas as pd
    pd.concat([frame, reserved], ignore_index=True).to_parquet(app.jobs.trades_path, index=False)
    requests = []
    page.on("request", lambda request: requests.append(request.url))
    page.route(re.compile(r"/api/session\?"), lambda route: route.fulfill(json={
        "symbol": "AAPL", "session": "2026-09-20", "kind": "pattern",
        "bars": [
            {"t": "09:30", "o": 100, "h": 101, "l": 99, "c": 100.5, "v": 100},
            {"t": "09:31", "o": 100.5, "h": 101, "l": 100, "c": 100.2, "v": 100},
        ], "signals": [], "trades": [], "trend_lookback": 2,
    }))
    open_dashboard(page, app)
    expect(page.locator("#validation-summary")).to_contain_text("2026-09-20")
    expect(page.locator("#validation-summary")).to_contain_text("invalidates confirmation")
    row = page.locator('#table tbody tr[data-pattern="hammer"]')
    expect(row).to_contain_text("NEGATIVE")
    expect(row).to_contain_text("0.02")
    row.click()
    expect(page.locator("#detail-sample")).to_have_value("discovery")
    expect(page.locator("#trades-count")).to_have_text("Trades 1–100 of 205")
    expect(page.locator("#session-day")).to_have_value("2026-09-01")
    page.locator("#detail-sample").select_option("validation")
    expect(page.locator("#trades-count")).to_have_text("Trades 1–3 of 3")
    expect(page.locator("#session-day")).to_have_value("2026-09-20")
    assert net_returns(page) == [-0.4, -0.3, -0.2]
    for endpoint in ("trades", "equity", "breakdown", "session"):
        assert any(f"/api/{endpoint}?" in url and "sample=validation" in url for url in requests)
    page.locator("#detail-close").click()
    page.locator('#table tbody tr[data-pattern="bullish_engulfing"]').click()
    expect(page.locator('#detail-sample option[value="validation"]')).to_have_attribute("disabled", "")
    expect(page.locator("#detail-sample")).to_have_value("discovery")


def test_legacy_report_does_not_claim_validation(page, app):
    payload = copy.deepcopy(app.jobs.results)
    payload.pop("validation")
    payload.pop("inference", None)
    for row in payload["stats"]:
        for key in ("sessions", "baseline_ci_low", "baseline_ci_high",
                    "p_expectancy_adjusted", "p_delta_adjusted", "discovery_verdict", "validation"):
            row.pop(key)
    page.route("**/api/results", lambda route: route.fulfill(json=payload))
    open_dashboard(page, app)
    expect(page.locator("#validation-summary")).to_contain_text(
        "Historical report: validation evidence unavailable"
    )
    expect(page.locator("#table")).not_to_contain_text("NaN")
    expect(page.locator("#caveats")).not_to_contain_text("Verdicts use Holm")
    page.locator('#table tbody tr[data-pattern="hammer"]').click()
    expect(page.locator('#detail-sample option[value="validation"]')).to_have_attribute("disabled", "")


def test_infinite_profit_factor_is_valid_json_and_visible(page, app):
    # All-winning runs legitimately have infinite profit factor. Legacy JSON
    # can contain this nonfinite number; the HTTP surface must normalize it.
    for row in app.jobs.results["stats"]:
        if row["pattern"] == "hammer":
            row["profit_factor"] = float("inf")
    open_dashboard(page, app)
    expect(page.locator('#table tbody tr[data-pattern="hammer"]')).to_contain_text("inf")
    expect(page.locator("#table")).not_to_contain_text("NaN")


def test_source_change_refreshes_cache_for_the_selected_provider(page, app):
    open_dashboard(page, app)
    page.locator("#source").select_option("alpaca")
    page.locator('#intervals [data-interval="1s"]').click()
    expect(page.locator('#intervals [data-interval="1s"]')).to_have_attribute("aria-pressed", "true")
    expect(page.locator("#cache-hint")).to_contain_text("1s 12 sessions")
    expect(page.locator("#cache-hint")).not_to_contain_text("Could not")
    page.locator("#source").select_option("yfinance")
    expect(page.locator('#intervals [data-interval="1s"]')).to_have_count(0)
    expect(page.locator("#cache-hint")).to_contain_text("1m 12 sessions")


def test_negative_equity_drawdown_shade_starts_at_initial_zero(page, app):
    from candlebench import trades

    frame = trades.read(app.jobs.trades_path)
    frame["net_r"] = -0.01
    frame.to_parquet(app.jobs.trades_path, index=False)
    open_dashboard(page, app)
    curve = page.request.get(app.url + "/api/equity?pattern=hammer&sample=discovery").json()["pattern"]
    assert curve["points"][0] == pytest.approx(-0.01)
    assert curve["max_drawdown_r"] == pytest.approx(2.05)
    page.locator('#table tbody tr[data-pattern="hammer"]').click()
    expect(page.locator("#chart-equity")).to_contain_text("deepest drawdown 2.05R")
    shade = page.locator('#chart-equity rect[fill="var(--negative)"]')
    expect(shade).to_have_count(1)
    # The band begins on the zero-equity line and spans the full loss, including
    # the very first trade; SVG geometry agrees with the server's 2.05R decline.
    zero = page.locator('#chart-equity line[stroke="var(--line)"]')
    assert float(shade.get_attribute("y")) == float(zero.get_attribute("y1"))
    assert float(shade.get_attribute("height")) == pytest.approx(222.0)


def test_keyboard_can_toggle_interval_and_start_a_run(page, app):
    open_dashboard(page, app)
    interval = page.locator('#intervals [data-interval="5m"]')
    interval.focus()
    interval.press("Space")
    expect(interval).to_have_attribute("aria-pressed", "true")
    interval.press("Space")
    expect(interval).to_have_attribute("aria-pressed", "false")
    page.locator("#run").focus()
    page.locator("#run").press("Enter")
    expect(page.locator("#run")).to_be_disabled()
    assert app.requests[-1]["run"]["intervals"] == ["1m"]
    app.release.set()
    expect(page.locator("#progress-text")).to_have_text("complete")
    expect(page.locator("#run")).to_be_enabled()


def test_the_cost_headline_matches_the_cost_line_under_quoted_costs(page, app):
    """A quoted run charged 7.50 bps per leg once headlined the estimator's 1.00 as "measured"."""
    payload = copy.deepcopy(app.jobs.results)
    payload["config"]["costs"]["model"] = "quoted"
    payload.update(spread_bps=2.0, spread_interval="1m", charged_bps=7.5, quoted_share=1.0,
                   costs_description="a quoted 7.50 bps per executed leg")
    page.route("**/api/results", lambda route: route.fulfill(json=payload))
    open_dashboard(page, app)
    expect(page.locator("#summary-body")).to_contain_text("7.50")
    expect(page.locator("#summary-body")).to_contain_text("bps paid per leg (quoted)")
    expect(page.locator("#summary-body")).not_to_contain_text("1.00")


def test_the_run_provenance_is_shown_with_the_caveats(page, app):
    """A run that cannot say which code and data produced it cannot be compared with another."""
    payload = copy.deepcopy(app.jobs.results)
    payload["provenance"] = {
        "created_at": "2026-10-07T21:00:00+00:00", "candlebench": "0.2.0",
        "git": {"commit": "abcdef1234567890", "dirty": True},
        "config_sha256": "1234567890abcdef", "python": "3.12.3", "packages": {},
        "data": {"source": "alpaca", "sha256": "fedcba0987654321", "files": 50, "missing": 0},
    }
    page.route("**/api/results", lambda route: route.fulfill(json=payload))
    open_dashboard(page, app)
    caveats = page.locator("#caveats")
    expect(caveats).to_contain_text("abcdef1234 (uncommitted changes)")
    expect(caveats).to_contain_text("alpaca data fedcba0987 over 50 cached file(s)")


def test_a_report_without_provenance_says_so(page, app):
    open_dashboard(page, app)
    expect(page.locator("#caveats")).to_contain_text("provenance were not recorded")
