"""Configuration loading and validation.

Unknown keys are rejected rather than ignored. A silently ignored typo in a
threshold would change what the run measures with no visible sign, which is the
one failure mode this tool cannot tolerate.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, fields, replace
from pathlib import Path

RANK_KEYS = ("ci_low", "expectancy_r", "win_rate", "profit_factor", "total_return_pct")

# A symbol becomes a path segment in the bar cache, so it is restricted to
# characters that cannot escape the cache directory or surprise the filesystem.
# Without this, a symbol of "../../../etc/passwd" would be written to and read
# from outside the cache, which matters as soon as a symbol can arrive from the
# browser rather than from a file the user wrote themselves.
SYMBOL_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,9}$")


@dataclass(frozen=True)
class RunConfig:
    trials: int = 200
    seed: int = 42
    # Chronological slices the trials are divided between, so a pattern's
    # expectancy can be read per period rather than only pooled. 1 keeps the
    # single pooled draw.
    windows: int = 1
    intervals: tuple[str, ...] = ("1m", "5m", "15m", "30m", "1h")
    # Where bars come from. "yfinance" needs no credentials and caps intraday
    # history at 28-59 days; "alpaca" needs a free API key from the environment
    # and reaches back to 2016, which is what makes a multi-regime test possible.
    source: str = "yfinance"
    # How far back to fetch. 0 means the source's own default: the provider
    # maximum for yfinance, one year for Alpaca, whose maximum is about a decade
    # and too much to download because a default said nothing.
    lookback_days: int = 0
    cache_dir: str = ".cache/bars"
    throttle_s: float = 0.3


@dataclass(frozen=True)
class UniverseConfig:
    symbols: tuple[str, ...] = ()
    sample_size: int = 50


@dataclass(frozen=True)
class TradeConfig:
    stop_buffer: float = 0.001
    reward_multiple: float = 2.0
    max_hold_bars: int = 20
    min_risk_pct: float = 0.0005
    risk_per_trade_usd: float = 100.0
    allow_overlapping_trades: bool = False


@dataclass(frozen=True)
class CostConfig:
    # "estimated" measures the spread from the cached bars with the
    # Corwin-Schultz high-low estimator; "fixed" charges `slippage_bps` flat.
    # Estimated is the default because the headline finding is about costs, and
    # a flat guess is the weakest possible evidence for it.
    model: str = "estimated"
    slippage_bps: float = 1.0
    commission_per_trade: float = 0.0


@dataclass(frozen=True)
class Thresholds:
    doji_body: float = 0.10
    small_body: float = 0.30
    long_body: float = 0.60
    shadow_dominance: float = 2.0
    opposite_shadow_max: float = 0.25
    doji_shadow_min: float = 0.60
    near_equal: float = 0.001
    gap_min: float = 0.0
    trend_lookback: int = 10
    trend_min_slope: float = 0.0


@dataclass(frozen=True)
class StatsConfig:
    min_trades: int = 30
    min_trades_per_trial: int = 3
    bootstrap_samples: int = 2000
    rank_by: str = "ci_low"


@dataclass(frozen=True)
class Config:
    run: RunConfig = RunConfig()
    universe: UniverseConfig = UniverseConfig()
    trade: TradeConfig = TradeConfig()
    costs: CostConfig = CostConfig()
    thresholds: Thresholds = Thresholds()
    stats: StatsConfig = StatsConfig()
    patterns: tuple[str, ...] = ()

    @property
    def cache_path(self) -> Path:
        """Where this run's bars live, which depends on the source.

        Each source gets its own directory because the two disagree on prices by
        design: Alpaca bars are split-adjusted, Yahoo's with
        `auto_adjust=False` are not. One 1m file half from each would carry a
        fabricated gap wherever the sources met, and nothing downstream could
        detect it. yfinance keeps the original path so an existing cache stays
        valid.
        """
        base = Path(self.run.cache_dir)
        return base if self.run.source == "yfinance" else base / self.run.source


_SECTIONS = {
    "run": RunConfig,
    "universe": UniverseConfig,
    "trade": TradeConfig,
    "costs": CostConfig,
    "thresholds": Thresholds,
    "stats": StatsConfig,
}


def _build_section(name: str, cls, raw: dict):
    allowed = {f.name for f in fields(cls)}
    unknown = set(raw) - allowed
    if unknown:
        raise ValueError(
            f"unknown key(s) in [{name}]: {', '.join(sorted(unknown))}. "
            f"valid keys: {', '.join(sorted(allowed))}"
        )
    coerced = {k: (tuple(v) if isinstance(v, list) else v) for k, v in raw.items()}
    return cls(**coerced)


def load(path: str | Path | None = None) -> Config:
    """Read a TOML config, falling back to built-in defaults for absent keys."""
    if path is None:
        return validate(Config(patterns=_all_enabled()))

    raw = tomllib.loads(Path(path).read_text())
    unknown = set(raw) - set(_SECTIONS) - {"patterns"}
    if unknown:
        raise ValueError(f"unknown config section(s): {', '.join(sorted(unknown))}")

    sections = {
        name: _build_section(name, cls, raw.get(name, {}))
        for name, cls in _SECTIONS.items()
    }

    flags = raw.get("patterns")
    if flags is not None and not isinstance(flags, dict):
        raise ValueError("[patterns] must be a table of name = true/false")

    # An absent [patterns] table means "measure everything". A table that is
    # present but switches everything off means the opposite, and must not be
    # quietly read as the first case.
    enabled = _all_enabled() if flags is None else tuple(n for n, on in flags.items() if on)

    return validate(Config(patterns=enabled, **sections))


def _all_enabled() -> tuple[str, ...]:
    from candlebench import patterns  # imported here to avoid a circular import

    return tuple(patterns.registry())


def validate(config: Config) -> Config:
    """Reject configurations that would produce a meaningless run."""
    from candlebench import bars, costs, patterns

    if config.run.trials <= 0:
        raise ValueError("run.trials must be positive")
    if config.run.throttle_s < 0:
        raise ValueError("run.throttle_s must not be negative")
    if not config.run.intervals:
        raise ValueError("run.intervals must list at least one interval")
    if config.run.source not in bars.SOURCES:
        raise ValueError(
            f"unknown run.source {config.run.source!r}. "
            f"valid: {', '.join(bars.SOURCES)}"
        )
    if config.run.lookback_days < 0:
        raise ValueError("run.lookback_days must not be negative")
    if config.run.lookback_days:
        # Yahoo silently serves less than it is asked for, so accepting a larger
        # number would mean the run measured a different window than the config
        # states.
        over = {
            interval: cap
            for interval in config.run.intervals
            for cap in [bars.provider_cap(config.run.source, interval)]
            if cap is not None and config.run.lookback_days > cap
        }
        if over:
            raise ValueError(
                f"run.lookback_days {config.run.lookback_days} exceeds what "
                f"{config.run.source} serves: "
                + ", ".join(f"{iv} caps at {cap}" for iv, cap in sorted(over.items()))
                + ". lower it, or set source = \"alpaca\" for deeper history."
            )
    if config.run.windows < 1:
        raise ValueError("run.windows must be at least 1")
    if config.run.windows > config.run.trials:
        raise ValueError(
            f"run.windows ({config.run.windows}) asks for more windows than there "
            f"are trials ({config.run.trials}); some window would measure nothing"
        )

    bad = [i for i in config.run.intervals if i not in bars.SUPPORTED_INTERVALS]
    if bad:
        raise ValueError(
            f"unsupported interval(s): {', '.join(bad)}. "
            f"yfinance supports: {', '.join(bars.SUPPORTED_INTERVALS)}. "
            "sub-minute intervals need a tick data provider and are out of scope."
        )

    if config.trade.reward_multiple <= 0:
        raise ValueError("trade.reward_multiple must be positive")
    if config.trade.max_hold_bars < 1:
        raise ValueError("trade.max_hold_bars must be at least 1")
    if config.trade.stop_buffer < 0:
        raise ValueError("trade.stop_buffer must not be negative")
    if config.trade.risk_per_trade_usd <= 0:
        raise ValueError("trade.risk_per_trade_usd must be positive")
    if config.universe.sample_size <= 0:
        raise ValueError("universe.sample_size must be positive")
    if config.costs.model not in costs.MODELS:
        raise ValueError(
            f"unknown costs.model {config.costs.model!r}. "
            f"valid: {', '.join(costs.MODELS)}"
        )
    if config.costs.slippage_bps < 0:
        raise ValueError("costs.slippage_bps must not be negative")

    bad_symbols = [s for s in config.universe.symbols if not SYMBOL_PATTERN.match(s)]
    if bad_symbols:
        raise ValueError(
            f"invalid symbol(s): {', '.join(map(repr, bad_symbols))}. "
            "a symbol must be 1-10 characters of A-Z, 0-9, dot or dash, "
            "because it is used as a filename in the bar cache."
        )

    if config.stats.rank_by not in RANK_KEYS:
        raise ValueError(
            f"unknown stats.rank_by {config.stats.rank_by!r}. "
            f"valid: {', '.join(RANK_KEYS)}"
        )
    if config.stats.bootstrap_samples < 1:
        raise ValueError("stats.bootstrap_samples must be at least 1")

    known = patterns.registry()
    missing = [p for p in config.patterns if p not in known]
    if missing:
        raise ValueError(
            f"unknown pattern(s): {', '.join(missing)}. "
            f"registered: {', '.join(sorted(known))}"
        )
    if not config.patterns:
        raise ValueError("no patterns enabled; there would be nothing to measure")

    return config


def override(config: Config, **kwargs) -> Config:
    """Apply command-line overrides, which outrank the file."""
    run_keys = {f.name for f in fields(RunConfig)}
    run_updates = {k: v for k, v in kwargs.items() if k in run_keys and v is not None}
    patterns = kwargs.get("patterns")

    updated = replace(config, run=replace(config.run, **run_updates))
    if patterns:
        updated = replace(updated, patterns=tuple(patterns))
    return validate(updated)
