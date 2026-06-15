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
import make_font_fixtures  # noqa: E402


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


@pytest.fixture(scope="session")
def font_fixtures(tmp_path_factory) -> dict[str, Path]:
    """Generate the font-forensics fixtures once; return a name -> path map."""
    dest = tmp_path_factory.mktemp("font_fixtures")
    return make_font_fixtures.write_fixtures(dest)


@pytest.fixture(scope="session")
def font_forged_pdf(font_fixtures) -> Path:
    """Single-revision PDF whose amount was re-embedded in a foreign subset."""
    return font_fixtures["font_edited_subset"]


@pytest.fixture(scope="session")
def font_multifont_pdf(font_fixtures) -> Path:
    """Genuine multi-font invoice (bold headers) — the known-negative."""
    return font_fixtures["font_multifont_invoice"]


# --------------------------------------------------------------------------- #
# Real-world sample PDFs (untracked, in ``test_pdf's/``). Acceptance cases for
# the two bug fixes; skip gracefully when the samples are not present.
# --------------------------------------------------------------------------- #

_TEST_PDFS = Path(__file__).resolve().parent.parent / "test_pdf's"


@pytest.fixture(scope="session")
def acrobat_pdf() -> Path:
    """Acrobat-edited file: amount 1871.23 -> 18071.23 (one inserted '0')."""
    p = _TEST_PDFS / "Acrobat_Demo_File.pdf"
    if not p.exists():
        pytest.skip(f"sample not available: {p}")
    return p


@pytest.fixture(scope="session")
def microsoft_clean_pdf() -> Path:
    """Clean multi-subset-font invoice (known-negative for both stages)."""
    p = _TEST_PDFS / "Microsoft-Sample-Invoice.pdf"
    if not p.exists():
        pytest.skip(f"sample not available: {p}")
    return p
