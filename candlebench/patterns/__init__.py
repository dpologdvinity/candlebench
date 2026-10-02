"""The pattern registry.

A detector is one decorated function returning a boolean mask, `True` at the
pattern's final bar. Two safety rules live in the registry wrapper rather than
in the detectors, so that no individual detector can forget them:

1. Bars without enough history are forced to `False`. The reference library this
   project was compared against loops over every row including those lacking
   the bars its own pattern requires.
2. The prior-trend gate is applied centrally, read from the bar *before* the
   pattern starts, so a pattern's own bars cannot define the trend they are
   supposed to reverse.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal

import numpy as np

from candlebench.patterns.context import Geometry

Bias = Literal["bull", "bear"]
Kind = Literal["pattern", "control"]


@dataclass(frozen=True)
class PatternSpec:
    name: str
    bias: Bias
    bars_required: int
    requires_trend: int  # -1 down, 0 any, +1 up
    fn: Callable[[Geometry, object], np.ndarray] | None
    kind: Kind = "pattern"

    @property
    def direction(self) -> int:
        return 1 if self.bias == "bull" else -1


_REGISTRY: dict[str, PatternSpec] = {}


def pattern(
    name: str,
    bias: Bias,
    bars_required: int,
    requires_trend: int = 0,
    kind: Kind = "pattern",
):
    """Register a detector under `name`."""

    def wrap(fn):
        if name in _REGISTRY:
            raise ValueError(f"pattern {name!r} is already registered")
        _REGISTRY[name] = PatternSpec(
            name=name,
            bias=bias,
            bars_required=bars_required,
            requires_trend=requires_trend,
            fn=fn,
            kind=kind,
        )
        return fn

    return wrap


def register_control(name: str, bias: Bias) -> None:
    """Register a null baseline, whose mask the runner supplies directly."""
    _REGISTRY[name] = PatternSpec(
        name=name, bias=bias, bars_required=1, requires_trend=0, fn=None, kind="control"
    )


def registry() -> dict[str, PatternSpec]:
    _load_detectors()
    return dict(_REGISTRY)


def get(name: str) -> PatternSpec:
    return registry()[name]


_loaded = False


def _load_detectors() -> None:
    """Import the detector modules so their decorators run."""
    global _loaded
    if _loaded:
        return
    _loaded = True
    from candlebench.patterns import control, double, single, triple  # noqa: F401


def first_valid_index(spec: PatternSpec, trend_lookback: int) -> int:
    """The earliest bar at which this pattern may legitimately fire.

    The pattern needs `bars_required` bars, and the trend gate reads the bar
    before them, which itself needs a complete `trend_lookback` window.
    """
    return spec.bars_required - 1 + trend_lookback


def detect(spec: PatternSpec, geom: Geometry, thresholds) -> np.ndarray:
    """Run a detector and apply the history and trend gates."""
    if spec.fn is None:
        raise ValueError(f"{spec.name} is a control; its mask is supplied by the runner")

    mask = np.asarray(spec.fn(geom, thresholds), dtype=bool)
    if mask.shape != geom.close.shape:
        raise ValueError(f"{spec.name} returned a mask of the wrong length")

    return apply_gates(mask, spec, geom)


def apply_gates(mask: np.ndarray, spec: PatternSpec, geom: Geometry) -> np.ndarray:
    """Zero out bars lacking history, then apply the prior-trend requirement."""
    mask = mask.copy()
    cutoff = first_valid_index(spec, geom.trend_lookback)
    mask[: min(cutoff, len(mask))] = False

    if spec.requires_trend:
        indices = np.arange(len(mask)) - spec.bars_required
        in_range = indices >= 0
        trend_at = np.zeros(len(mask), dtype=np.int8)
        trend_at[in_range] = geom.trend[indices[in_range]]
        mask &= in_range & (trend_at == spec.requires_trend)

    return mask
