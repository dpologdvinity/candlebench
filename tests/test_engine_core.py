"""The C++ trade walk against the Python one it replaces.

`candlebench._enginecore` exists only for speed, so the one rule it must keep is
that every trade it produces equals the Python engine's to the last bit. A
difference of one ulp in an R multiple would move a bootstrap p-value and,
eventually, a verdict, with nothing in the output to say the engine changed.
Trades are compared by `repr`, which round-trips every float exactly and treats
a NaN as equal to itself.
"""

from __future__ import annotations

import os
from dataclasses import replace
from datetime import date

import numpy as np
import pytest

from candlebench import engine, patterns
from candlebench.config import CostConfig, TradeConfig
from tests.conftest import geometry

NATIVE = engine._enginecore is not None  # pylint: disable=protected-access
needs_native = pytest.mark.skipif(not NATIVE, reason="candlebench._enginecore is not built")

SESSION = date(2026, 9, 15)


def random_session(rng: np.random.Generator, n: int):
    """A random walk with the bars the exit rules treat specially: gaps through
    a level, bars with no range, and missing opens and closes."""
    close = 100.0 * np.exp(np.cumsum(rng.normal(0, 0.002, n)))
    open_ = np.r_[100.0, close[:-1]] * np.exp(rng.normal(0, 0.0005, n))
    gaps = rng.random(n) < 0.05
    open_[gaps] *= np.exp(rng.normal(0, 0.01, gaps.sum()))
    high = np.maximum(open_, close) * np.exp(np.abs(rng.normal(0, 0.001, n)))
    low = np.minimum(open_, close) * np.exp(-np.abs(rng.normal(0, 0.001, n)))
    flat = rng.random(n) < 0.03
    open_[flat] = high[flat] = low[flat] = close[flat]
    # Missing opens and closes only: a missing low or high would leave the
    # rolling extreme an all-NaN slice, which validation never lets through.
    open_[rng.random(n) < 0.01] = np.nan
    close[rng.random(n) < 0.01] = np.nan
    rows = np.column_stack([open_, high, low, close])
    # Rounding to a cent makes ties between a level and a price common.
    return np.round(rows, 2).tolist()


def both_paths(monkeypatch, call):
    native = call()
    with monkeypatch.context() as patch:
        patch.setattr(engine, "_enginecore", None)
        python = call()
    return [repr(t) for t in native], [repr(t) for t in python]


CASES = [
    (seed, name, overlap, cost)
    for seed in range(12)
    for name in ("hammer", "hanging_man")
    for overlap in (False, True)
    for cost in ("none", "flat", "per_bar")
]


def test_the_native_engine_is_built_where_it_is_required():
    """`optional=True` lets a failed compile install quietly, which would skip
    every parity test below and leave CI green. CI sets the variable."""
    if os.environ.get("CANDLEBENCH_REQUIRE_NATIVE") == "1":
        assert NATIVE, "candlebench._enginecore failed to build"


@needs_native
@pytest.mark.parametrize("seed,name,overlap,cost", CASES)
def test_native_trades_equal_the_python_engine_bit_for_bit(monkeypatch, seed, name, overlap, cost):
    """Pattern trades and their matched controls, over sessions with gaps,
    flat bars and NaNs, under every cost shape `simulate` accepts."""
    rng = np.random.default_rng(seed)
    n = int(rng.integers(2, 400))
    geom = geometry(random_session(rng, n), lookback=3)
    spec = patterns.get(name)
    mask = rng.random(n) < 0.15
    trade_cfg = replace(TradeConfig(), allow_overlapping_trades=overlap,
                        max_hold_bars=int(rng.integers(1, 30)),
                        reward_multiple=float(rng.choice([0.5, 1.0, 2.0, 3.3])))
    cost_cfg = CostConfig(slippage_bps=1.7, commission_per_trade=0.35)
    one_way_cost = {"none": None, "flat": 0.00031,
                    "per_bar": rng.uniform(0, 0.001, int(rng.integers(1, n + 1)))}[cost]
    bar_minutes = None if seed % 3 == 0 else np.arange(n) * 5

    def simulate():
        return engine.simulate(
            geom, mask, spec, trade_cfg, cost_cfg, symbol="TEST", interval="5m",
            session=SESSION, trial_index=seed, window=seed % 2, bar_minutes=bar_minutes,
            one_way_cost=one_way_cost, sample="validation")

    native, python = both_paths(monkeypatch, simulate)
    assert native == python

    template = simulate()
    native, python = both_paths(monkeypatch, lambda: engine.simulate_matched(
        geom, template, trade_cfg, cost_cfg, np.random.default_rng(seed),
        bar_minutes=bar_minutes, one_way_cost=one_way_cost, sample="validation"))
    assert native == python


def test_matched_draws_consume_the_generator_as_one_choice_per_trade_did():
    """The vectorised draw must leave the controls unchanged: a single bounded
    `integers` call reproduces one `rng.choice(range(...))` per drawable trade,
    including one-bar ranges and skipped trades with nothing to draw."""
    low = np.random.default_rng(1).integers(1, 300, 2000)
    high = low + np.random.default_rng(2).integers(-1, 5, 2000)
    drawable = high >= low

    sequential = np.random.default_rng(7)
    expected = [int(sequential.choice(range(lo, hi + 1)))
                for lo, hi in zip(low[drawable], high[drawable])]

    vectorised = np.random.default_rng(7)
    got = low[drawable] + vectorised.integers(0, high[drawable] - low[drawable] + 1)

    assert got.tolist() == expected
    assert sequential.random() == vectorised.random()


@needs_native
@pytest.mark.parametrize("hold", [2**63 - 1, 2**63, 2**70])
def test_a_hold_longer_than_any_session_exits_as_a_long_one_does(monkeypatch, hold):
    """Regression: `start + max_hold_bars - 1` overflowed int64 in the kernel,
    so a browser-set hold of 2**63 - 1 read prices out of bounds, and 2**63
    was refused by the binding. Any hold past the session's end is the same
    trade, at the last bar with `session_end`."""
    rng = np.random.default_rng(3)
    geom = geometry(random_session(rng, 120), lookback=3)
    mask = rng.random(120) < 0.2
    spec = patterns.get("hammer")

    def run(max_hold_bars):
        cfg = replace(TradeConfig(), max_hold_bars=max_hold_bars)
        made = engine.simulate(geom, mask, spec, cfg, CostConfig(), symbol="T", interval="1m",
                               session=SESSION, trial_index=0)
        controls = engine.simulate_matched(geom, made, cfg, CostConfig(), np.random.default_rng(0))
        return made + controls

    expected = [repr(t) for t in run(10**6)]
    native_huge, python_huge = both_paths(monkeypatch, lambda: run(hold))
    assert native_huge == python_huge == expected
    assert any("session_end" in t for t in expected)


@needs_native
def test_a_missing_bar_minute_is_refused_on_both_paths(monkeypatch):
    """The Python walk raises on `int(nan)`; the native one used to cast it
    to INT64_MIN and store that as the entry minute."""
    rng = np.random.default_rng(5)
    geom = geometry(random_session(rng, 80), lookback=3)
    minutes = np.arange(80, dtype=np.float64)
    minutes[:] = np.nan

    def run():
        return engine.simulate(geom, np.ones(80, dtype=bool), patterns.get("hammer"),
                               TradeConfig(), CostConfig(), symbol="T", interval="1m",
                               session=SESSION, trial_index=0, bar_minutes=minutes)

    with pytest.raises(ValueError):
        run()
    with monkeypatch.context() as patch:
        patch.setattr(engine, "_enginecore", None)
        with pytest.raises(ValueError):
            run()
