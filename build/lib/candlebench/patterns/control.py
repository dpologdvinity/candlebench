"""Random-entry null baselines.

These are the reference line of the whole experiment. A real pattern ranked
above its matched control has shown an edge on this data; one ranked at or below
it has not, whatever its win rate looks like in isolation.

The controls fire at a rate matched to the real patterns on the same bars, so
the comparison is not confounded by sample size, and they flow through the
identical engine, metrics and ranking path as every real pattern.
"""

from __future__ import annotations

import numpy as np

from candlebench.patterns import register_control

CONTROLS = {"bull": "random_long", "bear": "random_short"}

register_control("random_long", bias="bull")
register_control("random_short", bias="bear")


def control_mask(
    n_bars: int, rate: float, first_valid: int, rng: np.random.Generator
) -> np.ndarray:
    """A mask firing at `rate` on randomly chosen eligible bars.

    `first_valid` mirrors the history cutoff the registry applies to real
    patterns, so a control cannot draw entries from bars where a real pattern
    is forbidden to fire.
    """
    mask = np.zeros(n_bars, dtype=bool)
    eligible = np.arange(first_valid, n_bars)
    if len(eligible) == 0 or rate <= 0:
        return mask

    count = min(len(eligible), int(round(rate * len(eligible))))
    if count == 0:
        return mask

    mask[rng.choice(eligible, size=count, replace=False)] = True
    return mask


def matched_rate(signal_rates: list[float]) -> float:
    """The firing rate to give the controls on one bar array.

    The highest rate any real pattern achieved, not the median or the mean.
    Because the control picks its bars uniformly at random, its rate changes
    only how precisely the no-skill baseline is estimated, never where that
    baseline sits. Sampling it at least as densely as the best-populated
    pattern keeps its confidence interval tighter than the intervals it is
    there to judge.

    A median would be dragged toward zero by the many patterns that are rare
    intraday, leaving the control with too few trades to clear the reporting
    minimum, which is exactly when the comparison is needed most.
    """
    if not signal_rates:
        return 0.0
    return float(np.max(signal_rates))
