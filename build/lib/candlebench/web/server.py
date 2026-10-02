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
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from candlebench import bars, leaderboard, patterns, runner, universe
from candlebench.config import Config, Thresholds, TradeConfig, CostConfig, RunConfig
from candlebench.config import StatsConfig, UniverseConfig, RANK_KEYS, validate
from candlebench.web.jobs import JobRunner, JobState

HOST = "127.0.0.1"
STATIC_DIR = Path(__file__).parent / "static"

# Only these sections may be set from the browser. cache_dir is deliberately
# absent: a request that could choose where bars are written or read would let
# the page reach any path on the machine.
_EDITABLE = {
    "run": {"trials", "seed", "intervals", "throttle_s"},
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

    def do_GET(self) -> None:
        route = urlparse(self.path).path

        if route in ("/", "/index.html"):
            self._static("index.html")
        elif route.startswith("/static/"):
            self._static(route[len("/static/"):])
        elif route == "/api/meta":
            self._json(200, {
                "patterns": describe_patterns(),
                "intervals": list(bars.SUPPORTED_INTERVALS),
                "rank_keys": list(RANK_KEYS),
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

        def work(state: JobState) -> dict:
            def progress(interval, done, total):
                state.done, state.total = done, total
                state.message = f"{interval}: trial {done} of {total}"

            result = runner.run(config, progress=progress)
            return leaderboard.payload(result, config)

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
    jobs = JobRunner(config.cache_path / "last_run.json")
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
