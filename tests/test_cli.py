"""Command-line argument rules."""

from __future__ import annotations

import pytest

from candlebench import cli


@pytest.mark.parametrize("count", ["0", "-2"])
def test_a_session_count_below_one_is_refused(count):
    """`--sessions 0` sliced `[-0:]` and sampled every cached session instead of none."""
    with pytest.raises(SystemExit):
        cli.parse_args(["quotes", "--sessions", count])


def test_the_quote_command_paces_requests_at_the_provider_interval(monkeypatch, tmp_path):
    """A fixed 0.05s sleep replaced both the page interval and the 429 backoff."""
    from candlebench import bars, quotes

    passed = {}
    monkeypatch.setattr(bars, "available_sessions", lambda *a, **k: {"AAPL": [__import__("datetime").date(2026, 9, 15)]})
    monkeypatch.setattr(quotes, "build_table", lambda symbols, sessions, **kw: passed.update(kw) or {})
    monkeypatch.setattr(quotes, "write_table", lambda table, path: tmp_path / "t.json")
    cli.main(["quotes", "--sessions", "1"])
    assert passed.get("sleep") is None
