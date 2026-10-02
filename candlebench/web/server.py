"""A local HTTP server for browsing and triggering runs.

Standard library only, to keep the project's dependency list as it is.

The server binds to the loopback interface. It triggers real runs and real
network fetches from unauthenticated requests, which is acceptable only
because nothing outside this machine can reach it. Binding to 0.0.0.0 would
hand any host on the network the ability to start work and read results, so
the host is not configurable.
"""

from __future__ import annotations

import json
import mimetypes
import webbrowser
from dataclasses import replace
from datetime import date
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import numpy as np

from candlebench import bars, costs, leaderboard, patterns, runner, trades, universe
from candlebench.patterns import context
from candlebench.config import Config, Thresholds, TradeConfig, CostConfig, RunConfig
from candlebench.config import StatsConfig, UniverseConfig, RANK_KEYS, SYMBOL_PATTERN, validate
from candlebench.patterns.control import CONTROLS
from candlebench.web.history import RUN_ID, History
from candlebench.web.jobs import JobResult, JobRunner, JobState

HOST = "127.0.0.1"
STATIC_DIR = Path(__file__).parent / "static"

# The largest page of trades one request may ask for. A full run holds about
# 43,000 trades; serving them in one response would be several MiB and would
# make the browser, not the server, decide how much work to do.
MAX_PAGE = 500
DEFAULT_PAGE = 200

# Only these sections may be set from the browser. cache_dir is deliberately
# absent: a request that could choose where bars are written or read would let
# the page reach any path on the machine.
_EDITABLE = {
    "run": {"trials", "seed", "windows", "intervals", "throttle_s"},
    "universe": {"symbols", "sample_size"},
    "trade": set(TradeConfig.__dataclass_fields__),
    "costs": set(CostConfig.__dataclass_fields__),
    "thresholds": set(Thresholds.__dataclass_fields__),
    "stats": set(StatsConfig.__dataclass_fields__),
}

_SECTION_TYPES = {
    "run": RunConfig,
    "universe": UniverseConfig,
    "trade": TradeConfig,
    "costs": CostConfig,
    "thresholds": Thresholds,
    "stats": StatsConfig,
}


def config_from_request(base: Config, body: dict) -> Config:
    """Build a validated config from a browser payload.

    Unknown or non-editable keys are rejected rather than ignored, matching the
    file loader: a silently dropped field would mean the run measured something
    other than what the page displayed.
    """
    updates = {}
    for section, allowed in _EDITABLE.items():
        raw = body.get(section)
        if not raw:
            continue
        if not isinstance(raw, dict):
            raise ValueError(f"{section} must be an object")
        rejected = set(raw) - allowed
        if rejected:
            raise ValueError(
                f"{section}: cannot set {', '.join(sorted(rejected))} from the browser"
            )
        current = getattr(base, section)
        coerced = {
            k: (tuple(v) if isinstance(v, list) else v) for k, v in raw.items()
        }
        updates[section] = replace(current, **coerced)

    chosen = body.get("patterns")
    if chosen is not None:
        if not isinstance(chosen, list) or not all(isinstance(p, str) for p in chosen):
            raise ValueError("patterns must be a list of names")
        updates["patterns"] = tuple(chosen)

    return validate(replace(base, **updates))


def describe_cache(config: Config) -> dict:
    """What the cache holds, so the page can say whether a fetch is needed."""
    symbols = universe.resolve(config.universe.symbols, config.universe.sample_size)
    out = {}
    for interval in bars.SUPPORTED_INTERVALS:
        present = bars.available_sessions(symbols, interval, config.cache_path)
        out[interval] = {
            "symbols": len(present),
            "sessions": sum(len(v) for v in present.values()),
        }
    return {"requested_symbols": len(symbols), "intervals": out}


def query_filters(params: dict[str, list[str]]) -> dict:
    """Validate the filter and paging parameters of a trade query.

    Every value is checked against the same authority the config loader uses
    before it reaches a frame or a path: the pattern registry, the supported
    interval list, and `SYMBOL_PATTERN`. A rejected value raises `ValueError`,
    which the handler turns into a 400 — the alternative, filtering a frame by a
    string nobody vetted, is how a traversal attempt becomes a 500.
    """

    def single(name: str) -> str | None:
        values = params.get(name)
        return values[0] if values and values[0] != "" else None

    pattern = single("pattern")
    if pattern is not None and pattern not in patterns.registry():
        raise ValueError(f"unknown pattern {pattern!r}")

    interval = single("interval")
    if interval is not None and interval not in bars.SUPPORTED_INTERVALS:
        raise ValueError(
            f"unsupported interval {interval!r}. "
            f"supported: {', '.join(bars.SUPPORTED_INTERVALS)}"
        )

    symbol = single("symbol")
    if symbol is not None and not SYMBOL_PATTERN.match(symbol):
        raise ValueError(f"invalid symbol {symbol!r}")

    run = single("run")
    if run is not None and not RUN_ID.match(run):
        raise ValueError(
            f"invalid run id {run!r}; a run id is a timestamp such as 20260915T143000"
        )

    return {
        "pattern": pattern,
        "interval": interval,
        "symbol": symbol,
        "run": run,
        **query_page(params),
    }


def query_page(params: dict[str, list[str]]) -> dict:
    """The `limit` and `offset` of a paged query, capped server-side."""

    def integer(name: str, default: int) -> int:
        values = params.get(name)
        if not values or values[0] == "":
            return default
        try:
            value = int(values[0])
        except ValueError:
            raise ValueError(f"{name} must be a whole number, not {values[0]!r}") from None
        if value < 0:
            raise ValueError(f"{name} must not be negative")
        return value

    sort = (params.get("sort") or [""])[0] or None
    if sort is not None and sort not in trades.COLUMNS:
        raise ValueError(f"cannot sort by {sort!r}. valid: {', '.join(trades.COLUMNS)}")

    return {
        "limit": min(integer("limit", DEFAULT_PAGE), MAX_PAGE),
        "offset": integer("offset", 0),
        "sort": sort,
        "desc": (params.get("desc") or ["0"])[0] not in ("0", "false", ""),
    }


def _no_trades(filters: dict) -> str:
    run = filters.get("run")
    return (
        f"no saved trades for run {run}" if run
        else "no trades recorded yet; run the backtest first"
    )


def describe_patterns() -> list[dict]:
    return [
        {
            "name": spec.name,
            "bias": spec.bias,
            "bars_required": spec.bars_required,
            "requires_trend": spec.requires_trend,
            "kind": spec.kind,
        }
        for spec in sorted(
            patterns.registry().values(), key=lambda s: (s.kind, s.bars_required, s.name)
        )
    ]


class Handler(BaseHTTPRequestHandler):
    server_version = "candlebench"

    def __init__(self, *args, base_config: Config, jobs: JobRunner, **kwargs):
        self.base_config = base_config
        self.jobs = jobs
        super().__init__(*args, **kwargs)

    # Quieter than the default, which prints a line per asset request.
    def log_message(self, fmt, *args):
        if self.path.startswith("/api/") and self.command != "GET":
            super().log_message(fmt, *args)

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload) -> None:
        self._send(status, json.dumps(payload, default=str).encode(), "application/json")

    def _static(self, name: str) -> None:
        # resolve() then a prefix check, so "../" in a request cannot read
        # outside the static directory.
        target = (STATIC_DIR / name).resolve()
        if not str(target).startswith(str(STATIC_DIR.resolve())) or not target.is_file():
            self._json(404, {"error": "not found"})
            return
        kind = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self._send(200, target.read_bytes(), kind)

    def _frame_for(self, filters: dict):
        """The trade frame a query refers to: a saved run's, or the latest.

        `run` has already been checked against `RUN_ID`, so it is safe to use as
        a filename here and nowhere else.
        """
        run = filters.get("run")
        if run is None:
            return self.jobs.trades_frame()
        history = self.jobs.history
        return None if history is None else history.trades_frame(run)

    def _runs(self, params: dict[str, list[str]]) -> None:
        history = self.jobs.history
        if history is None:
            self._json(404, {"error": "this server keeps no run history"})
            return

        run = (params.get("id") or [""])[0] or None
        if run is not None and not RUN_ID.match(run):
            self._json(400, {
                "error": f"invalid run id {run!r}; "
                         "a run id is a timestamp such as 20260915T143000"
            })
            return

        if run is None:
            self._json(200, {"keep": history.keep, "runs": history.summaries()})
            return

        payload = history.payload(run)
        if payload is None:
            self._json(404, {"error": f"no saved run {run}"})
            return
        self._json(200, payload)

    def _trades(self, params: dict[str, list[str]]) -> None:
        try:
            filters = query_filters(params)
        except ValueError as exc:
            self._json(400, {"error": str(exc)})
            return

        frame = self._frame_for(filters)
        if frame is None:
            self._json(404, {"error": _no_trades(filters)})
            return

        filters = {k: v for k, v in filters.items() if k != "run"}
        page = trades.query(frame, **filters)
        total = len(
            trades.query(
                frame,
                pattern=filters["pattern"],
                interval=filters["interval"],
                symbol=filters["symbol"],
            )
        )
        self._json(200, {
            "total": total,
            "limit": filters["limit"],
            "offset": filters["offset"],
            "sort": filters["sort"],
            "desc": filters["desc"],
            "columns": list(trades.COLUMNS),
            # `pd.NA` would serialise as the string "<NA>" through the JSON
            # encoder's `default=str`, putting a string where the page expects a
            # number. Missing stays null.
            "trades": page.astype(object).where(page.notna(), None).to_dict(
                orient="records"
            ),
        })

    def _breakdown(self, params: dict[str, list[str]]) -> None:
        by = (params.get("by") or [""])[0]
        try:
            filters = query_filters(params)
            if not by:
                raise ValueError(f"by is required. valid: {', '.join(trades.BREAKDOWNS)}")
            if by not in trades.BREAKDOWNS:
                raise ValueError(
                    f"cannot break down by {by!r}. valid: {', '.join(trades.BREAKDOWNS)}"
                )
        except ValueError as exc:
            self._json(400, {"error": str(exc)})
            return

        frame = self._frame_for(filters)
        if frame is None:
            self._json(404, {"error": _no_trades(filters)})
            return

        self._json(200, {
            "by": by,
            "run": filters["run"],
            "pattern": filters["pattern"],
            "interval": filters["interval"],
            "rows": trades.breakdown(
                frame, by, pattern=filters["pattern"], interval=filters["interval"]
            ),
        })

    def _equity(self, params: dict[str, list[str]]) -> None:
        try:
            filters = query_filters(params)
            if not filters["pattern"]:
                raise ValueError("pattern is required")
        except ValueError as exc:
            self._json(400, {"error": str(exc)})
            return

        frame = self._frame_for(filters)
        if frame is None:
            self._json(404, {"error": _no_trades(filters)})
            return

        name = filters["pattern"]
        interval = filters["interval"]
        # The control is looked up from the pattern's own bias rather than chosen
        # by the caller, so the overlay cannot be the wrong noise floor.
        control_name = CONTROLS.get(patterns.registry()[name].bias)
        self._json(200, {
            "pattern": trades.equity_curve(frame, name, interval),
            "control": (
                trades.equity_curve(frame, control_name, interval)
                if control_name
                else None
            ),
        })

    def _session(self, params: dict[str, list[str]]) -> None:
        """One session's bars, the pattern's signal mask, and its recorded trades.

        The signal mask is recomputed from the cached bars, but the entry, stop
        and target marks come from the trades the last run stored. Re-deriving
        those here would let the chart and the leaderboard disagree about what was
        traded; reading them back cannot.
        """
        try:
            filters = query_filters(params)
            for name in ("symbol", "pattern"):
                if not filters[name]:
                    raise ValueError(f"{name} is required")
            interval = filters["interval"]
            if not interval:
                raise ValueError("interval is required")
            raw = (params.get("session") or [""])[0]
            try:
                session = date.fromisoformat(raw)
            except ValueError:
                raise ValueError(
                    f"session must be an ISO date such as 2026-09-15, not {raw!r}"
                ) from None
        except ValueError as exc:
            self._json(400, {"error": str(exc)})
            return

        symbol, name = filters["symbol"], filters["pattern"]
        try:
            available = bars.sessions(bars.load(symbol, interval, self.base_config.cache_path))
        except FileNotFoundError as exc:
            self._json(404, {"error": str(exc)})
            return

        frame = available.get(session)
        if frame is None or len(frame) < 2:
            self._json(404, {
                "error": f"no cached {interval} bars for {symbol} on {session}"
            })
            return

        spec = patterns.registry()[name]
        thresholds = self.base_config.thresholds
        lookback = runner.trend_lookback_for_length(
            thresholds.trend_lookback,
            len(frame),
            max(s.bars_required for s in patterns.registry().values()),
        )
        geom = context.geometry(bars.to_arrays(frame), lookback, thresholds.trend_min_slope)
        # A control's mask is random and supplied by the runner, so there is
        # nothing to re-detect; its trades are still worth marking.
        mask = (
            patterns.detect(spec, geom, thresholds)
            if spec.kind == "pattern"
            else np.zeros(len(geom), dtype=bool)
        )

        recorded = self.jobs.trades_frame()
        marks = []
        if recorded is not None:
            on_session = trades.query(recorded, pattern=name, interval=interval, symbol=symbol)
            on_session = on_session[on_session["session"] == session.isoformat()]
            marks = on_session[[
                "entry_index", "exit_index", "entry_price", "exit_price",
                "stop_price", "target_price", "direction", "net_r", "exit_reason",
            ]].to_dict(orient="records")

        local = frame.index
        self._json(200, {
            "symbol": symbol,
            "session": session.isoformat(),
            "interval": interval,
            "pattern": name,
            "kind": spec.kind,
            "trend_lookback": lookback,
            "signals": [int(i) for i in np.flatnonzero(mask)],
            "trades": marks,
            "bars": [
                {
                    "t": stamp.strftime("%H:%M"),
                    "o": float(row.open), "h": float(row.high),
                    "l": float(row.low), "c": float(row.close), "v": float(row.volume),
                }
                for stamp, row in zip(local, frame.itertuples())
            ],
        })

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        route = parsed.path
        params = parse_qs(parsed.query)

        if route == "/api/trades":
            self._trades(params)
            return
        if route == "/api/breakdown":
            self._breakdown(params)
            return
        if route == "/api/equity":
            self._equity(params)
            return
        if route == "/api/session":
            self._session(params)
            return
        if route == "/api/runs":
            self._runs(params)
            return

        if route in ("/", "/index.html"):
            self._static("index.html")
        elif route.startswith("/static/"):
            self._static(route[len("/static/"):])
        elif route == "/api/meta":
            self._json(200, {
                "patterns": describe_patterns(),
                "intervals": list(bars.SUPPORTED_INTERVALS),
                "rank_keys": list(RANK_KEYS),
                "breakdowns": list(trades.BREAKDOWNS),
                "cost_models": list(costs.MODELS),
                "config": leaderboard.payload_config(self.base_config),
                "lookback_days": bars.INTERVAL_MAX_LOOKBACK_DAYS,
            })
        elif route == "/api/results":
            self._json(200, self.jobs.results or {})
        elif route == "/api/status":
            self._json(200, self.jobs.state)
        elif route == "/api/cache":
            self._json(200, describe_cache(self.base_config))
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        route = urlparse(self.path).path
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError) as exc:
            self._json(400, {"error": f"malformed request body: {exc}"})
            return

        if route == "/api/run":
            self._start_run(body)
        elif route == "/api/fetch":
            self._start_fetch(body)
        else:
            self._json(404, {"error": "not found"})

    def _start_run(self, body: dict) -> None:
        try:
            config = config_from_request(self.base_config, body)
        except ValueError as exc:
            self._json(400, {"error": str(exc)})
            return

        def work(state: JobState) -> JobResult:
            def progress(interval, done, total):
                state.done, state.total = done, total
                state.message = f"{interval}: trial {done} of {total}"

            result = runner.run(config, progress=progress)
            return JobResult(leaderboard.payload(result, config), result.trades)

        if not self.jobs.submit("run", work):
            self._json(409, {"error": "a job is already running"})
            return
        self._json(202, {"started": True})

    def _start_fetch(self, body: dict) -> None:
        try:
            config = config_from_request(self.base_config, body)
        except ValueError as exc:
            self._json(400, {"error": str(exc)})
            return

        symbols = universe.resolve(config.universe.symbols, config.universe.sample_size)

        def work(state: JobState) -> None:
            state.total = len(config.run.intervals)
            state.message = f"fetching {len(symbols)} symbols"
            report = bars.warm_cache(
                symbols, config.run.intervals, config.cache_path, config.run.throttle_s
            )
            state.done = state.total
            state.message = report.summary()
            return None

        if not self.jobs.submit("fetch", work):
            self._json(409, {"error": "a job is already running"})
            return
        self._json(202, {"started": True})


def serve(config: Config, port: int = 8765, open_browser: bool = True) -> None:
    """Run the server until interrupted."""
    jobs = JobRunner(
        config.cache_path / "last_run.json",
        history=History(config.cache_path / "runs"),
    )
    handler = partial(Handler, base_config=config, jobs=jobs)
    httpd = ThreadingHTTPServer((HOST, port), handler)
    url = f"http://{HOST}:{httpd.server_address[1]}"

    print(f"\ncandlebench serving at {url}")
    print("  loopback only; nothing outside this machine can reach it")
    print("  press Ctrl-C to stop\n")

    if open_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped\n")
    finally:
        httpd.server_close()
