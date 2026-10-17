"""The C++ trade walk against the Python one it replaces.

`candlebench._enginecore` exists only for speed, so the one rule it must keep is
that every trade it produces equals the Python engine's to the last bit. A
difference of one ulp in an R multiple would move a bootstrap p-value and,
eventually, a verdict, with nothing in the output to say the engine changed.
Trades are compared by `repr`, which round-trips every float exactly and treats
a NaN as equal to itself.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import numpy as np
import pytest

from candlebench import engine, patterns
from candlebench.config import CostConfig, TradeConfig
from tests.conftest import geometry

pytestmark = pytest.mark.skipif(
    engine._enginecore is None,  # pylint: disable=protected-access
    reason="candlebench._enginecore is not built")

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
