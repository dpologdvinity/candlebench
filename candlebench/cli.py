"""Command-line entry point.

    python -m candlebench fetch      warm the bar cache
    python -m candlebench run        measure and rank
    python -m candlebench patterns   list what is registered
"""

from __future__ import annotations

import argparse
from pathlib import Path

from candlebench import bars, config as config_module, leaderboard, patterns, runner, universe

DEFAULT_CONFIG = Path("config/backtest.toml")


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
    run.add_argument("--seed", type=int, default=None, help="random seed")
    run.add_argument("--patterns", type=str, default=None,
                     help="comma-separated pattern names, overriding the config")
    run.add_argument("-v", "--verbose", action="store_true",
                     help="add gross vs net, exit mix, holding period and drawdown")
    run.add_argument("--json", type=Path, default=None, help="write results as JSON")
    run.add_argument("--csv", type=Path, default=None, help="write results as CSV")

    sub.add_parser("patterns", help="list registered patterns")

    serve = sub.add_parser("serve", help="browse results and trigger runs in a browser")
    serve.add_argument("--config", type=Path, default=None,
                       help=f"TOML config file (default: {DEFAULT_CONFIG} if present)")
    serve.add_argument("--port", type=int, default=8765, help="port to listen on")
    serve.add_argument("--no-browser", action="store_true",
                       help="do not open a browser window automatically")

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


def main(argv=None) -> int:
    args = parse_args(argv)

    if args.command == "patterns":
        return _list_patterns()

    cfg = _load_config(args.config)

    if args.command == "serve":
        from candlebench.web.server import serve

        serve(cfg, port=args.port, open_browser=not args.no_browser)
        return 0

    cfg = config_module.override(
        cfg,
        intervals=_split(args.intervals),
        trials=getattr(args, "trials", None),
        seed=getattr(args, "seed", None),
        patterns=_split(getattr(args, "patterns", None)),
    )

    if args.command == "fetch":
        symbols = universe.resolve(cfg.universe.symbols, cfg.universe.sample_size)
        print(
            f"\nfetching {len(symbols)} symbols x {len(cfg.run.intervals)} intervals "
            f"into {cfg.cache_path}\nyfinance caps intraday history, so this is "
            f"{', '.join(f'{i}: {bars.INTERVAL_MAX_LOOKBACK_DAYS[i]}d' for i in cfg.run.intervals)}\n"
        )
        report = bars.warm_cache(
            symbols, cfg.run.intervals, cfg.cache_path, cfg.run.throttle_s
        )
        print(report.summary())
        print()
        return 0 if report.written else 1

    result = runner.run(cfg)
    print(leaderboard.render(result, cfg, verbose=args.verbose))
    if args.json:
        leaderboard.write_json(result, cfg, args.json)
        print(f"  wrote {args.json}")
    if args.csv:
        leaderboard.write_csv(result, args.csv)
        print(f"  wrote {args.csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
