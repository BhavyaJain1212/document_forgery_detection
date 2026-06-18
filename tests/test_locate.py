"""Tests for added-text localization (revision_recovery/extract/locate.py).

Covers the reflow-safe text-multiset matching, the graceful-degradation /
diagnostic-note contract, and rotation-aware coordinates (validated directly
because pdfminer — which drives the upstream text diff — does not apply /Rotate,
while pdfplumber, which drives the geometry, does).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SCRIPTS = str(Path(__file__).resolve().parent.parent / "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import make_localization_fixtures as F  # noqa: E402

from pdf_forgery.revision_recovery.analyze import analyze_bytes  # noqa: E402
from pdf_forgery.revision_recovery.config import Config  # noqa: E402
from pdf_forgery.revision_recovery.detect import detect  # noqa: E402
from pdf_forgery.revision_recovery.extract.locate import (  # noqa: E402
    _PageWords,
    _locate_one,
    locate_findings,
)
from pdf_forgery.revision_recovery.models import (  # noqa: E402
    BoxPt,
    Finding,
    TokenDiff,
)
from pdf_forgery.revision_recovery.reconstruct import reconstruct  # noqa: E402


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #

def _recon(forged: bytes):
    return reconstruct(forged, detect(forged))


def _text_finding(after: str, *, page: int = 0, from_rev: int = 0, to_rev: int = 1) -> Finding:
    """A minimal text-bearing finding with a single inserted/changed token."""
    return Finding(
        from_revision=from_rev,
        to_revision=to_rev,
        page_index=page,
        object_ids=("4 0",),
        object_classes=(),
        token_changes=(TokenDiff(before="", after=after),),
        is_high_value=False,
        high_value_kind=None,
        summary="test",
    )


def _page(words: list[tuple[str, float, float, float, float]], *, w=612.0, h=792.0, rot=0):
    """Build a _PageWords from (text, x0, top, x1, bottom) tuples."""
    boxes = tuple(BoxPt(x0=x0, top=top, x1=x1, bottom=bottom) for _, x0, top, x1, bottom in words)
    texts = tuple(t for t, *_ in words)
    return _PageWords(boxes=boxes, texts=texts, width_pt=w, height_pt=h, rotation=rot)


def _pages_for(mapping: dict[int, list[_PageWords]]):
    return lambda idx: mapping.get(idx, [])


# --------------------------------------------------------------------------- #
# Multiset matching (synthetic, no PDF parsing)                                #
# --------------------------------------------------------------------------- #

def test_added_token_boxed_when_absent_in_prior():
    cur = _page([("Total", 72, 100, 110, 116), ("50,000", 120, 100, 170, 116)])
    prior = _page([("Total", 72, 100, 110, 116)])
    notes: list[str] = []
    loc = _locate_one(
        _text_finding("50,000"), _pages_for({0: [prior], 1: [cur]}), Config(), notes
    )
    assert loc is not None
    assert len(loc.boxes) == 1
    assert loc.boxes[0].x0 == 120 and loc.boxes[0].x1 == 170


def test_reflow_safe_unchanged_duplicate_not_boxed():
    # "500" appears once in prior and once in current but MOVED far (reflow).
    # Multiset count is unchanged -> it must NOT be boxed even though position differs.
    prior = _page([("500", 72, 100, 100, 116)])
    cur = _page([("500", 72, 300, 100, 316)])  # same text, moved 200pt down
    notes: list[str] = []
    loc = _locate_one(_text_finding("500"), _pages_for({0: [prior], 1: [cur]}), Config(), notes)
    assert loc is None  # count(current)=1, count(prior)=1 -> n_new=0 -> nothing added


def test_one_new_among_duplicates_boxes_only_the_new_count():
    # prior has one "900"; current has two -> exactly one is new.
    prior = _page([("900", 72, 100, 110, 116)])
    cur = _page([("900", 72, 100, 110, 116), ("900", 72, 200, 110, 216)])
    notes: list[str] = []
    loc = _locate_one(_text_finding("900"), _pages_for({0: [prior], 1: [cur]}), Config(), notes)
    assert loc is not None
    assert len(loc.boxes) == 1  # n_new = 2 - 1 = 1


def test_object_only_finding_not_localized():
    f = Finding(
        from_revision=0, to_revision=1, page_index=0,
        object_ids=("5 0",), object_classes=(), token_changes=(),
        is_high_value=False, high_value_kind=None, summary="overlay",
    )
    notes: list[str] = []
    assert _locate_one(f, _pages_for({0: [_page([])], 1: [_page([])]}), Config(), notes) is None


def test_removed_only_finding_not_localized():
    f = _text_finding("")  # after empty -> removed-only
    f = Finding(
        from_revision=0, to_revision=1, page_index=0, object_ids=("4 0",),
        object_classes=(), token_changes=(TokenDiff(before="gone", after=""),),
        is_high_value=False, high_value_kind=None, summary="removed",
    )
    notes: list[str] = []
    assert _locate_one(f, _pages_for({}), Config(), notes) is None


def test_current_extraction_failure_emits_note_and_no_box():
    notes: list[str] = []
    loc = _locate_one(_text_finding("50,000"), _pages_for({0: [_page([])], 1: []}), Config(), notes)
    assert loc is None
    assert any("word geometry unavailable for revision 1" in n for n in notes)


def test_prior_extraction_failure_degrades_with_note():
    # Prior words unavailable -> duplicate-disambiguation skipped, but the token
    # is still boxed from the current revision (plain-A fallback) + a note.
    cur = _page([("50,000", 120, 100, 170, 116)])
    notes: list[str] = []
    loc = _locate_one(_text_finding("50,000"), _pages_for({0: [], 1: [cur]}), Config(), notes)
    assert loc is not None and len(loc.boxes) == 1
    assert any("duplicate-disambiguation skipped" in n for n in notes)


def test_disabled_localization_passes_findings_through():
    cfg = Config(enable_localization=False)
    f = _text_finding("50,000")
    out = locate_findings([f], _Recon([]), cfg, [])
    assert out[0].location is None


class _Recon:
    def __init__(self, revisions):
        self.revisions = revisions


# --------------------------------------------------------------------------- #
# Integration over real reconstructed revisions                                #
# --------------------------------------------------------------------------- #

def test_reflow_fixture_boxes_only_inserted_line_not_reflowed_duplicates():
    clean, forged = F.reflow_pair()
    report = analyze_bytes(forged, "reflow.pdf")
    located = [f for f in report.findings if f.location is not None]
    assert len(located) == 1
    f = located[0]
    assert f.after_text == "Adjusted 777"

    # The located boxes are all on the inserted TOP line; the reflowed "500"
    # lines (further down the page = larger `top`) are never boxed.
    boxed_tops = [b.top for b in f.location.boxes]
    assert len(boxed_tops) == 2
    top_line = min(boxed_tops)
    assert all(abs(t - top_line) < 1.0 for t in boxed_tops)

    # Locate the "500" word tops in the current revision and assert none coincide.
    from pdf_forgery.revision_recovery.extract.locate import _extract_pages
    pages = _extract_pages(forged, Config())
    five_hundred_tops = [
        b.top for b, t in zip(pages[0].boxes, pages[0].texts) if t == "500"
    ]
    assert five_hundred_tops  # sanity: the duplicates exist
    for bt in boxed_tops:
        assert all(abs(bt - ft) > 1.0 for ft in five_hundred_tops)


def test_amount_fixture_boxes_changed_amount():
    clean, forged = F.amount_pair()
    report = analyze_bytes(forged, "amount.pdf")
    located = [f for f in report.findings if f.location is not None]
    assert len(located) == 1
    assert "50,000" in located[0].after_text
    assert len(located[0].location.boxes) >= 1


def test_rotated_page_pdfplumber_visual_coords_and_normalization():
    # pdfminer garbles text on /Rotate 90 so the live text diff can't match;
    # validate the localizer's geometry directly with a hand-built finding.
    clean, forged = F.rotated_pair(90)
    recon = _recon(forged)
    assert recon.revision_count == 2
    notes: list[str] = []
    out = locate_findings([_text_finding("50,000")], recon, Config(), notes)
    loc = out[0].location
    assert loc is not None
    # pdfplumber applies rotation: a 612x792 page becomes 792x612 visual.
    assert (loc.page_width_pt, loc.page_height_pt) == (792.0, 612.0)
    assert loc.page_rotation == 90
    # Box lies within the visual page and normalizes into [0, 1].
    b = loc.boxes[0]
    assert 0 <= b.x0 < b.x1 <= loc.page_width_pt
    assert 0 <= b.top < b.bottom <= loc.page_height_pt


def test_prior_revision_pdfplumber_parse_path_is_exercised():
    # Regression guard for the previously-unexercised "pdfplumber on the PRIOR
    # reconstructed revision" path: it must parse and yield words.
    from pdf_forgery.revision_recovery.extract.locate import _extract_pages
    clean, forged = F.amount_pair()
    recon = _recon(forged)
    prior_bytes = recon.revisions[0].data
    pages = _extract_pages(prior_bytes, Config())
    assert pages and any("5,000" in t for t in pages[0].texts)
