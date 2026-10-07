"""Run provenance: which code, settings and bars a report came from."""

from __future__ import annotations

from dataclasses import replace

from candlebench import leaderboard, provenance, runner
from tests.test_end_to_end import config, write_cache


def test_every_report_records_code_settings_and_data(tmp_path):
    """Two reports with the same seed can still differ; the manifest says why."""
    write_cache(tmp_path)
    cfg = config(tmp_path)
    report = leaderboard.payload(runner.run(cfg), cfg)

    manifest = report["provenance"]
    assert manifest["config_sha256"] == provenance.config_hash(report["config"])
    assert set(manifest["packages"]) == {"numpy", "pandas", "pyarrow"}
    assert manifest["data"]["files"] > 0
    assert manifest["data"]["missing"] == 0
    assert manifest["data"]["first_session"] <= manifest["data"]["last_session"]


def test_the_config_hash_ignores_key_order_but_not_values():
    a = {"run": {"trials": 10, "seed": 1}}
    assert provenance.config_hash(a) == provenance.config_hash({"run": {"seed": 1, "trials": 10}})
    assert provenance.config_hash(a) != provenance.config_hash({"run": {"trials": 11, "seed": 1}})


def test_the_data_fingerprint_follows_contents_and_counts_missing_files(tmp_path):
    """A changed bar must change the fingerprint; a missing file must not hide."""
    one, two = tmp_path / "a.parquet", tmp_path / "b.parquet"
    one.write_bytes(b"bars")
    before = provenance.data_fingerprint([one])
    one.write_bytes(b"bars, refreshed")
    after = provenance.data_fingerprint([one])
    assert before["sha256"] != after["sha256"]
    assert provenance.data_fingerprint([one, two])["missing"] == 1


def test_a_run_outside_a_git_checkout_records_no_commit(monkeypatch):
    def no_git(*_args, **_kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(provenance.subprocess, "run", no_git)
    assert provenance._git_revision() is None
