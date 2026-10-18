"""A 20-bar breakout, as an example of testing a signal of your own.

    candlebench test examples/breakout_20.py --synthetic   # offline, no edge can exist
    candlebench test examples/breakout_20.py               # on the cached market bars

The signal fires on a bar that closes above the highest high of the 20 bars
before it, and the trade enters at the next bar's open. As for every pattern,
the stop sits just beyond the extreme of the `bars_required` bars ending at the
signal, here the lowest low of the last 21, and the target is the configured
multiple of that risk. `bias` sets the direction: "bull" buys, "bear" sells.

`geom` holds the session's bars as numpy arrays (`open`, `high`, `low`,
`close`, `volume`, plus derived `body`, `rng`, `upper_shadow`, `lower_shadow`,
`trend`), and the detector returns one boolean per bar, True where the signal
fires. Only the bars up to and including that one are known when it fires;
reading a later bar would let the signal see the future.
"""

from candlebench.patterns import pattern
from candlebench.patterns.context import lag, rolling_max


@pattern("breakout_20", bias="bull", bars_required=21)
def breakout_20(geom, thresholds):  # pylint: disable=unused-argument
    return geom.close > lag(rolling_max(geom.high, 20))
