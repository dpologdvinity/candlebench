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
