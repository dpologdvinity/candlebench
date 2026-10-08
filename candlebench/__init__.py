"""candlebench - measure which candlestick patterns have an intraday edge.

The package answers one narrow question: given a pattern signal on an intraday
chart, and a mechanical risk-managed trade taken on the next bar, does the
resulting distribution of returns differ from the distribution produced by
entering at random? Patterns that cannot beat their random-entry control are
reported as noise rather than ranked as though the difference were real.
"""

__version__ = "0.4.2"
