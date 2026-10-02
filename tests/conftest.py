"""Shared fixtures.

Detectors are tested against hand-built bars rather than market data. A fixture
states the intended shape unambiguously; a real chart only happens to contain
one, and says nothing about why.
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from candlebench.config import CostConfig, Thresholds, TradeConfig
from candlebench.patterns import context

Row = tuple[float, float, float, float]

TREND_BARS = 10  # matches the default trend_lookback, so a pattern sits at the cutoff


def arrays(rows: list[Row], volume: float = 1000.0) -> dict[str, np.ndarray]:
    data = np.asarray(rows, dtype=np.float64)
    return {
        "open": data[:, 0],
        "high": data[:, 1],
        "low": data[:, 2],
        "close": data[:, 3],
        "volume": np.full(len(rows), volume),
    }


def geometry(rows: list[Row], lookback: int = TREND_BARS, min_slope: float = 0.0):
    return context.geometry(arrays(rows), lookback, min_slope)


def falling(n: int = TREND_BARS, start: float = 100.0, step: float = -0.5) -> list[Row]:
    """Ordinary down bars, enough of them to label the prior trend."""
    out = []
    for i in range(n):
        p = start + i * step
        out.append((p, p + 0.05, p - 0.35, p - 0.3))
    return out


def rising(n: int = TREND_BARS, start: float = 100.0, step: float = 0.5) -> list[Row]:
    """Ordinary up bars, enough of them to label the prior trend."""
    out = []
    for i in range(n):
        p = start + i * step
        out.append((p, p + 0.35, p - 0.05, p + 0.3))
    return out


def prefixed(direction: str, rows: list[Row]) -> list[Row]:
    return (falling() if direction == "down" else rising()) + rows


@pytest.fixture
def thresholds() -> Thresholds:
    return Thresholds()


@pytest.fixture
def trade() -> TradeConfig:
    """Trade parameters with overlap allowed, so a test controls its own signals."""
    return replace(TradeConfig(), max_hold_bars=10, allow_overlapping_trades=True)


@pytest.fixture
def free() -> CostConfig:
    """No slippage or commission, so price assertions stay exact."""
    return CostConfig(slippage_bps=0.0, commission_per_trade=0.0)
