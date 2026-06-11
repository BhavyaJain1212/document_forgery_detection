"""Shared test fixtures.

Builds the known-positive / known-negative PDFs once per session into a tmp
directory using the canonical generator (``scripts/make_fixtures.py``), so the
end-to-end tests exercise exactly the artifacts the deliverable ships — without
depending on the git-ignored ``tests/fixtures/`` checkout.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# ``scripts/`` is not an importable package; add it to the path so tests can use
# the same generator the CLI/users run.
_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import make_fixtures  # noqa: E402


@pytest.fixture(scope="session")
def fixtures(tmp_path_factory) -> dict[str, Path]:
    """Generate the fixtures once and return a name -> path map."""
    dest = tmp_path_factory.mktemp("fixtures")
    return make_fixtures.write_fixtures(dest)


@pytest.fixture(scope="session")
def clean_pdf(fixtures) -> Path:
    """Path to the single-revision known-negative PDF."""
    return fixtures["clean"]


@pytest.fixture(scope="session")
def forged_pdf(fixtures) -> Path:
    """Path to the incremental-update known-positive PDF."""
    return fixtures["edited_incremental"]
