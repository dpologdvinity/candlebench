"""What a run was computed from, recorded with the run.

Two reports that disagree are only comparable if each says which code, which
settings and which bars produced it. The same seed over a refreshed cache, or
the same cache under a changed engine, draws the same trials and still reports
different numbers. The config alone cannot tell those apart, so every report
carries this manifest beside it.

The data fingerprint hashes the cache files the run actually read: the sampled
symbols at the enabled intervals, plus the quote table when costs came from
it. Hashing contents rather than modification times means a re-fetch that
returned identical bars keeps the same fingerprint, and one that changed a
single bar does not.
"""

from __future__ import annotations

import hashlib
import json
import platform
import subprocess
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path

PACKAGES = ("numpy", "pandas", "pyarrow")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _git_revision() -> dict | None:
    """The checkout's commit and whether it had uncommitted changes, or None outside git."""
    root = Path(__file__).resolve().parent.parent
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True,
            timeout=5, check=True,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no", "--", "candlebench"],
            cwd=root, capture_output=True, text=True, timeout=5, check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return {"commit": commit, "dirty": bool(status.strip())} if commit else None


def _version(package: str) -> str | None:
    try:
        return metadata.version(package)
    except metadata.PackageNotFoundError:
        return None


def config_hash(config_payload: dict) -> str:
    """A stable hash of the effective settings, independent of key order."""
    canonical = json.dumps(config_payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def data_fingerprint(files: list[Path]) -> dict:
    """One hash over the named files' contents, and how many were missing.

    A file absent at fingerprint time is counted rather than skipped, since a
    fingerprint silently computed over fewer files would look like a match.
    """
    digest = hashlib.sha256()
    present = missing = 0
    for path in sorted(set(files)):
        if path.exists():
            digest.update(f"{path.parent.name}/{path.name}:{_sha256_file(path)}\n".encode())
            present += 1
        else:
            missing += 1
    return {"sha256": digest.hexdigest(), "files": present, "missing": missing}


def manifest(config, config_payload: dict, trials) -> dict:
    """The provenance block stored with a run report."""
    from candlebench import __version__, bars

    symbols = sorted({t.symbol for t in trials})
    files = [
        bars.cache_file(config.cache_path, symbol, interval)
        for symbol in symbols for interval in config.run.intervals
    ]
    if config.costs.model == "quoted":
        files.append(Path(config.costs.quote_table))
    sessions = sorted(t.session for t in trials)
    return {
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "candlebench": __version__,
        "git": _git_revision(),
        "python": platform.python_version(),
        "packages": {name: _version(name) for name in PACKAGES},
        "config_sha256": config_hash(config_payload),
        "data": {
            "source": config.run.source,
            "first_session": sessions[0].isoformat() if sessions else None,
            "last_session": sessions[-1].isoformat() if sessions else None,
            **data_fingerprint(files),
        },
    }
