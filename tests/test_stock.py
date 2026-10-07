"""Ratings describe indicator agreement only when enough evidence is available."""

import math

import pytest

from stock import Analysis, Metric, print_concise, print_verbose


def analysis(scores, *, base=None):
    return Analysis(
        "Test style",
        [Metric(f"Metric {i}", "reading", "reason", score) for i, score in enumerate(scores)],
        {"strong": "Strong evidence", "moderate": "Moderate evidence", "poor": "Poor evidence"},
        base=base,
    )


def test_one_positive_indicator_out_of_ten_is_inconclusive():
    result = analysis([1] + [None] * 9)
    assert result.rating == "inconclusive"
    assert result.indicator_score == 1
    assert result.evaluated_count == 1
    assert result.total_count == 10
    assert result.coverage == pytest.approx(0.1)


@pytest.mark.parametrize(
    "scores,rating,coverage",
    [
        ([1, 1], "inconclusive", 1.0),
        ([1, 1, 1, None, None], "strong", 0.6),
        ([1, 1, 1, None, None, None], "inconclusive", 0.5),
        ([1, 1, 0], "moderate", 1.0),
        ([1, 0, 0], "poor", 1.0),
    ],
)
def test_rating_requires_both_three_indicators_and_sixty_percent_coverage(scores, rating, coverage):
    result = analysis(scores)
    assert result.rating == rating
    assert result.coverage == pytest.approx(coverage)


def test_inherited_metrics_count_toward_both_score_and_coverage():
    base = analysis([1, None, 0])
    result = analysis([1, 1], base=base)
    assert result.evaluated_count == 4
    assert result.total_count == 5
    assert result.coverage == pytest.approx(0.8)
    assert result.indicator_score == pytest.approx(0.75)
    assert result.rating == "moderate"


@pytest.mark.parametrize("scores", [[], [None] * 3, [math.nan, math.inf, -math.inf, None]])
def test_missing_or_nonfinite_scores_are_unavailable_evidence(scores):
    result = analysis(scores)
    assert result.evaluated_count == 0
    assert result.total_count == len(scores)
    assert result.coverage == 0
    assert result.indicator_score is None
    assert result.rating == "inconclusive"
    assert result.score == 0


def test_nonfinite_scores_do_not_satisfy_the_minimum_evidence_floor():
    result = analysis([1, 1, math.nan, math.inf])
    assert result.evaluated_count == 2
    assert result.indicator_score == 1
    assert result.rating == "inconclusive"


@pytest.mark.parametrize("printer", [print_concise, print_verbose])
def test_output_labels_score_and_evaluated_coverage(printer, capsys):
    printer([analysis([1] + [None] * 9)])
    output = capsys.readouterr().out.lower()
    assert "indicator score" in output
    assert "100%" in output
    assert "1/10" in output
    assert "10%" in output
    assert "inconclusive" in output
    assert "win rate" not in output
    assert "probability" not in output


def test_verbose_output_does_not_print_nonfinite_points(capsys):
    print_verbose([analysis([math.nan, math.inf, -math.inf, None])])
    output = capsys.readouterr().out.lower()
    assert "0/4" in output
    assert "nan" not in output
    assert "inf" not in output.replace("inconclusive", "")


@pytest.mark.parametrize('invalid', [math.nan, math.inf, -math.inf])
def test_nonfinite_fundamentals_are_unavailable_not_failed_metrics(invalid):
    from types import SimpleNamespace
    import pandas as pd
    from stock import analyze_long_term_investment

    data = SimpleNamespace(info={key: invalid for key in (
        'trailingPE', 'forwardPE', 'priceToBook', 'returnOnEquity',
        'debtToEquity', 'profitMargins', 'payoutRatio', 'beta')},
        financials=pd.DataFrame())
    result = analyze_long_term_investment(data)
    assert result.evaluated_count == 0
    assert result.rating == 'inconclusive'


def _fundamentals(**info):
    from types import SimpleNamespace
    import pandas as pd

    return SimpleNamespace(info=info, financials=pd.DataFrame())


def _metric(result, label):
    return next(m for m in result.all_metrics if m.label == label)


@pytest.mark.parametrize("pe", [-5.0, 0.0])
def test_a_non_positive_pe_is_loss_making_rather_than_undervalued(pe):
    """A P/E below 15 reads as cheap only when earnings are positive. A
    loss-making company also has a P/E below 15, and scoring it as undervalued
    rewards losses."""
    from stock import analyze_long_term_investment

    metric = _metric(analyze_long_term_investment(_fundamentals(trailingPE=pe)), "P/E Valuation")
    assert metric.score == 0
    assert "loss" in metric.detail.lower()


@pytest.mark.parametrize("forward,trailing", [(-6.0, -5.0), (-6.0, 20.0), (18.0, -5.0)])
def test_growth_outlook_is_unavailable_unless_both_pe_ratios_are_positive(forward, trailing):
    """Forward below trailing P/E implies earnings growth only when both are
    positive; -6 below -5 says nothing about growth but used to score 1."""
    from stock import analyze_long_term_investment

    result = analyze_long_term_investment(_fundamentals(forwardPE=forward, trailingPE=trailing))
    assert not _metric(result, "Growth Outlook").is_evaluated


def test_a_negative_price_to_book_is_negative_equity_not_undervalued():
    """Negative book value makes P/B negative. Reading that as P/B below 1.0
    calls an insolvent balance sheet cheap."""
    from stock import analyze_long_term_investment

    metric = _metric(analyze_long_term_investment(_fundamentals(priceToBook=-2.0)), "P/B Valuation")
    assert metric.score == 0
    assert "negative equity" in metric.detail.lower()


def test_a_negative_debt_to_equity_is_negative_equity_not_low_leverage():
    """Debt over negative equity is negative. Below 100 is not low leverage when
    the denominator has gone below zero."""
    from stock import analyze_long_term_investment

    metric = _metric(analyze_long_term_investment(_fundamentals(debtToEquity=-50.0)), "Debt-to-Equity")
    assert metric.score == 0
    assert "negative equity" in metric.detail.lower()


def test_a_negative_payout_ratio_is_unavailable_rather_than_sustainable():
    """A negative payout ratio means a dividend paid out of losses; it has no
    sustainability reading and must not count as a safe dividend."""
    from stock import analyze_long_term_investment

    metric = _metric(analyze_long_term_investment(_fundamentals(payoutRatio=-0.3)), "Dividend Payout")
    assert not metric.is_evaluated


@pytest.mark.parametrize(
    "closes,expected",
    [
        ([10.0] * 30, 50.0),
        ([float(i) for i in range(1, 31)], 100.0),
        ([float(i) for i in range(30, 0, -1)], 0.0),
    ],
)
def test_rsi_reads_flat_as_neutral_and_one_way_moves_as_the_extremes(closes, expected):
    """With no losses RSI divides by zero. A flat series has no gains either,
    and calling it 100 (maximally overbought) inverts its meaning."""
    import pandas as pd
    from stock import _rsi

    assert _rsi(pd.Series(closes)) == pytest.approx(expected)


@pytest.mark.parametrize("closes", [[10.0] * 5, [10.0] * 29 + [math.inf], []])
def test_rsi_is_unavailable_for_short_or_nonfinite_histories(closes):
    """A window without fourteen finite changes has no RSI. Returning a number
    there would score momentum the data never showed."""
    import pandas as pd
    from stock import _rsi

    assert _rsi(pd.Series(closes, dtype=float)) is None


class _PriceData:
    """Offline stand-in for StockData with a fixed close series."""

    def __init__(self, closes, earnings=None):
        import pandas as pd

        index = pd.date_range("2026-01-01", periods=len(closes), freq="B")
        self._close = pd.Series(closes, index=index, dtype=float)
        self._earnings = earnings
        self.info = {}

    def close(self, period):
        from stock import _finite

        return _finite(self._close)

    def earnings_date(self):
        return self._earnings


def test_a_missing_earnings_date_is_unavailable_rather_than_a_failed_catalyst():
    """No scheduled date usually means the provider has none, not that the
    company has no catalyst; scoring it 0 penalised missing data."""
    from stock import analyze_short_term_trading

    result = analyze_short_term_trading(_PriceData([10.0 + i % 3 for i in range(60)]))
    assert not _metric(result, "Catalyst (Earnings)").is_evaluated


def test_nonfinite_closes_never_reach_the_moving_average_comparison():
    """An infinite close would make every SMA comparison meaningless while
    still producing a score; such prints are dropped before indicators run."""
    from stock import analyze_short_term_trading

    closes = [10.0 + i * 0.1 for i in range(60)]
    closes[-1] = math.inf
    result = analyze_short_term_trading(_PriceData(closes))
    metric = _metric(result, "Moving Average Trend")
    assert "inf" not in metric.value.lower()
    assert "nan" not in metric.value.lower()
