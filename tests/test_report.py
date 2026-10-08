"""The static HTML report.

A report is the form a result is shared in, so it must say exactly what the
run said, contain nothing it should not publish, and survive hostile text.
"""

from __future__ import annotations

import pytest

from candlebench import leaderboard, report, runner, trades
from tests.test_end_to_end import config, write_cache


@pytest.fixture(scope="module")
def finished(tmp_path_factory):
    cache = tmp_path_factory.mktemp("cache")
    write_cache(cache)
    cfg = config(cache)
    result = runner.run(cfg)
    return leaderboard.payload(result, cfg), trades.to_frame(result.trades)


def test_a_report_is_one_file_with_no_scripts(finished):
    """No scripts means it renders anywhere it is opened, and runs nothing when it is."""
    payload, frame = finished
    page = report.render(payload, frame)
    assert page.startswith("<!doctype html>")
    assert "<script" not in page.lower()
    assert "http" not in page.replace("https://github.com/dpologdvinity/candlebench", "")


def test_a_report_publishes_no_prices(finished):
    """Trades appear as R multiples and dates; a report of licensed data must not republish it."""
    payload, frame = finished
    page = report.render(payload, frame)
    for column in ("entry_price", "exit_price", "stop_price", "target_price"):
        assert column not in page
    some_price = f"{frame['entry_price'].iloc[0]:.2f}"
    assert some_price not in page


def test_every_pattern_and_interval_appears_with_its_verdict(finished):
    payload, _ = finished
    page = report.render(payload)
    for row in payload["stats"]:
        assert row["pattern"] in page
    assert "all timeframes pooled" in page
    assert "INSUFFICIENT" in page or "NOISE" in page


def test_an_unavailable_statistic_reads_n_a_never_zero(finished):
    """Unavailable is not zero, in a report as everywhere else."""
    payload, _ = finished
    row = dict(payload["stats"][0], ci_low=None, ci_high=None, baseline_delta_r=None)
    page = report.render({**payload, "stats": [row]})
    assert "n/a" in page


def test_text_from_the_run_is_escaped(finished):
    """Warnings and titles come from files a stranger could have written."""
    payload, _ = finished
    hostile = "<img src=x onerror=alert(1)>"
    page = report.render({**payload, "warnings": [hostile]}, title=hostile)
    assert hostile not in page
    assert "&lt;img src=x onerror=alert(1)&gt;" in page


def test_without_trades_the_report_has_no_drill_downs(finished):
    payload, _ = finished
    assert "Drill-down" not in report.render(payload)


def test_the_cost_headline_is_the_charged_figure_under_quoted_costs(finished):
    payload, _ = finished
    quoted = {**payload, "config": {**payload["config"], "costs": {"model": "quoted"}},
              "charged_bps": 7.5, "quoted_share": 1.0, "spread_bps": 2.0}
    assert report.cost_headline(quoted) == ("7.50", "bps paid per leg (quoted)")


def test_the_report_command_writes_a_file_and_refuses_a_non_result(finished, tmp_path):
    import json

    from candlebench import cli

    payload, frame = finished
    saved = tmp_path / "run.json"
    saved.write_text(json.dumps(payload))
    frame.to_parquet(tmp_path / "run.parquet", index=False)
    assert cli.main(["report", str(saved)]) == 0
    assert "Drill-down" in (tmp_path / "run.html").read_text()

    other = tmp_path / "other.json"
    other.write_text("[1, 2, 3]")
    assert cli.main(["report", str(other)]) == 1


def test_the_report_command_accepts_a_trade_file_published_without_prices(finished, tmp_path):
    """The published two-year results ship without price columns; they must still render."""
    import json

    from candlebench import cli, site

    payload, frame = finished
    saved = tmp_path / "run.json"
    saved.write_text(json.dumps(payload))
    frame.drop(columns=list(site.PRICE_COLUMNS)).to_parquet(tmp_path / "run.parquet", index=False)
    assert cli.main(["report", str(saved)]) == 0
    assert "Drill-down" in (tmp_path / "run.html").read_text()
