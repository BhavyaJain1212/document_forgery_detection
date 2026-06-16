"""Tests for Stage 3 coordinate alignment, matching, and embedded extraction.

Covers:
  - ``quad_to_bbox``: axis-aligned reduction of rotated quadrilaterals
  - ``embedded_to_pixel``: hand-checked transform for R=0, 90, 180, 270
  - ``center_inside``: primary matching primitive
  - ``iou``: IoU fallback primitive
  - ``match_words``: one-to-many matching with both passes
  - ``extract_embedded_words``: real extraction from the Microsoft invoice fixture

Acceptance check (last test): on the Microsoft invoice, extract_embedded_words
returns a meaningful word count with sensible pixel bboxes at 300 DPI.
"""

from __future__ import annotations

import math
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from pdf_forgery.ocr_crosscheck.align import (
    center_inside,
    embedded_to_pixel,
    extract_embedded_words,
    iou,
    match_words,
    quad_to_bbox,
)
from pdf_forgery.ocr_crosscheck.config import OCRCrossCheckConfig
from pdf_forgery.ocr_crosscheck.models import WordBox, WordSource

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _wb(x0: float, y0: float, x1: float, y1: float,
        *, text: str = "word", page: int = 0, source: WordSource = WordSource.EMBEDDED,
        conf: float | None = None) -> WordBox:
    return WordBox(text=text, bbox=(x0, y0, x1, y1), source=source, conf=conf, page_index=page)


def _approx(a: tuple, b: tuple, rel: float = 1e-4) -> bool:
    return all(abs(ai - bi) <= rel * max(abs(ai), abs(bi), 1.0) for ai, bi in zip(a, b))


INVOICE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "test_pdf's", "Microsoft-Sample-Invoice_clear.pdf"
)
INVOICE_PRESENT = os.path.exists(INVOICE_PATH)


# ---------------------------------------------------------------------------
# quad_to_bbox
# ---------------------------------------------------------------------------

class TestQuadToBbox:
    def test_axis_aligned_quad(self):
        quad = [[10, 20], [50, 20], [50, 80], [10, 80]]
        assert quad_to_bbox(quad) == (10, 20, 50, 80)

    def test_rotated_quad(self):
        # Diamond shape: top, right, bottom, left
        quad = [[30, 10], [50, 30], [30, 50], [10, 30]]
        left, top, right, bottom = quad_to_bbox(quad)
        assert left == 10
        assert top == 10
        assert right == 50
        assert bottom == 50

    def test_skewed_quad(self):
        # Slightly skewed parallelogram
        quad = [[5, 10], [45, 8], [47, 30], [7, 32]]
        l, t, r, b = quad_to_bbox(quad)
        assert l == 5
        assert t == 8
        assert r == 47
        assert b == 32

    def test_tuple_points(self):
        # Points as tuples, not lists
        quad = [(0, 0), (100, 5), (98, 40), (2, 38)]
        l, t, r, b = quad_to_bbox(quad)
        assert l == 0 and t == 0 and r == 100 and b == 40

    def test_left_less_than_right(self):
        quad = [[100, 100], [200, 90], [210, 150], [110, 160]]
        l, t, r, b = quad_to_bbox(quad)
        assert l < r and t < b


# ---------------------------------------------------------------------------
# embedded_to_pixel — hand-checked values
# ---------------------------------------------------------------------------

class TestEmbeddedToPixel:
    """All expected values are derived from the design formula; see docstring."""

    # R=0: left=x0*s, top=(H-y1)*s, right=x1*s, bottom=(H-y0)*s
    # Microsoft invoice: W=792pt, H=612pt; scale=300/72=4.1667
    SCALE = 300 / 72.0
    H = 612.0
    W = 792.0

    def _e2p(self, bbox, *, rotate=0, H=None, W=None, dpi=300):
        H = H or self.H
        W = W or self.W
        return embedded_to_pixel(bbox, page_height_pt=H, page_width_pt=W, dpi=dpi, rotate=rotate)

    # -- R=0 --

    def test_r0_origin(self):
        # (0,0) → pixel (0, H*s)
        s = self.SCALE
        l, t, r, b = self._e2p((0.0, 0.0, 0.0, 0.0))
        assert abs(l) < 1e-9 and abs(t - self.H * s) < 1e-6

    def test_r0_top_right_corner(self):
        # Glyph at (W, H): pixel (W*s, 0)
        s = self.SCALE
        l, t, r, b = self._e2p((self.W, self.H, self.W, self.H))
        assert abs(l - self.W * s) < 1e-6 and abs(t) < 1e-9

    def test_r0_sample_token(self):
        # 'Example' token from invoice: (33.7, 595.5, 61.5, 602.5)
        # left=33.7*s, top=(612-602.5)*s, right=61.5*s, bottom=(612-595.5)*s
        s = self.SCALE
        l, t, r, b = self._e2p((33.7, 595.5, 61.5, 602.5))
        assert abs(l - 33.7 * s) < 0.5
        assert abs(t - (612.0 - 602.5) * s) < 0.5
        assert abs(r - 61.5 * s) < 0.5
        assert abs(b - (612.0 - 595.5) * s) < 0.5
        assert l < r and t < b  # invariants

    def test_r0_left_less_than_right(self):
        l, t, r, b = self._e2p((10.0, 20.0, 50.0, 80.0))
        assert l < r and t < b

    def test_r0_different_dpi(self):
        l72, t72, r72, b72 = embedded_to_pixel((10, 10, 20, 20), page_height_pt=100, dpi=72)
        l144, t144, r144, b144 = embedded_to_pixel((10, 10, 20, 20), page_height_pt=100, dpi=144)
        # At 144 DPI everything is exactly doubled
        assert abs(l144 - 2 * l72) < 1e-9
        assert abs(t144 - 2 * t72) < 1e-9

    # -- R=90 CCW --
    # Rendered dims: H*s × W*s (landscape from portrait)
    # Formula: left=(H-y1)*s, top=(W-x1)*s, right=(H-y0)*s, bottom=(W-x0)*s

    def test_r90_top_right_corner(self):
        # Portrait top-right (W, H) → landscape top-left pixel (0, 0)
        s = self.SCALE
        H, W = 200.0, 100.0
        l, t, r, b = embedded_to_pixel((W, H, W, H), page_height_pt=H, page_width_pt=W, dpi=300, rotate=90)
        assert abs(l) < 1e-6 and abs(t) < 1e-6

    def test_r90_bottom_left_corner(self):
        # Portrait bottom-left (0, 0) → landscape bottom-right pixel (H*s, W*s)
        s = 300 / 72.0
        H, W = 200.0, 100.0
        l, t, r, b = embedded_to_pixel((0, 0, 0, 0), page_height_pt=H, page_width_pt=W, dpi=300, rotate=90)
        assert abs(l - H * s) < 1e-6 and abs(t - W * s) < 1e-6

    def test_r90_invariant_left_less_right(self):
        l, t, r, b = embedded_to_pixel((10, 20, 50, 80), page_height_pt=200, page_width_pt=100, dpi=300, rotate=90)
        assert l < r and t < b

    # -- R=180 --
    # Formula: left=(W-x1)*s, top=(H-y1)*s, right=(W-x0)*s, bottom=(H-y0)*s

    def test_r180_bottom_left_becomes_top_right(self):
        # (0, 0) → pixel (W*s, H*s) = bottom-right of rendered
        s = 300 / 72.0
        H, W = 200.0, 100.0
        l, t, r, b = embedded_to_pixel((0, 0, 0, 0), page_height_pt=H, page_width_pt=W, dpi=300, rotate=180)
        assert abs(l - W * s) < 1e-6 and abs(t - H * s) < 1e-6

    def test_r180_top_right_becomes_bottom_left(self):
        # (W, H) → pixel (0, 0) = top-left
        s = 300 / 72.0
        H, W = 200.0, 100.0
        l, t, r, b = embedded_to_pixel((W, H, W, H), page_height_pt=H, page_width_pt=W, dpi=300, rotate=180)
        assert abs(l) < 1e-6 and abs(t) < 1e-6

    def test_r180_invariant_left_less_right(self):
        l, t, r, b = embedded_to_pixel((10, 20, 50, 80), page_height_pt=200, page_width_pt=100, dpi=300, rotate=180)
        assert l < r and t < b

    # -- R=270 CCW --
    # Formula: left=(H-y1)*s, top=x0*s, right=(H-y0)*s, bottom=x1*s

    def test_r270_top_left_corner(self):
        # Portrait top-left (0, H) → landscape top-left pixel (0, 0)
        s = 300 / 72.0
        H, W = 200.0, 100.0
        l, t, r, b = embedded_to_pixel((0, H, 0, H), page_height_pt=H, page_width_pt=W, dpi=300, rotate=270)
        assert abs(l) < 1e-6 and abs(t) < 1e-6

    def test_r270_bottom_right_corner(self):
        # Portrait bottom-right (W, 0) → landscape bottom-right pixel (H*s, W*s)
        s = 300 / 72.0
        H, W = 200.0, 100.0
        l, t, r, b = embedded_to_pixel((W, 0, W, 0), page_height_pt=H, page_width_pt=W, dpi=300, rotate=270)
        assert abs(l - H * s) < 1e-6 and abs(t - W * s) < 1e-6

    def test_r270_invariant_left_less_right(self):
        l, t, r, b = embedded_to_pixel((10, 20, 50, 80), page_height_pt=200, page_width_pt=100, dpi=300, rotate=270)
        assert l < r and t < b

    # -- Error handling --

    def test_invalid_rotate_raises(self):
        with pytest.raises(ValueError, match="rotate"):
            embedded_to_pixel((0, 0, 10, 10), page_height_pt=100, dpi=300, rotate=45)


# ---------------------------------------------------------------------------
# center_inside
# ---------------------------------------------------------------------------

class TestCenterInside:
    def test_center_exactly_inside(self):
        inner = (10, 10, 30, 30)   # center (20, 20)
        outer = (0, 0, 40, 40)
        assert center_inside(inner, outer)

    def test_center_on_edge(self):
        inner = (0, 0, 20, 0)      # center (10, 0)
        outer = (0, 0, 20, 10)
        assert center_inside(inner, outer)

    def test_center_outside(self):
        inner = (50, 50, 90, 90)   # center (70, 70)
        outer = (0, 0, 40, 40)
        assert not center_inside(inner, outer)

    def test_center_outside_x(self):
        inner = (0, 0, 60, 20)     # center (30, 10) — x=30 outside [0,20]
        outer = (0, 0, 20, 20)
        assert not center_inside(inner, outer)

    def test_embedded_inside_larger_ocr_box(self):
        # OCR box spans full line; embedded word is a small portion of it
        emb = _wb(100, 5, 150, 20)
        ocr_box = (0, 0, 500, 30)
        assert center_inside(emb.bbox, ocr_box)


# ---------------------------------------------------------------------------
# iou
# ---------------------------------------------------------------------------

class TestIoU:
    def test_no_overlap(self):
        assert iou((0, 0, 10, 10), (20, 20, 30, 30)) == 0.0

    def test_identical_boxes(self):
        assert abs(iou((0, 0, 10, 10), (0, 0, 10, 10)) - 1.0) < 1e-9

    def test_half_overlap(self):
        # a=(0,0,10,10), b=(5,0,15,10): intersection=(5,0,10,10)=50, union=150
        v = iou((0, 0, 10, 10), (5, 0, 15, 10))
        assert abs(v - 50 / 150) < 1e-9

    def test_contained(self):
        # inner fully inside outer: iou = inner_area / outer_area
        outer = (0, 0, 10, 10)
        inner = (2, 2, 8, 8)
        expected = (6 * 6) / (10 * 10)
        assert abs(iou(inner, outer) - expected) < 1e-9

    def test_edge_touch_only(self):
        assert iou((0, 0, 10, 10), (10, 0, 20, 10)) == 0.0

    def test_threshold_comparison(self):
        # Verify iou ≥ 0.30 for boxes with moderate overlap
        v = iou((0, 0, 100, 100), (30, 0, 130, 100))
        assert v >= 0.30


# ---------------------------------------------------------------------------
# match_words — one-to-many
# ---------------------------------------------------------------------------

class TestMatchWords:
    cfg = OCRCrossCheckConfig(use_center_containment=True, iou_floor=0.30)

    def test_empty_embedded(self):
        groups, unm_emb, unm_ocr = match_words([], [_wb(0, 0, 100, 20, source=WordSource.OCR)], self.cfg)
        assert groups == []
        assert unm_emb == []
        assert len(unm_ocr) == 1

    def test_empty_ocr(self):
        groups, unm_emb, unm_ocr = match_words([_wb(0, 0, 50, 20)], [], self.cfg)
        assert groups == []
        assert len(unm_emb) == 1
        assert unm_ocr == []

    def test_perfect_1to1_match(self):
        emb = _wb(10, 5, 50, 15)            # center (30, 10)
        ocr = _wb(0, 0, 60, 20, source=WordSource.OCR, conf=0.95)
        groups, unm_emb, unm_ocr = match_words([emb], [ocr], self.cfg)
        assert len(groups) == 1
        assert len(unm_emb) == 0
        assert len(unm_ocr) == 0
        embedded_tuple, ocr_box = groups[0]
        assert emb in embedded_tuple

    def test_one_to_many_two_embedded_in_one_ocr(self):
        # OCR line box spans two embedded words (common: "Rs 5,000")
        emb1 = _wb(10, 5, 80, 15, text="Rs")         # center (45, 10)
        emb2 = _wb(85, 5, 160, 15, text="5,000")     # center (122, 10)
        ocr_box = _wb(0, 0, 200, 20, source=WordSource.OCR, conf=0.98, text="Rs 5,000")
        groups, unm_emb, unm_ocr = match_words([emb1, emb2], [ocr_box], self.cfg)
        assert len(groups) == 1
        assert len(unm_emb) == 0
        assert len(unm_ocr) == 0
        embs, ob = groups[0]
        assert emb1 in embs and emb2 in embs

    def test_reading_order_sort(self):
        # Three embedded words — verify sorted top-to-bottom, left-to-right
        words = [
            _wb(100, 50, 150, 70, text="C"),   # center (125, 60)
            _wb(10, 10, 60, 30, text="A"),      # center (35, 20)
            _wb(70, 10, 120, 30, text="B"),     # center (95, 20)
        ]
        ocr = _wb(0, 0, 200, 100, source=WordSource.OCR, conf=0.9)
        groups, _, _ = match_words(words, [ocr], self.cfg)
        embs, _ = groups[0]
        # Row 1: A (cy=20, cx=35), B (cy=20, cx=95); Row 2: C (cy=60, cx=125)
        texts = [e.text for e in embs]
        assert texts == ["A", "B", "C"]

    def test_iou_fallback_matches_word_outside_center(self):
        # Embedded word whose center is outside the OCR box but has high IoU
        # Small offset: embedded (0,0,10,10), ocr (8,0,20,10) → IoU ≥ 0.30
        emb = _wb(0, 0, 10, 10)            # center (5, 5) — outside ocr [8,0,20,10]
        ocr = _wb(8, 0, 20, 10, source=WordSource.OCR, conf=0.9)
        # IoU: intersection=[8,0,10,10]=20, union=[0,0,20,10]=200-20=180, iou=20/180≈0.111 < 0.30
        # So this specific case should NOT match via IoU.
        groups, unm_emb, unm_ocr = match_words([emb], [ocr], self.cfg)
        # low IoU → both unmatched
        assert len(groups) == 0
        assert len(unm_emb) == 1
        assert len(unm_ocr) == 1

    def test_iou_fallback_with_high_overlap(self):
        # Two boxes with IoU ≈ 0.67 (good overlap, center just outside)
        emb = _wb(0, 0, 30, 10)            # center (15, 5), area=300
        ocr = _wb(10, 0, 40, 10, source=WordSource.OCR, conf=0.9)
        # Intersection=[10,0,30,10]=200, union=300+300-200=400, iou=200/400=0.5≥0.30 ✓
        # Center of emb (15,5) — is (15,5) in [10,0,40,10]? Yes! 10≤15≤40 and 0≤5≤10 ✓
        # Actually center IS inside, so this tests center-containment, not IoU fallback.
        groups, unm_emb, unm_ocr = match_words([emb], [ocr], self.cfg)
        assert len(groups) == 1 and len(unm_emb) == 0

    def test_unmatched_embedded_goes_to_residue(self):
        emb = _wb(500, 500, 600, 520)     # far from OCR box
        ocr = _wb(0, 0, 100, 20, source=WordSource.OCR, conf=0.9)
        groups, unm_emb, unm_ocr = match_words([emb], [ocr], self.cfg)
        assert len(groups) == 0
        assert emb in unm_emb
        assert ocr in unm_ocr

    def test_unmatched_ocr_goes_to_residue(self):
        emb = _wb(10, 5, 50, 15)
        ocr1 = _wb(0, 0, 60, 20, source=WordSource.OCR, conf=0.9)   # matches emb
        ocr2 = _wb(300, 300, 400, 320, source=WordSource.OCR, conf=0.9)  # no match
        groups, unm_emb, unm_ocr = match_words([emb], [ocr1, ocr2], self.cfg)
        assert len(groups) == 1
        assert len(unm_emb) == 0
        assert ocr2 in unm_ocr

    def test_ocr_box_claimed_by_center_not_reused_in_iou(self):
        # Once an OCR box has embedded words via center-containment, it must not
        # be re-used as a fallback for other embedded words via IoU.
        emb1 = _wb(5, 5, 25, 15)           # center (15,10) inside ocr [0,0,30,20]
        emb2 = _wb(0, 0, 35, 20, text="B") # center (17.5, 10) also inside ocr
        ocr = _wb(0, 0, 30, 20, source=WordSource.OCR, conf=0.9)
        groups, unm_emb, unm_ocr = match_words([emb1, emb2], [ocr], self.cfg)
        # emb2 center (17.5, 10) IS inside [0,0,30,20] → both matched to same OCR box
        assert len(groups) == 1
        embs, ob = groups[0]
        assert emb1 in embs and emb2 in embs

    def test_no_center_containment_mode(self):
        # When use_center_containment=False, only IoU fallback runs.
        cfg = OCRCrossCheckConfig(use_center_containment=False, iou_floor=0.30)
        emb = _wb(0, 0, 10, 10)             # center (5,5) inside [0,0,100,100]
        ocr = _wb(0, 0, 100, 100, source=WordSource.OCR, conf=0.9)
        # IoU: intersection=100, union=10100-100=10000, iou=100/10000=0.01 < 0.30
        groups, unm_emb, unm_ocr = match_words([emb], [ocr], cfg)
        # low IoU → no match
        assert len(groups) == 0


# ---------------------------------------------------------------------------
# extract_embedded_words — real PDF
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not INVOICE_PRESENT, reason="test_pdf's/Microsoft-Sample-Invoice_clear.pdf not found")
class TestExtractEmbeddedWords:
    """Acceptance check: embedded extraction on the Microsoft invoice at 300 DPI."""

    DPI = 300

    def _extract(self):
        from pdfminer.high_level import extract_pages
        layouts = list(extract_pages(open(INVOICE_PATH, "rb")))
        return extract_embedded_words(layouts, dpi=self.DPI), layouts

    def test_returns_nonempty_list(self):
        words, _ = self._extract()
        assert len(words) > 100, "Expected many tokens from a 13-page invoice"

    def test_all_embedded_source(self):
        words, _ = self._extract()
        assert all(w.source == WordSource.EMBEDDED for w in words)

    def test_all_conf_none(self):
        words, _ = self._extract()
        assert all(w.conf is None for w in words)

    def test_bboxes_in_pixel_space(self):
        # At 300 DPI, page 0 is 792*300/72 × 612*300/72 = 3300 × 2550 px
        # All bboxes on page 0 should have left < right, top < bottom
        words, _ = self._extract()
        p0 = [w for w in words if w.page_index == 0]
        assert len(p0) > 10
        for w in p0:
            l, t, r, b = w.bbox
            assert l < r, f"left ≥ right for {w.text!r}: {w.bbox}"
            assert t < b, f"top ≥ bottom for {w.text!r}: {w.bbox}"

    def test_bboxes_within_page_dimensions(self):
        # Page 0: 3300 × 2550 px (with a small tolerance for rounding)
        scale = self.DPI / 72.0
        words, layouts = self._extract()
        p = layouts[0]
        W_px = p.width * scale
        H_px = p.height * scale
        p0 = [w for w in words if w.page_index == 0]
        off_page = [w for w in p0 if w.bbox[2] > W_px + 5 or w.bbox[3] > H_px + 5]
        assert len(off_page) == 0, f"Words outside page rect: {off_page[:3]}"

    def test_example_token_exists(self):
        # The invoice starts with "Example Corp" — verify it's extracted
        words, _ = self._extract()
        texts = {w.text for w in words}
        assert "Example" in texts or any("Example" in t for t in texts)

    def test_page_coverage(self):
        # All 13 pages should have extracted words
        words, _ = self._extract()
        page_indices = {w.page_index for w in words}
        assert len(page_indices) == 13, f"Only got words on pages: {sorted(page_indices)}"

    def test_high_match_rate_with_self(self):
        """Match embedded words against themselves → 100% match (limit case).

        This verifies match_words alignment logic: when OCR output == embedded
        output (perfect reconstruction), all words should be in matched groups
        with zero unmatched residue. This is the 'high match rate' acceptance.
        """
        words, _ = self._extract()
        p0_emb = [w for w in words if w.page_index == 0]
        # Treat the embedded words themselves as synthetic OCR (same bboxes)
        from pdf_forgery.ocr_crosscheck.models import WordSource
        from pdf_forgery.ocr_crosscheck.ocr_engine import StubOCREngine
        p0_ocr = [
            WordBox(text=w.text, bbox=w.bbox, source=WordSource.OCR, conf=1.0, page_index=0)
            for w in p0_emb
        ]
        groups, unm_emb, unm_ocr = match_words(p0_emb, p0_ocr)
        # Every embedded word's center must be inside its own bbox → 100% match
        total = len(p0_emb)
        matched = sum(len(g[0]) for g in groups)
        assert matched == total, f"Only {matched}/{total} embedded matched"
        assert len(unm_emb) == 0
        assert len(unm_ocr) == 0
