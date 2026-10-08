"""A saved run as an interactive static site: the dashboard, with no server.

GitHub Pages serves files, not queries. Everything the dashboard asks the
server for is therefore either written here, computed by the same Python the
server calls (equity curves, breakdowns, session charts), or left to the page
for the one thing too large to precompute: the trade table's filtering,
sorting and paging. An exhaustive set of trade pages, every sort order and
page of every view, would exceed the 1 GB a Pages site may hold; compact trade
files and a few lines of sorting in the page do not.

Trades are written one file per pattern, because the table always shows one
pattern's. A single file for a 2,500-trial run at 1m held 240,000 trades, 52 MB
for a phone to download and parse before showing the first row; the largest
pattern's file is a seventh of that. Each row keeps its position in the full
frame, so a query across patterns can restore the stored order the server uses.

Prices are published only for synthetic markets. For licensed data the trade
file keeps R multiples, dates and times but nulls every price, and no session
charts are written, because a chart is the bars themselves.
"""

from __future__ import annotations

import json
import math
import shutil
from dataclasses import replace
from datetime import date
from pathlib import Path

import pandas as pd

from candlebench import bars, leaderboard, trades
from candlebench.config import RANK_KEYS, Config
from candlebench.patterns.control import CONTROLS

STATIC_DIR = Path(__file__).parent / "web" / "static"
POOLED = "all"
SAMPLES = ("discovery", "validation")
# Price levels, and the two figures computed directly from them.
PRICE_COLUMNS = ("entry_price", "exit_price", "stop_price", "target_price",
                 "risk_per_share", "return_pct")


def strip_prices(frame: pd.DataFrame) -> pd.DataFrame:
    """The trade frame with every price-derived column emptied."""
    out = frame.copy()
    for column in PRICE_COLUMNS:
        if column in out.columns:
            out[column] = float("nan")
    return out


def read_trades(path: str | Path) -> pd.DataFrame:
    """A trade file, including one published without its price columns."""
    frame = pd.read_parquet(path)
    for column in PRICE_COLUMNS:
        if column not in frame.columns:
            frame[column] = float("nan")
    if "sample" not in frame.columns:
        frame["sample"] = "discovery"
    if "cost_bps" not in frame.columns:
        frame["cost_bps"] = pd.Series(pd.NA, index=frame.index, dtype="Float64")
    if "matched" not in frame.columns:
        frame["matched"] = False
    return frame[list(trades.COLUMNS)].astype(trades._DTYPES)  # pylint: disable=protected-access


def public_config(config: dict) -> dict:
    """A run's config without the local paths it was run from."""
    out = json.loads(json.dumps(config, default=str))
    out.get("run", {})["cache_dir"] = ""
    out.get("costs", {})["quote_table"] = ""
    return out


def _write(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(leaderboard.json_safe(payload), default=str, allow_nan=False,
                               separators=(",", ":")))


def _cell(value):
    if value is None or value is pd.NA:
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if hasattr(value, "item"):
        return value.item()
    return value


def _columnar(frame: pd.DataFrame, order: list[int] | None = None) -> dict:
    """The trade frame as one list per column, in stored order."""
    return {
        "columns": list(trades.COLUMNS),
        "data": {column: [_cell(v) for v in frame[column].astype(object)] for column in trades.COLUMNS},
        "order": list(range(len(frame))) if order is None else order,
    }


def _trade_files(out: Path, frame: pd.DataFrame, names: list[str]) -> None:
    """One trade file per pattern, each row tagged with its place in `frame`."""
    frame = frame.reset_index(drop=True)
    for name in sorted({*names, *frame["pattern"].unique()}):
        rows = frame[frame["pattern"] == name]
        _write(out / "trades" / f"{name}.json", _columnar(rows, [int(i) for i in rows.index]))


def _views(report: dict) -> list[str]:
    intervals = list(report["config"]["run"]["intervals"])
    return [*intervals, POOLED]


def _pattern_file(frame: pd.DataFrame, name: str, views: list[str]) -> dict:
    out = {}
    for view in views:
        interval = None if view == POOLED else view
        out[view] = {}
        for sample in SAMPLES:
            selected = trades.query(frame, sample=sample, matched=None)
            out[view][sample] = {
                "equity": trades.equity_curve(selected, name, interval),
                "matched_equity": trades.equity_curve(selected, name, interval, matched=True),
                "breakdowns": {
                    by: trades.breakdown(selected, by, pattern=name, interval=interval)
                    for by in trades.BREAKDOWNS
                },
            }
    return {"pattern": name, "views": out}


def _sessions(out: Path, report: dict, base_config: Config, cache_config: Config) -> int:
    """Bars and signal masks for every sampled session, one file per (symbol, day)."""
    from candlebench.web.server import session_view

    wanted: dict[str, set[str]] = {}
    for trial in report.get("trials", []):
        wanted.setdefault(trial["symbol"], set()).add(trial["session"])
    names = list(report["config"]["patterns"])
    written = 0
    loaded: dict[tuple[str, str], dict] = {}
    for symbol, days in sorted(wanted.items()):
        for day in sorted(days):
            intervals = {}
            for interval in report["config"]["run"]["intervals"]:
                if (symbol, interval) not in loaded:
                    try:
                        loaded[(symbol, interval)] = bars.sessions(
                            bars.load(symbol, interval, cache_config.cache_path))
                    except FileNotFoundError:
                        loaded[(symbol, interval)] = {}
                frame = loaded[(symbol, interval)].get(date.fromisoformat(day))
                if frame is None or len(frame) < 2:
                    continue
                signals, first = {}, None
                for name in names:
                    view = session_view(frame, symbol=symbol, session=date.fromisoformat(day),
                                        interval=interval, pattern=name, report=report,
                                        base_config=base_config, recorded=None)
                    signals[name] = view["signals"]
                    first = first or view
                intervals[interval] = {
                    "bars": first["bars"], "trend_lookback": first["trend_lookback"],
                    "measured": first["measured"], "signals": signals,
                }
            if intervals:
                _write(out / "sessions" / f"{symbol}_{day}.json", {"intervals": intervals})
                written += 1
    return written


def export(out: str | Path, report: dict, frame: pd.DataFrame | None, *,
           prices: bool, title: str | None = None, base_config: Config | None = None) -> Path:
    """Write the dashboard and everything it reads for one run into `out`."""
    from candlebench import config as config_module, report as html_report

    out = Path(out)
    if out.exists():
        shutil.rmtree(out)
    data = out / "data"
    data.mkdir(parents=True)
    base_config = base_config or config_module.load(None)
    run_config = report["config"]["run"]
    cache_config = replace(base_config, run=replace(
        base_config.run, source=run_config["source"], cache_dir=run_config["cache_dir"]))

    frame = frame if frame is not None else trades.to_frame([])
    if not prices:
        frame = strip_prices(frame)
    public = dict(report, config=public_config(report["config"]))

    intervals = list(run_config["intervals"])
    source = run_config["source"]
    _write(data / "meta.json", {
        "patterns": [p for p in _describe_patterns() if p["name"] in report["config"]["patterns"]],
        "intervals": intervals,
        "rank_keys": list(RANK_KEYS),
        "breakdowns": list(trades.BREAKDOWNS),
        "cost_models": [report["config"]["costs"]["model"]],
        "config": public["config"],
        "lookback_days": {iv: bars.lookback_days(source, iv, run_config.get("lookback_days", 0))
                          for iv in intervals},
        "intervals_by_source": {source: intervals},
        "sources": [source],
        "read_only": True,
        "static": True,
        "prices": prices,
        "controls": dict(CONTROLS),
        "matched": bool(frame["matched"].any()),
    })
    _write(data / "results.json", public)
    # Pattern trades only: the matched controls appear as curves, never as rows.
    _trade_files(data, frame[~frame["matched"]], list(report["config"]["patterns"]))
    views = _views(report)
    for name in report["config"]["patterns"]:
        _write(data / "patterns" / f"{name}.json", _pattern_file(frame, name, views))
    if prices:
        _sessions(data, report, base_config, cache_config)

    (out / "report.html").write_text(html_report.render(public, frame, title), encoding="utf-8")
    shutil.copy(STATIC_DIR / "app.js", out / "app.js")
    shutil.copy(STATIC_DIR / "app.css", out / "app.css")
    settings = json.dumps({"data": "data/", "report": "report.html", "prices": prices,
                           "title": title or "candlebench"})
    page = (STATIC_DIR / "index.html").read_text()
    page = page.replace('href="/static/app.css"', 'href="app.css"')
    page = page.replace('<script src="/static/app.js"></script>',
                        f"<script>window.CANDLEBENCH_STATIC = {settings};</script>\n"
                        '<script src="app.js"></script>')
    (out / "index.html").write_text(page, encoding="utf-8")
    return out


def _describe_patterns() -> list[dict]:
    from candlebench.web.server import describe_patterns

    return describe_patterns()
