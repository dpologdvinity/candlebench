"""Trial selection.

A trial is one symbol and one trading session. Every enabled pattern then runs
on every enabled timeframe over that same session.

The pairing is deliberate. If each pattern drew its own random symbol and day,
the leaderboard would mostly reflect which pattern happened to draw a trending
day, and reaching usable confidence would take orders of magnitude more trials.
Paired sampling puts every pattern in front of identical market conditions, so
the comparison between patterns stays clean even where the absolute numbers are
noisy.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np

from candlebench import bars


@dataclass(frozen=True)
class Trial:
    index: int
    symbol: str
    session: date
    seed: int


def narrowest_interval(intervals: tuple[str, ...]) -> str:
    """The finest enabled interval, whose coverage is the scarcest."""
    return min(intervals, key=lambda i: bars.INTERVAL_MINUTES[i])


def draw_trials(
    symbols: tuple[str, ...],
    intervals: tuple[str, ...],
    cache_dir: Path,
    trials: int,
    rng: np.random.Generator,
) -> list[Trial]:
    """Draw (symbol, session) pairs from what the cache actually holds.

    Sessions are drawn against the narrowest enabled interval, because a session
    present at 1m is present at every coarser interval but not the reverse.
    Drawing against a coarse interval would produce trials with no fine-grained
    data.
    """
    pool_by_symbol = bars.available_sessions(symbols, narrowest_interval(intervals), cache_dir)
    pool = [(symbol, day) for symbol, days in sorted(pool_by_symbol.items()) for day in days]
    if not pool:
        raise RuntimeError(
            "no cached sessions to sample. run `python -m candlebench fetch` first."
        )

    # Without replacement while the pool allows, so a small cache does not
    # produce a leaderboard built from the same day counted many times.
    replace = trials > len(pool)
    chosen = rng.choice(len(pool), size=trials, replace=replace)

    # One root seed drives everything, and each trial gets a derived child seed
    # so a single trial can be replayed in isolation when debugging.
    child_seeds = rng.integers(0, 2**32 - 1, size=trials)

    return [
        Trial(index=n, symbol=pool[int(k)][0], session=pool[int(k)][1], seed=int(seed))
        for n, (k, seed) in enumerate(zip(chosen, child_seeds))
    ]
