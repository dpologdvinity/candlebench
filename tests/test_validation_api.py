"""Evidence stays explicit across reports, saved trades and HTTP queries."""
import csv
import json
from dataclasses import replace

import pytest

from candlebench import leaderboard, trades
from candlebench.web.server import query_filters
from tests.test_metrics import trade
from tests.test_leaderboard import stat, _result
from tests.test_web import base_config, client


def test_sample_filter_rejects_unknown_values():
    with pytest.raises(ValueError, match='sample'):
        query_filters({'sample': ['future']})


def test_trade_queries_keep_validation_separate():
    frame = trades.to_frame([trade(1), replace(trade(-1), sample='validation')])
    assert trades.query(frame, sample='discovery').net_r.tolist() == [1]
    assert trades.query(frame, sample='validation').net_r.tolist() == [-1]


def test_old_parquet_remains_readable_and_exploratory(tmp_path):
    path = tmp_path / 'old.parquet'
    trades.to_frame([trade(1)]).drop(columns='sample').to_parquet(path)
    assert trades.read(path)['sample'].tolist() == ['discovery']


def test_api_cache_reports_selected_source_and_rejects_unknown_source(client):
    result = client('/api/cache?source=alpaca')
    assert '1s' in result['intervals']
    assert 'source' in client('/api/cache?source=unknown', expect=400)['error']


def test_payload_is_strict_json_and_preserves_unavailable():
    result, cfg = _result([stat('hammer', profit_factor=float('inf'),
                               p_delta_adjusted=None)])
    payload = leaderboard.payload(result, cfg)
    encoded = json.dumps(payload, allow_nan=False)
    assert json.loads(encoded)['stats'][0]['profit_factor'] == 'Infinity'
    assert payload['stats'][0]['p_delta_adjusted'] is None
    assert payload['inference']['cluster'] == 'market_date'
    assert 'validation' in payload


def test_csv_flattens_validation_evidence(tmp_path):
    validation = stat('hammer', verdict='NEGATIVE', expectancy_r=-0.3)
    result, cfg = _result([stat('hammer', verdict='NOISE', discovery_verdict='EDGE',
                               validation=validation, baseline_ci_low=0.1,
                               p_delta_adjusted=0.01)])
    path = tmp_path / 'report.csv'
    leaderboard.write_csv(result, path)
    row = next(csv.DictReader(path.open()))
    assert row['discovery_verdict'] == 'EDGE'
    assert row['validation_verdict'] == 'NEGATIVE'
    assert float(row['validation_expectancy_r']) == -0.3
    assert float(row['baseline_ci_low']) == 0.1


def test_terminal_report_explains_adjusted_tests_and_holdout():
    result, cfg = _result([stat('hammer', discovery_verdict='EDGE', validation=None,
                               verdict='NOISE')])
    result.validation = {'enabled': False, 'evaluated': False, 'cutoff': None,
                         'candidates': [{'pattern': 'hammer', 'interval': '1m'}],
                         'discovery_trials': 5, 'validation_trials': 0}
    report = leaderboard.render(result, cfg)
    assert 'Holm' in report
    assert 'validation' in report.lower()
    assert 'paired' in report.lower()
    assert 'exploratory' in report.lower()
