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
import make_invoice_fixtures  # noqa: E402


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


@pytest.fixture(scope="session")
def invoice_fixtures(tmp_path_factory) -> dict[str, Path]:
    """Generate the invoice-arithmetic fixtures once; return a name -> path map."""
    dest = tmp_path_factory.mktemp("invoice_fixtures")
    return make_invoice_fixtures.write_fixtures(dest)


@pytest.fixture(scope="session")
def invoice_clean_pdf(invoice_fixtures) -> Path:
    """Single-revision invoice whose relationships all reconcile (known-negative)."""
    return invoice_fixtures["invoice_clean"]


@pytest.fixture(scope="session")
def invoice_tamper_pdf(invoice_fixtures) -> Path:
    """Single-revision invoice with a line amount inflated; totals untouched
    (known-positive; convergence -> HIGH)."""
    return invoice_fixtures["invoice_convergence_tamper"]


@pytest.fixture(scope="session")
def invoice_two_clean_pdf(invoice_fixtures) -> Path:
    return invoice_fixtures["invoice_two_clean_bundle"]


@pytest.fixture(scope="session")
def invoice_repeated_header_pdf(invoice_fixtures) -> Path:
    return invoice_fixtures["invoice_repeated_header"]


@pytest.fixture(scope="session")
def invoice_headerless_pdf(invoice_fixtures) -> Path:
    return invoice_fixtures["invoice_headerless_continuation"]


@pytest.fixture(scope="session")
def invoice_ambiguous_pdf(invoice_fixtures) -> Path:
    return invoice_fixtures["invoice_ambiguous_multipage"]


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
    """Clean multi-subset-font invoice (known-negative for font/revision stages).

    NOTE: clean only w.r.t. fonts and revisions. It is NOT a clean arithmetic /
    provenance baseline — it carries a genuine line-item break (9*41.61 !=
    37004.49) and a Sejda producer + ModDate>CreationDate (see
    ``test_invoice_end_to_end`` / ``test_provenance``)."""
    p = _TEST_PDFS / "Microsoft-Sample-Invoice.pdf"
    if not p.exists():
        pytest.skip(f"sample not available: {p}")
    return p


@pytest.fixture(scope="session")
def microsoft_hybrid_pdf() -> Path:
    """Clean hybrid-reference invoice (cross-reference streams + a final 184-byte
    compatibility xref append). The compatibility append is a second %%EOF but
    authors zero objects — a known false-positive trap for revision recovery."""
    p = _TEST_PDFS / "Microsoft-Sample-Invoice_clear.pdf"
    if not p.exists():
        pytest.skip(f"sample not available: {p}")
    return p


@pytest.fixture(scope="session")
def page4_microsoft_pdf() -> Path:
    """Clean page-4 extract containing the uniformly-rendered ABN identifier."""
    p = _TEST_PDFS / "page4_Microsoft-Sample-Invoice.pdf"
    if not p.exists():
        pytest.skip(f"sample not available: {p}")
    return p


@pytest.fixture(scope="session")
def sejda_tampered_pdf() -> Path:
    """The Sejda-tampered invoice: line amount 249.69 -> 24019.69, qty/rate left
    untouched (the clean-re-render attack)."""
    p = _TEST_PDFS / "tampered.pdf"
    if not p.exists():
        pytest.skip(f"sample not available: {p}")
    return p


@pytest.fixture(scope="session")
def pristine_invoice_pdf() -> Path:
    """A REAL, untouched invoice for the precision baseline (LOW on both stages).

    The Microsoft sample shipped here is itself Sejda-processed and arithmetically
    broken, so it cannot serve this role. Drop a genuine, unmodified invoice at
    ``test_pdf's/pristine-invoice.pdf`` to activate the real-world precision
    assertions; the test skips until one is provided."""
    p = _TEST_PDFS / "pristine-invoice.pdf"
    if not p.exists():
        pytest.skip(
            "no pristine real invoice provided at test_pdf's/pristine-invoice.pdf "
            "(needed to prove precision on a genuinely untouched bill)"
        )
    return p
