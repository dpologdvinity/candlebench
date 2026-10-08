"""A run as one self-contained HTML file.

The dashboard needs a running server and a bar cache; a result worth sharing
needs neither. This renders a saved report, and optionally its trades, into a
single page with inline styles and SVG and no scripts, so it can be attached,
hosted on any static site, or opened years later without the code.

It deliberately contains no prices. Trades appear only as R multiples, dates,
symbols and times of day, so publishing a report built from licensed market
data republishes statistics about the data, not the data.

Every figure comes from the saved report or the trade file, with the same
rounding and the same "n/a for unavailable" rule as the CLI and the dashboard.
"""

from __future__ import annotations

import html
import math
from pathlib import Path

import pandas as pd

from candlebench import trades as trade_store

POOLED = "all"
VERDICTS = ("EDGE", "NOISE", "NEGATIVE", "INSUFFICIENT")
MAX_CURVE_POINTS = 400

CSS = """
:root{--bg:#11131a;--panel:#181b24;--line:#2a2f3c;--text:#e4e7ee;--muted:#8b93a7;
--accent:#6aa9ff;--edge:#46d18a;--negative:#e2685f;--insufficient:#5a6176;
--mono:ui-monospace,"SF Mono","Cascadia Mono",Menlo,Consolas,monospace}
*{box-sizing:border-box}
body{margin:0 auto;padding:2rem 1.5rem 4rem;max-width:1180px;background:var(--bg);color:var(--text);
font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
h1{font-size:1.5rem;margin:0}
h2{font-size:.95rem;margin:0 0 .8rem;text-transform:uppercase;letter-spacing:.08em;color:var(--muted)}
h3{font-size:1rem;margin:1.2rem 0 .5rem}
a{color:var(--accent)}
.sub,.muted,.hint{color:var(--muted)}
.hint{font-size:.85rem}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:1.1rem 1.25rem;margin-bottom:1.1rem}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:.6rem}
.stat{border:1px solid var(--line);border-radius:8px;padding:.6rem .75rem}
.stat b{display:block;font:600 1.2rem var(--mono)}
.stat span{color:var(--muted);font-size:.72rem;text-transform:uppercase;letter-spacing:.05em}
.scroll{overflow-x:auto}
table{border-collapse:collapse;width:100%;font:.8rem var(--mono)}
th,td{padding:.35rem .5rem;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}
th{color:var(--muted);font-weight:500;font-size:.7rem;text-transform:uppercase}
th:nth-child(2),td:nth-child(2){text-align:left}
.pos{color:var(--edge)}.neg{color:var(--negative)}.na{color:var(--insufficient)}
.chip{display:inline-block;border-radius:999px;padding:0 .5rem;font-size:.7rem;border:1px solid var(--line)}
.EDGE{color:var(--edge);border-color:var(--edge)}.NEGATIVE{color:var(--negative)}
.NOISE{color:var(--muted)}.INSUFFICIENT{color:var(--insufficient)}
.control td:nth-child(2){color:var(--muted)}
.note{border-left:3px solid var(--negative);padding:.4rem .7rem;margin:.6rem 0;font-size:.85rem;background:#2a1d22}
details{border-top:1px solid var(--line);padding:.5rem 0}
summary{cursor:pointer;font:.85rem var(--mono)}
svg{display:block;width:100%;height:auto;font:10px var(--mono);fill:var(--muted)}
.two{display:grid;grid-template-columns:1fr 1fr;gap:1rem}
@media (max-width:760px){.two{grid-template-columns:1fr}}
footer{color:var(--muted);font-size:.8rem;margin-top:2rem}
"""


def _e(value) -> str:
    return html.escape(str(value), quote=True)


def _known(value) -> bool:
    return value is not None and not (isinstance(value, float) and not math.isfinite(value))


def _num(value, places: int = 2, signed: bool = False) -> str:
    if not _known(value):
        return '<span class="na">n/a</span>'
    text = f"{value:+.{places}f}" if signed else f"{value:.{places}f}"
    cls = "pos" if signed and value > 0 else "neg" if signed and value < 0 else ""
    return f'<span class="{cls}">{text}</span>' if cls else text


def _pct(value) -> str:
    return f"{value * 100:.1f}" if _known(value) else '<span class="na">n/a</span>'


def _interval(low, high) -> str:
    if not (_known(low) and _known(high)):
        return '<span class="na">n/a</span>'
    return f"[{low:+.2f}, {high:+.2f}]"


def _detectable(value) -> str:
    return f"&ge;{value:.2f}" if _known(value) else '<span class="na">n/a</span>'


def _rank_key(row: dict):
    value = row.get("ci_low")
    return (0, -value) if _known(value) else (1, 0)


def cost_headline(report: dict) -> tuple[str, str]:
    """The headline cost and its label; the same rule as the dashboard's."""
    costs = (report.get("config") or {}).get("costs") or {}
    model = costs.get("model")
    if model == "quoted" and _known(report.get("charged_bps")):
        label = ("bps paid per leg (quoted)" if (report.get("quoted_share") or 0) > 0
                 else "bps paid per leg (estimated; no quotes)")
        return f"{report['charged_bps']:.2f}", label
    if model == "fixed" or not _known(report.get("spread_bps")):
        return f"{costs.get('slippage_bps', 'n/a')}", "bps cost per leg (fixed)"
    return f"{report['spread_bps'] / 2:.2f}", f"bps cost per leg (estimated, {report.get('spread_interval')})"


def _beats_control(row: dict) -> bool:
    return (row.get("kind") != "control" and _known(row.get("p_delta_adjusted"))
            and row["p_delta_adjusted"] <= 0.05 and _known(row.get("baseline_ci_low"))
            and row["baseline_ci_low"] > 0)


def _summary(report: dict) -> str:
    config = report.get("config") or {}
    rows = [r for r in report.get("stats", []) if r.get("kind") != "control"]
    edges = [r for r in rows if r.get("verdict") == "EDGE"]
    beating = [r for r in rows if _beats_control(r)]
    cost, cost_label = cost_headline(report)
    cells = [
        (len(report.get("trials") or []), "trials"),
        (report.get("sessions_evaluated", "n/a"), "sessions"),
        ((config.get("universe") or {}).get("sample_size", "n/a"), "symbols"),
        (len(edges), "with an edge"),
        (len(beating), "beating control"),
        (cost, cost_label),
        ((config.get("run") or {}).get("seed", "n/a"), "seed"),
    ]
    grid = "".join(f'<div class="stat"><b>{_e(v)}</b><span>{_e(k)}</span></div>' for v, k in cells)
    if edges:
        verdict = ("<b>" + ", ".join(_e(f"{r['pattern']} ({r['interval']})") for r in edges)
                   + "</b> passed corrected discovery tests and independent later validation.")
    else:
        verdict = (f"No pattern established a confirmed edge. {len(beating)} discovery row(s) "
                   "show a corrected advantage over their controls; that alone does not "
                   "establish profitable trading.")
    warnings = "".join(f'<p class="hint">&#9888; {_e(w)}</p>' for w in report.get("warnings") or [])
    return (f'<section class="panel"><h2>Result</h2><div class="grid">{grid}</div>'
            f"<p>{verdict}</p>{_validation(report)}{warnings}</section>")


def _validation(report: dict) -> str:
    info = report.get("validation") or {}
    if not info:
        return '<p class="hint">Historical report: validation evidence unavailable.</p>'
    if not info.get("enabled"):
        return '<p class="hint">Validation disabled: exploratory results only.</p>'
    evaluated = ("Validation evaluated." if info.get("evaluated")
                 else "Holdout untouched: no candidate passed discovery.")
    return (f'<p class="hint">Holdout starts {_e(info.get("cutoff"))}: '
            f'{_e(info.get("discovery_trials"))} discovery / {_e(info.get("validation_trials"))} '
            f'validation trials; {len(info.get("candidates") or [])} selected candidate(s). '
            f"{evaluated}</p>")


def _table(rows: list[dict]) -> str:
    head = ("<tr><th>#</th><th>pattern</th><th>trades</th><th>win%</th><th>exp R</th>"
            "<th>95% CI</th><th>vs ctrl</th><th>paired CI</th><th>adj p</th>"
            '<th title="smallest advantage over matched controls this row would detect 80% of the time">detectable</th>'
            "<th>dates</th><th>verdict</th></tr>")
    body = []
    for rank, row in enumerate(sorted(rows, key=_rank_key), 1):
        control = row.get("kind") == "control"
        verdict = row.get("verdict") or "n/a"
        body.append(
            f'<tr class="{"control" if control else ""}"><td>{rank}</td>'
            f'<td>{_e(row["pattern"])}{" *" if control else ""}</td><td>{_e(row.get("trades", 0))}</td>'
            f'<td>{_pct(row.get("win_rate"))}</td><td>{_num(row.get("expectancy_r"), signed=True)}</td>'
            f'<td>{_interval(row.get("ci_low"), row.get("ci_high"))}</td>'
            f'<td>{_num(row.get("baseline_delta_r"), signed=True)}</td>'
            f'<td>{_interval(row.get("baseline_ci_low"), row.get("baseline_ci_high"))}</td>'
            f'<td>{_num(row.get("p_delta_adjusted"), 4)}</td>'
            f'<td>{_detectable(row.get("mde_delta_r"))}</td><td>{_e(row.get("sessions", "n/a"))}</td>'
            f'<td><span class="chip {_e(verdict)}">{_e(verdict)}</span></td></tr>'
        )
    return f'<div class="scroll"><table>{head}{"".join(body)}</table></div>'


def _forest(rows: list[dict]) -> str:
    """Expectancy with its interval, one line per row."""
    shown = [r for r in sorted(rows, key=_rank_key) if _known(r.get("ci_low"))]
    if not shown:
        return '<p class="hint">No row has an interval at this timeframe.</p>'
    width, label, row_h = 560, 150, 16
    height = 20 + row_h * len(shown)
    lo = min(min(r["ci_low"] for r in shown), 0.0)
    hi = max(max(r["ci_high"] for r in shown), 0.0)
    span = (hi - lo) or 1.0

    def x(v):
        return label + (v - lo) / span * (width - label - 10)

    parts = [f'<line x1="{x(0):.1f}" y1="4" x2="{x(0):.1f}" y2="{height - 14}" stroke="#2a2f3c"/>']
    for i, r in enumerate(shown):
        y = 12 + i * row_h
        colour = "var(--edge)" if r["ci_low"] > 0 else "var(--negative)" if r["ci_high"] < 0 else "var(--muted)"
        parts.append(
            f'<text x="{label - 6}" y="{y + 3}" text-anchor="end">{_e(r["pattern"])}</text>'
            f'<line x1="{x(r["ci_low"]):.1f}" y1="{y}" x2="{x(r["ci_high"]):.1f}" y2="{y}" stroke="{colour}"/>'
            f'<circle cx="{x(r["expectancy_r"]):.1f}" cy="{y}" r="2.5" fill="{colour}"/>'
        )
    parts.append(f'<text x="{x(lo):.1f}" y="{height - 2}">{lo:+.2f}R</text>'
                 f'<text x="{x(hi):.1f}" y="{height - 2}" text-anchor="end">{hi:+.2f}R</text>')
    return f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="expectancy intervals">{"".join(parts)}</svg>'


def _deltas(rows: list[dict]) -> str:
    """Each pattern's advantage over its controls, as bars."""
    shown = sorted((r for r in rows if r.get("kind") != "control" and _known(r.get("baseline_delta_r"))),
                   key=lambda r: -r["baseline_delta_r"])
    if not shown:
        return '<p class="hint">No control comparison is available at this timeframe.</p>'
    width, label, row_h, margin = 560, 150, 16, 44
    height = 12 + row_h * len(shown)
    extent = max(abs(r["baseline_delta_r"]) for r in shown) or 1.0
    # A margin on both sides of the bars leaves room for the value labels, so
    # the longest negative bar's label cannot run into its pattern name.
    half = (width - label - 2 * margin) / 2
    mid = label + margin + half
    scale = half / extent
    parts = [f'<line x1="{mid:.1f}" y1="2" x2="{mid:.1f}" y2="{height}" stroke="#2a2f3c"/>']
    for i, r in enumerate(shown):
        y = 6 + i * row_h
        v = r["baseline_delta_r"]
        colour = "var(--edge)" if v > 0 else "var(--negative)"
        left = mid if v >= 0 else mid + v * scale
        parts.append(
            f'<text x="{label - 6}" y="{y + 9}" text-anchor="end">{_e(r["pattern"])}</text>'
            f'<rect x="{left:.1f}" y="{y}" width="{max(1.0, abs(v) * scale):.1f}" height="11" fill="{colour}" opacity="0.8"/>'
            f'<text x="{mid + v * scale + (4 if v >= 0 else -4):.1f}" y="{y + 9}" '
            f'text-anchor="{"start" if v >= 0 else "end"}">{v:+.2f}</text>'
        )
    return f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="advantage over controls">{"".join(parts)}</svg>'


def _intervals(report: dict) -> str:
    stats = report.get("stats") or []
    order = list((report.get("config") or {}).get("run", {}).get("intervals") or [])
    present = [iv for iv in order if any(r["interval"] == iv for r in stats)]
    if any(r["interval"] == POOLED for r in stats):
        present.append(POOLED)
    losing = report.get("losing_controls") or {}
    sections = []
    for n, interval in enumerate(present):
        rows = [r for r in stats if r["interval"] == interval]
        note = ""
        if losing.get(interval):
            names = ", ".join(losing[interval])
            note = (f'<p class="note"><b>{_e(names)}</b> reliably '
                    f'{"lose" if len(losing[interval]) > 1 else "loses"} at this timeframe after '
                    "costs, so NEGATIVE verdicts partly reflect cost drag rather than the pattern.</p>")
        title = "all timeframes pooled" if interval == POOLED else interval
        sections.append(
            f'<details{" open" if n == 0 else ""}><summary>{_e(title)}</summary>{note}{_table(rows)}'
            f'<div class="two"><div><h3>Expectancy with 95% interval</h3>{_forest(rows)}</div>'
            f'<div><h3>Advantage over matched controls (gross R)</h3>{_deltas(rows)}</div></div></details>'
        )
    legend = ('<p class="hint">* random-entry reference row. EDGE: corrected positive net '
              "expectancy and advantage over controls, confirmed on held-out dates. NOISE: no "
              "confirmed edge. NEGATIVE: reliably loses. INSUFFICIENT: too few trades or dates. "
              "Detectable: the smallest true advantage over matched controls, in gross R, that "
              "this row's corrected test would catch 80% of the time; a NOISE row can still hide "
              "an edge smaller than that.</p>")
    return f'<section class="panel"><h2>Leaderboard</h2>{"".join(sections)}{legend}</section>'


def _curve(points: list[float]) -> str:
    if not points:
        return '<p class="hint">No trades.</p>'
    step = max(1, math.ceil(len(points) / MAX_CURVE_POINTS))
    series = [0.0] + points[step - 1::step]
    if series[-1] != points[-1]:
        series.append(points[-1])
    # Drawn at the width it is displayed at, so its labels stay text-sized.
    width, height, pad = 1080, 180, 52
    lo, hi = min(min(series), 0.0), max(max(series), 0.0)
    span = (hi - lo) or 1.0

    def xy(i, v):
        return (pad + i / max(1, len(series) - 1) * (width - pad - 6),
                6 + (1 - (v - lo) / span) * (height - 22))

    path = "".join(f'{"L" if i else "M"}{px:.1f},{py:.1f}' for i, (px, py) in
                   enumerate(xy(i, v) for i, v in enumerate(series)))
    zero = xy(0, 0.0)[1]
    return (f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="cumulative R">'
            f'<line x1="{pad}" y1="{zero:.1f}" x2="{width - 6}" y2="{zero:.1f}" stroke="#2a2f3c" stroke-dasharray="2 3"/>'
            f'<path d="{path}" fill="none" stroke="var(--accent)" stroke-width="1.4"/>'
            f'<text x="{pad - 6}" y="12" text-anchor="end">{hi:+.1f}R</text>'
            f'<text x="{pad - 6}" y="{height - 16}" text-anchor="end">{lo:+.1f}R</text>'
            f'<text x="{pad}" y="{height - 2}">trade 1</text>'
            f'<text x="{width - 6}" y="{height - 2}" text-anchor="end">trade {len(points)}</text></svg>')


def _breakdown_table(rows: list[dict], label: str, limit: int | None = None) -> str:
    if not rows:
        return ""
    shown = sorted(rows, key=lambda r: -r["trades"])[:limit] if limit else rows
    body = "".join(
        f'<tr><td></td><td>{_e(r["key"])}</td><td>{r["trades"]}</td><td>{_pct(r["win_rate"])}</td>'
        f'<td>{_num(r["expectancy_r"], signed=True)}</td><td>{_num(r["total_r"], 1, signed=True)}</td></tr>'
        for r in shown
    )
    more = f'<p class="hint">{len(rows) - len(shown)} more not shown.</p>' if len(shown) < len(rows) else ""
    return (f'<h3>By {_e(label)}</h3><div class="scroll"><table><tr><th></th><th>{_e(label)}</th>'
            f"<th>trades</th><th>win%</th><th>exp R</th><th>total R</th></tr>{body}</table></div>{more}")


def _drilldowns(report: dict, frame: pd.DataFrame | None) -> str:
    if frame is None or frame.empty:
        return ""
    discovery = frame[frame["sample"] == "discovery"]
    names = [r["pattern"] for r in sorted(
        (r for r in report.get("stats", []) if r["interval"] == POOLED), key=_rank_key)]
    parts = []
    for name in names:
        curve = trade_store.equity_curve(discovery, name)
        if not curve["trades"]:
            continue
        parts.append(
            f"<details><summary>{_e(name)} — {curve['trades']} discovery trades, deepest drawdown "
            f"{_num(curve['max_drawdown_r'])}R</summary>"
            f"<h3>Cumulative net R in the order traded</h3>{_curve(curve['points'])}"
            f'<div class="two"><div>{_breakdown_table(trade_store.breakdown(discovery, "time_of_day", pattern=name), "time of day")}'
            f'{_breakdown_table(trade_store.breakdown(discovery, "exit_reason", pattern=name), "exit")}</div>'
            f'<div>{_breakdown_table(trade_store.breakdown(discovery, "symbol", pattern=name), "symbol", limit=12)}</div></div>'
            "</details>"
        )
    return ('<section class="panel"><h2>Drill-down, all timeframes pooled</h2>'
            '<p class="hint">One ticker carrying the result, or the opening auction, are the two '
            "usual explanations for an apparent intraday edge.</p>" + "".join(parts) + "</section>")


def _method(report: dict) -> str:
    config = report.get("config") or {}
    trade = config.get("trade") or {}
    stats = config.get("stats") or {}
    trials = sorted(t["session"] for t in report.get("trials") or [])
    span = f"{trials[0]} to {trials[-1]}" if trials else "n/a"
    p = report.get("provenance")
    if p:
        git = p.get("git")
        code = (f'{_e(git["commit"][:10])}{" (uncommitted changes)" if git.get("dirty") else ""}'
                if git else f'version {_e(p.get("candlebench"))}')
        data = p.get("data") or {}
        provenance = (f"Computed {_e(p.get('created_at'))} by code {code}; config "
                      f"{_e((p.get('config_sha256') or '')[:10])}; {_e(data.get('source'))} data "
                      f"{_e((data.get('sha256') or '')[:10])} over {_e(data.get('files'))} cached file(s).")
    else:
        provenance = "Historical report: code and data provenance were not recorded."
    return (
        '<section class="panel"><h2>Method</h2><p class="hint">'
        f"Each signal enters on the next bar's open with a stop at the pattern extreme padded by "
        f"{(trade.get('stop_buffer') or 0) * 100:.3f}%, a target at {_e(trade.get('reward_multiple'))}R and "
        f"a {_e(trade.get('max_hold_bars'))}-bar holding cap, net of {_e(report.get('costs_description', 'n/a'))}. "
        "A bar touching both stop and target counts as a stop unless it opened past the target. "
        "Each pattern trade is compared, in gross R, with a random-entry control in the same session "
        "with the same direction and stop distance, entering one to five bars later. "
        "Intervals resample whole market dates; verdicts use Holm correction across all rows and "
        f"{_e(stats.get('experiment_count', 1))} declared experiment(s), and a confirmed EDGE must "
        f"also pass on held-out later dates. Sampled sessions span {_e(span)}.</p>"
        f'<p class="hint">{provenance}</p></section>'
    )


def render(report: dict, frame: pd.DataFrame | None = None, title: str | None = None) -> str:
    """The whole report as one HTML document."""
    title = title or "candlebench report"
    cost_line = report.get("costs_description") or "costs unavailable"
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        f"<title>{_e(title)}</title><style>{CSS}</style></head><body>"
        f"<header><h1>{_e(title)}</h1><p class=\"sub\">Which candlestick patterns have a real "
        f"intraday edge, net of {_e(cost_line)}.</p></header>"
        f"{_summary(report)}{_intervals(report)}{_drilldowns(report, frame)}{_method(report)}"
        "<footer>Generated by candlebench. "
        "<a href=\"https://github.com/dpologdvinity/stock-analyzer\">Source and method</a>.</footer>"
        "</body></html>"
    )


def write(report: dict, path: str | Path, frame: pd.DataFrame | None = None,
          title: str | None = None) -> Path:
    """Render to `path` via a temporary file, like every other output here."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f"{path.name}.tmp")
    try:
        temp.write_text(render(report, frame, title), encoding="utf-8")
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)
    return path
