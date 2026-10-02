"""Configuration loading and validation.

Unknown keys are rejected rather than ignored. A silently ignored typo in a
threshold would change what the run measures with no visible sign, which is the
one failure mode this tool cannot tolerate.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, fields, replace
from pathlib import Path

RANK_KEYS = ("ci_low", "expectancy_r", "win_rate", "profit_factor", "total_return_pct")


@dataclass(frozen=True)
class RunConfig:
    trials: int = 200
    seed: int = 42
    intervals: tuple[str, ...] = ("1m", "5m", "15m", "30m", "1h")
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
    force_close_at_session_end: bool = True


@dataclass(frozen=True)
class CostConfig:
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
        return Path(self.run.cache_dir)


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

    flags = raw.get("patterns", {})
    if not isinstance(flags, dict):
        raise ValueError("[patterns] must be a table of name = true/false")
    enabled = tuple(name for name, on in flags.items() if on)

    return validate(Config(patterns=enabled or _all_enabled(), **sections))


def _all_enabled() -> tuple[str, ...]:
    from candlebench import patterns  # imported here to avoid a circular import

    return tuple(patterns.registry())


def validate(config: Config) -> Config:
    """Reject configurations that would produce a meaningless run."""
    from candlebench import bars, patterns

    if config.run.trials <= 0:
        raise ValueError("run.trials must be positive")
    if config.run.throttle_s < 0:
        raise ValueError("run.throttle_s must not be negative")
    if not config.run.intervals:
        raise ValueError("run.intervals must list at least one interval")

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
