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

from dataclasses import dataclass, replace
from datetime import date
import math
from pathlib import Path

import numpy as np

from candlebench import bars


@dataclass(frozen=True)
class Trial:
    index: int
    symbol: str
    session: date
    seed: int
    # Which chronological slice of the cache this trial was drawn from. 0 for
    # every trial when `run.windows` is 1, which is the default.
    window: int = 0
    sample: str = "discovery"
    holdout_start: date | None = None


def narrowest_interval(intervals: tuple[str, ...]) -> str:
    """The finest enabled interval, whose coverage is the scarcest."""
    return min(intervals, key=lambda i: bars.INTERVAL_MINUTES[i])


def window_counts(trials: int, windows: int) -> list[int]:
    """How many trials each window draws, the remainder going to the earliest."""
    base, extra = divmod(trials, windows)
    return [base + (1 if w < extra else 0) for w in range(windows)]


def session_windows(days: list[date], windows: int) -> list[list[date]]:
    """Split sessions into contiguous chronological groups of near-equal size."""
    return [list(group) for group in np.array_split(np.array(sorted(days)), windows)]


def draw_trials(
    symbols: tuple[str, ...],
    intervals: tuple[str, ...],
    cache_dir: Path,
    trials: int,
    rng: np.random.Generator,
    windows: int = 1,
    holdout_fraction: float = 0.0,
) -> list[Trial]:
    """Draw (symbol, session) pairs from what the cache actually holds.

    Sessions are drawn against the narrowest enabled interval, because a session
    present at 1m is present at every coarser interval but not the reverse.
    Drawing against a coarse interval would produce trials with no fine-grained
    data.

    With `windows` above 1 the cache's sessions are split into that many
    contiguous chronological groups and the trials are divided between them, so a
    pattern's expectancy can be read per period. One pooled draw cannot be split
    after the fact: a pattern that earned its edge in week one and gave it back in
    week four looks identical to one that earned it steadily.
    """
    pool_by_symbol = bars.available_sessions(symbols, narrowest_interval(intervals), cache_dir)
    pool = [(symbol, day) for symbol, days in sorted(pool_by_symbol.items()) for day in days]
    if not pool:
        raise RuntimeError(
            "no cached sessions to sample. run `python -m candlebench fetch` first."
        )

    if holdout_fraction:
        dates = sorted({day for _, day in pool})
        if len(dates) < 2:
            raise RuntimeError("holdout validation needs at least two distinct cached sessions")
        reserved = min(len(dates) - 1, math.ceil(len(dates) * holdout_fraction))
        cutoff = dates[-reserved]
        validation_count = math.ceil(trials * holdout_fraction)
        discovery_count = trials - validation_count
        if discovery_count < windows:
            raise RuntimeError("holdout leaves too few discovery trials for the requested windows")
        discovery = _draw_pool([(s, d) for s, d in pool if d < cutoff],
                               discovery_count, rng, windows)
        # Validation uses a child stream, never consuming discovery random draws.
        validation_rng = np.random.default_rng(int(rng.integers(0, 2**32 - 1)))
        validation = _draw_pool([(s, d) for s, d in pool if d >= cutoff],
                                validation_count, validation_rng, 1,
                                sample="validation", start_index=len(discovery))
        return [replace(t, holdout_start=cutoff) for t in discovery + validation]
    return _draw_pool(pool, trials, rng, windows)


def _draw_pool(pool, trials, rng, windows, sample="discovery", start_index=0):
    distinct = sorted({day for _, day in pool})
    if windows > len(distinct):
        raise RuntimeError(
            f"{windows} walk-forward windows requested but the cache holds "
            f"fewer cached sessions ({len(distinct)}). lower run.windows, or "
            "fetch more history."
        )

    groups = session_windows(distinct, windows)
    counts = window_counts(trials, windows)

    # The index draws come first and the seed draws second, in one call each per
    # window. At windows == 1 that is the identical sequence of calls the pooled
    # version made, so a default run reproduces every result recorded before
    # walk-forward existed.
    picked: list[tuple[int, list, int]] = []
    for window, (group, count) in enumerate(zip(groups, counts)):
        if not count:
            continue
        date_set = set(group)
        subset = [pair for pair in pool if pair[1] in date_set]
        # Without replacement while the pool allows, so a small cache does not
        # produce a leaderboard built from the same day counted many times.
        chosen = rng.choice(len(subset), size=count, replace=count > len(subset))
        picked.append((window, subset, chosen))

    # One root seed drives everything, and each trial gets a derived child seed
    # so a single trial can be replayed in isolation when debugging.
    child_seeds = rng.integers(0, 2**32 - 1, size=trials)

    out: list[Trial] = []
    for window, subset, chosen in picked:
        for k in chosen:
            symbol, day = subset[int(k)]
            out.append(
                Trial(
                    index=start_index + len(out),
                    symbol=symbol,
                    session=day,
                    seed=int(child_seeds[len(out)]),
                    window=window,
                    sample=sample,
                )
            )
    return out
