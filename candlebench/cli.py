"""Command-line entry point.

    python -m candlebench demo       offline end-to-end run on synthetic bars
    python -m candlebench fetch      warm the bar cache
    python -m candlebench run        measure and rank
    python -m candlebench patterns   list what is registered
"""

from __future__ import annotations

import argparse
from pathlib import Path

from candlebench import (
    bars,
    config as config_module,
    leaderboard,
    patterns,
    runner,
    trades,
    universe,
)

DEFAULT_CONFIG = Path("config/backtest.toml")
DEMO_CACHE = Path(".cache/demo")


def _positive_int(text: str) -> int:
    """An argparse type for counts. `--sessions 0` used to select every session via `[-0:]`."""
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError(f"must be at least 1, got {value}")
    return value


def _split(value: str | None) -> tuple[str, ...] | None:
    return tuple(part.strip() for part in value.split(",") if part.strip()) if value else None


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="candlebench",
        description="Rank classic candlestick patterns by measured intraday edge.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    for name, help_text in (
        ("fetch", "download and cache intraday bars"),
        ("run", "run the backtest and print the leaderboard"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--config", type=Path, default=None,
                       help=f"TOML config file (default: {DEFAULT_CONFIG} if present)")
        p.add_argument("--intervals", type=str, default=None,
                       help="comma-separated intervals, overriding the config")

    run = sub.choices["run"]
    run.add_argument("--trials", type=int, default=None, help="number of trials")
    run.add_argument("--holdout-fraction", type=float, default=None,
                     help="newest date fraction reserved for validation; 0 is exploratory")
    run.add_argument("--seed", type=int, default=None, help="random seed")
    run.add_argument("--patterns", type=str, default=None,
                     help="comma-separated pattern names, overriding the config")
    run.add_argument("-v", "--verbose", action="store_true",
                     help="add gross vs net, exit mix, holding period and drawdown")
    run.add_argument("--json", type=Path, default=None, help="write results as JSON")
    run.add_argument("--csv", type=Path, default=None, help="write results as CSV")
    run.add_argument("--html", type=Path, default=None,
                     help="write a self-contained HTML report")

    rep = sub.add_parser(
        "report", help="render a saved JSON result as a self-contained HTML report"
    )
    rep.add_argument("results", type=Path, help="a JSON file written by `run --json`")
    rep.add_argument("--trades", type=Path, default=None,
                     help="its trade file (default: the JSON path with .parquet, if present)")
    rep.add_argument("--out", type=Path, default=None,
                     help="where to write the report (default: the JSON path with .html)")
    rep.add_argument("--title", type=str, default=None, help="the report's heading")

    pub = sub.add_parser(
        "site", help="export a saved result as an interactive static dashboard (no server)"
    )
    pub.add_argument("results", type=Path, help="a JSON file written by `run --json` or a dashboard run")
    pub.add_argument("--out", type=Path, required=True, help="directory to write the site into")
    pub.add_argument("--trades", type=Path, default=None,
                     help="its trade file (default: the JSON path with .parquet, or last_run_trades.parquet)")
    pub.add_argument("--prices", choices=("auto", "yes", "no"), default="auto",
                     help="publish prices and session charts; auto = only for synthetic data")
    pub.add_argument("--title", type=str, default=None, help="the page and report heading")

    sub.add_parser("patterns", help="list registered patterns")

    demo = sub.add_parser(
        "demo", help="generate synthetic bars, run, and open the dashboard; no network or key"
    )
    demo.add_argument("--cache-dir", type=Path, default=DEMO_CACHE,
                      help=f"where the synthetic bars and results go (default: {DEMO_CACHE})")
    demo.add_argument("--trials", type=_positive_int, default=150, help="number of trials")
    demo.add_argument("--port", type=int, default=8765, help="port to listen on")
    demo.add_argument("--no-serve", action="store_true",
                      help="print the leaderboard and exit instead of serving the dashboard")
    demo.add_argument("--no-browser", action="store_true",
                      help="do not open a browser window automatically")

    spreads = sub.add_parser(
        "quotes", help="sample NBBO quotes into an observed half-spread table"
    )
    spreads.add_argument("--config", type=Path, default=None,
                         help=f"TOML config file (default: {DEFAULT_CONFIG} if present)")
    spreads.add_argument("--sessions", type=_positive_int, default=5,
                         help="how many cached sessions to sample per symbol")

    serve = sub.add_parser("serve", help="browse results and trigger runs in a browser")
    serve.add_argument("--config", type=Path, default=None,
                       help=f"TOML config file (default: {DEFAULT_CONFIG} if present)")
    serve.add_argument("--port", type=int, default=8765, help="port to listen on")
    serve.add_argument("--no-browser", action="store_true",
                       help="do not open a browser window automatically")
    for serving in (serve, demo):
        serving.add_argument("--read-only", action="store_true",
                             help="browse saved runs only; refuse to start runs or fetches")
        serving.add_argument("--host", default="127.0.0.1",
                             help="address to listen on; anything but loopback needs --read-only")

    return parser.parse_args(argv)


def _load_config(path: Path | None):
    chosen = path or (DEFAULT_CONFIG if DEFAULT_CONFIG.exists() else None)
    return config_module.load(chosen)


def _list_patterns() -> int:
    registry = patterns.registry()
    width = max(len(name) for name in registry)
    print()
    for name, spec in sorted(registry.items(), key=lambda kv: (kv[1].kind, kv[1].bars_required, kv[0])):
        trend = {-1: "after downtrend", 0: "any trend", 1: "after uptrend"}[spec.requires_trend]
        kind = " (control)" if spec.kind == "control" else ""
        print(f"  {name:<{width}}  {spec.bias:<4}  {spec.bars_required}-bar  {trend}{kind}")
    print(f"\n  {len(registry)} registered\n")
    return 0


def _render_report(args) -> int:
    """Render a saved result. A malformed file is refused with a reason, not a traceback."""
    import json

    from candlebench import report

    try:
        saved = json.loads(args.results.read_text())
    except (OSError, ValueError) as exc:
        print(f"\ncannot read {args.results}: {exc}\n")
        return 1
    if not isinstance(saved, dict) or not isinstance(saved.get("stats"), list):
        print(f"\n{args.results} is not a candlebench result: it has no stats.\n")
        return 1
    trade_path = args.trades or args.results.with_suffix(".parquet")
    frame = None
    if trade_path.exists():
        from candlebench import site

        try:
            # Tolerant of a file published without its price columns.
            frame = site.read_trades(trade_path)
        except (OSError, ValueError) as exc:
            print(f"\ncannot read trades from {trade_path}: {exc}\n")
            return 1
    elif args.trades is not None:
        print(f"\nno trade file at {trade_path}\n")
        return 1
    out = report.write(saved, args.out or args.results.with_suffix(".html"), frame, args.title)
    detail = "with per-pattern drill-downs" if frame is not None else "without trades, so no drill-downs"
    print(f"\nwrote {out} ({detail})\n")
    return 0


def _export_site(args) -> int:
    """Export a saved run as a static, interactive copy of the dashboard."""
    import json

    from candlebench import site

    try:
        saved = json.loads(args.results.read_text())
    except (OSError, ValueError) as exc:
        print(f"\ncannot read {args.results}: {exc}\n")
        return 1
    if not isinstance(saved, dict) or not isinstance(saved.get("stats"), list):
        print(f"\n{args.results} is not a candlebench result: it has no stats.\n")
        return 1
    candidates = [args.trades] if args.trades else [
        args.results.with_suffix(".parquet"),
        args.results.with_name(f"{args.results.stem}_trades.parquet"),
    ]
    trade_path = next((p for p in candidates if p.exists()), None)
    if trade_path is None:
        print(f"\nno trade file found; tried {', '.join(map(str, candidates))}\n")
        return 1
    synthetic = saved["config"]["run"]["source"] == "synthetic"
    prices = {"yes": True, "no": False, "auto": synthetic}[args.prices]
    if prices and not synthetic:
        print("\nwarning: publishing prices from licensed market data; check its terms first")
    out = site.export(args.out, saved, site.read_trades(trade_path), prices=prices, title=args.title)
    print(f"\nwrote {out} ({'with' if prices else 'without'} prices and session charts)\n")
    return 0


def demo_config(cache_dir: Path, trials: int = 150):
    """The demo's settings: every pattern on synthetic bars at three timeframes.

    4,000 bootstrap draws rather than the default 10,000 keep it to seconds,
    while still clearing the resolution floor for this family (168 hypotheses,
    so corrected p-values can reach 0.042).
    """
    from dataclasses import replace

    from candlebench import synthetic

    base = config_module.load(None)
    return config_module.validate(replace(
        base,
        run=replace(base.run, source="synthetic", intervals=("1m", "5m", "15m"),
                    trials=trials, cache_dir=str(cache_dir), throttle_s=0.0),
        universe=replace(base.universe, symbols=synthetic.SYMBOLS,
                         sample_size=len(synthetic.SYMBOLS)),
        stats=replace(base.stats, bootstrap_samples=4000),
    ))


def _demo(args) -> int:
    """Run the whole pipeline offline, so a fresh clone shows real output at once.

    The bars are a driftless random walk, so the expected answer is that no
    pattern has an edge. The run is published exactly as a dashboard run would
    be, which is what lets `serve` open with results, trades and charts.
    """
    from candlebench.web.history import History
    from candlebench.web.jobs import JobResult, JobRunner

    cfg = demo_config(args.cache_dir, args.trials)
    symbols = universe.resolve(cfg.universe.symbols, cfg.universe.sample_size)
    print(f"\ngenerating synthetic bars for {len(symbols)} symbols into {cfg.cache_path}")
    report = bars.warm_cache(symbols, cfg.run.intervals, cfg.cache_path, 0.0,
                             source=cfg.run.source)
    if report.failures:
        print(report.summary())
        return 1

    print(f"running {cfg.run.trials} trials over {', '.join(cfg.run.intervals)}\n")
    result = runner.run(cfg)
    print(leaderboard.render(result, cfg))

    jobs = JobRunner(cfg.cache_path / "last_run.json", history=History(cfg.cache_path / "runs"))
    jobs.submit("run", lambda state: JobResult(leaderboard.payload(result, cfg), result.trades))
    jobs._thread.join()
    if jobs.state["status"] != "done":
        print(f"could not save the demo run: {jobs.state['error']}")
        return 1
    if args.no_serve:
        return 0

    from candlebench.web.server import serve

    return _serve(serve, cfg, args)


def _serve(serve, cfg, args) -> int:
    try:
        serve(cfg, port=args.port, open_browser=not args.no_browser,
              read_only=args.read_only, host=args.host)
    except ValueError as exc:
        print(f"\n{exc}\n")
        return 2
    return 0


def _build_quote_table(args) -> int:
    """Sample quotes into the observed half-spread table.

    Separate from `fetch` because it needs no bars of its own and is cheap: nine
    requests per symbol-session against the bar fetch's hundreds. The sessions
    come from the cache so the table covers days the backtest will actually draw.
    """
    from candlebench import quotes

    cfg = _load_config(args.config)
    symbols = universe.resolve(cfg.universe.symbols, cfg.universe.sample_size)
    interval = min(cfg.run.intervals, key=lambda i: bars.INTERVAL_MINUTES[i])
    available = bars.available_sessions(symbols, interval, cfg.cache_path)
    sessions = sorted({day for days in available.values() for day in days})[-args.sessions:]
    if not sessions:
        print(f"\nno cached {interval} sessions to sample. run fetch first.\n")
        return 1

    print(
        f"\nsampling quotes for {len(symbols)} symbols over "
        f"{len(sessions)} sessions ({sessions[0]} .. {sessions[-1]})\n"
        f"{len(quotes.SAMPLE_MINUTES) * quotes.SAMPLES_PER_BUCKET} requests per "
        f"symbol-session\n"
    )
    # The default sleep honours the pause each call asks for: the provider's
    # page interval, and the longer backoff after a 429. A fixed short sleep
    # here once replaced both and reproduced the throttling it was meant to avoid.
    table = quotes.build_table(symbols, [d.isoformat() for d in sessions])
    path = quotes.write_table(table, cfg.costs.quote_table)
    buckets = {}
    for key, value in table.items():
        buckets.setdefault(key.split(quotes.SEPARATOR, 1)[1], []).append(value)
    print(f"wrote {path} with {len(table)} entries")
    for bucket in ("open", "midday", "close"):
        values = buckets.get(bucket) or []
        if values:
            import statistics

            print(f"  {bucket:<7} median {statistics.median(values):5.2f} bps/leg "
                  f"across {len(values)} symbols")
    print()
    return 0


def main(argv=None) -> int:
    args = parse_args(argv)

    if args.command == "patterns":
        return _list_patterns()

    if args.command == "quotes":
        return _build_quote_table(args)

    if args.command == "demo":
        return _demo(args)

    if args.command == "report":
        return _render_report(args)

    if args.command == "site":
        return _export_site(args)

    cfg = _load_config(args.config)

    if args.command == "serve":
        from candlebench.web.server import serve

        return _serve(serve, cfg, args)

    cfg = config_module.override(
        cfg,
        intervals=_split(args.intervals),
        trials=getattr(args, "trials", None),
        seed=getattr(args, "seed", None),
        holdout_fraction=getattr(args, "holdout_fraction", None),
        patterns=_split(getattr(args, "patterns", None)),
    )

    if args.command == "fetch":
        symbols = universe.resolve(cfg.universe.symbols, cfg.universe.sample_size)
        spans = {
            i: bars.lookback_days(cfg.run.source, i, cfg.run.lookback_days)
            for i in cfg.run.intervals
        }
        print(
            f"\nfetching {len(symbols)} symbols x {len(cfg.run.intervals)} intervals "
            f"from {cfg.run.source} into {cfg.cache_path}\n"
            f"{', '.join(f'{i}: {d}d' for i, d in spans.items())}\n"
        )
        report = bars.warm_cache(
            symbols, cfg.run.intervals, cfg.cache_path, cfg.run.throttle_s,
            source=cfg.run.source, lookback_days=cfg.run.lookback_days,
        )
        print(report.summary())
        print()
        return 0 if report.written else 1

    result = runner.run(cfg)
    print(leaderboard.render(result, cfg, verbose=args.verbose))
    if args.json:
        leaderboard.write_json(result, cfg, args.json)
        # The trades go beside the report rather than inside it. A full run's
        # trades are roughly a hundred times the report's size, so embedding
        # them would make the JSON unreadable for the sake of data only a
        # breakdown needs.
        trade_file = trades.write(result.trades, args.json.with_suffix(".parquet"))
        print(f"  wrote {args.json} and {trade_file} ({len(result.trades):,} trades)")
    if args.csv:
        leaderboard.write_csv(result, args.csv)
        print(f"  wrote {args.csv}")
    if args.html:
        from candlebench import report

        path = report.write(leaderboard.payload(result, cfg), args.html, trades.to_frame(result.trades))
        print(f"  wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
