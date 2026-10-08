"""The static site exporter.

A published copy of a run must answer every question the dashboard asks,
leak nothing local, and publish prices only for synthetic data.
"""

from __future__ import annotations

import json

import pytest

from candlebench import bars, cli, leaderboard, runner, site, trades


@pytest.fixture(scope="module")
def demo_run(tmp_path_factory):
    cache = tmp_path_factory.mktemp("cache")
    cfg = cli.demo_config(cache, trials=24)
    symbols = cfg.universe.symbols
    bars.warm_cache(symbols, cfg.run.intervals, cfg.cache_path, 0.0, source="synthetic")
    result = runner.run(cfg)
    return cfg, leaderboard.payload(result, cfg), trades.to_frame(result.stored_trades)


def _read(path):
    return json.loads(path.read_text())


def _all_trades(out):
    """Every published trade, columns merged across the per-pattern files."""
    merged = {}
    for path in sorted((out / "data" / "trades").glob("*.json")):
        for column, values in _read(path)["data"].items():
            merged.setdefault(column, []).extend(values)
    return merged


def test_trades_are_split_by_pattern_and_keep_their_stored_order(demo_run, tmp_path):
    """The table loads one pattern's file, not every trade of the run."""
    _, payload, frame = demo_run
    out = site.export(tmp_path / "site", payload, frame, prices=False)
    own = frame[~frame["matched"]].reset_index(drop=True)
    seen = []
    for name in payload["config"]["patterns"]:
        file = _read(out / "data" / "trades" / f"{name}.json")
        assert set(file["data"]["pattern"]) <= {name}
        assert file["data"]["net_r"] == own.loc[file["order"], "net_r"].tolist()
        seen += file["order"]
    assert sorted(seen) == list(range(len(own)))
    assert not (out / "data" / "trades.json").exists()


def test_a_licensed_run_publishes_no_prices_and_no_charts(demo_run, tmp_path):
    """Session bars and trade prices are the data itself; statistics are not."""
    _, payload, frame = demo_run
    out = site.export(tmp_path / "site", payload, frame, prices=False)
    data = _all_trades(out)
    for column in site.PRICE_COLUMNS:
        assert all(v is None for v in data[column]), column
    assert any(v is not None for v in data["net_r"])
    assert not (out / "data" / "sessions").exists()
    assert _read(out / "data" / "meta.json")["prices"] is False
    some_price = f"{frame['entry_price'].iloc[0]:.4f}"
    assert some_price not in (out / "report.html").read_text()


def test_a_published_run_leaks_no_local_paths(demo_run, tmp_path):
    cfg, payload, frame = demo_run
    out = site.export(tmp_path / "site", payload, frame, prices=False)
    local = str(cfg.run.cache_dir)
    for path in out.rglob("*"):
        if path.is_file():
            assert local not in path.read_text(errors="ignore"), path


def test_a_synthetic_run_publishes_every_sampled_session(demo_run, tmp_path):
    _, payload, frame = demo_run
    out = site.export(tmp_path / "site", payload, frame, prices=True)
    pairs = {(t["symbol"], t["session"]) for t in payload["trials"]}
    written = {p.stem for p in (out / "data" / "sessions").glob("*.json")}
    assert written == {f"{s}_{d}" for s, d in pairs}


def test_every_pattern_has_curves_and_breakdowns_for_every_view(demo_run, tmp_path):
    _, payload, frame = demo_run
    out = site.export(tmp_path / "site", payload, frame, prices=False)
    views = [*payload["config"]["run"]["intervals"], "all"]
    for name in payload["config"]["patterns"]:
        file = _read(out / "data" / "patterns" / f"{name}.json")
        for view in views:
            for sample in ("discovery", "validation"):
                entry = file["views"][view][sample]
                assert set(entry["breakdowns"]) == set(trades.BREAKDOWNS)
                assert "points" in entry["equity"]


def test_the_page_loads_its_assets_relative_to_itself(demo_run, tmp_path):
    """A project Pages site lives under /<repo>/, so root-absolute paths would 404."""
    _, payload, frame = demo_run
    page = (site.export(tmp_path / "site", payload, frame, prices=False) / "index.html").read_text()
    assert 'href="app.css"' in page and 'src="app.js"' in page
    assert "/static/" not in page
    assert "window.CANDLEBENCH_STATIC" in page


def test_a_trade_file_published_without_prices_reads_back(demo_run, tmp_path):
    _, _, frame = demo_run
    path = tmp_path / "published.parquet"
    frame.drop(columns=list(site.PRICE_COLUMNS)).to_parquet(path, index=False)
    back = site.read_trades(path)
    assert list(back.columns) == list(trades.COLUMNS)
    assert back["entry_price"].isna().all()
    assert back["net_r"].tolist() == frame["net_r"].tolist()


def test_matched_controls_are_published_as_curves_not_as_trades(demo_run, tmp_path):
    """The table lists a pattern's own trades; its comparison is drawn, not listed."""
    _, payload, frame = demo_run
    out = site.export(tmp_path / "site", payload, frame, prices=False)
    data = _all_trades(out)
    assert not any(data["matched"])
    assert _read(out / "data" / "meta.json")["matched"] is True
    traded = next(s["pattern"] for s in payload["stats"]
                  if s["interval"] == "all" and s["kind"] == "pattern" and s["trades"])
    view = _read(out / "data" / "patterns" / f"{traded}.json")["views"]["all"]["discovery"]
    assert view["matched_equity"]["points"]
