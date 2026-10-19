"""Today's trade engine against a record of the one before the C++ walk.

`test_engine_core` compares the native walk with the Python one, but both are
the current code, so a change that moved both the same way would pass it.
`tests/data/engine_golden.json` was made by `scripts/engine_golden.py` from
revision 8e9a067, the pure-Python engine every published result came from.
Both of today's paths must reproduce it exactly: if one trade's R multiple
moves by an ulp, the hash moves.

The record's signals come from today's pattern detectors and context code,
so a deliberate change to a detector, or to the trade rules, changes the record
too. Regenerate it from the new revision and say in the commit why the trades
changed; the record then pins that revision rather than 8e9a067.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from candlebench import engine

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import engine_golden  # noqa: E402  pylint: disable=wrong-import-position,wrong-import-order,import-error

RECORD = json.loads((Path(__file__).parent / "data" / "engine_golden.json").read_text())
CASES = dict(engine_golden.cases())


def test_the_record_covers_every_case_the_script_defines():
    assert set(RECORD["cases"]) == set(CASES)


@pytest.mark.parametrize("path", ["native", "python"])
@pytest.mark.parametrize("name", sorted(CASES))
def test_the_engine_reproduces_the_recorded_trades(monkeypatch, name, path):
    if path == "native" and engine._enginecore is None:  # pylint: disable=protected-access
        pytest.skip("candlebench._enginecore is not built")
    if path == "python":
        monkeypatch.setattr(engine, "_enginecore", None)
    got = engine_golden.digest(engine_golden.run_case(CASES[name]))
    assert got == RECORD["cases"][name], f"{name} differs from revision {RECORD['revision']}"
