"""Tests for Stage 3 false-positive guards (§4).

Tests:
  - ``filter_offpage_embedded``: clipping guard drops words whose center is off-page
  - ``filter_low_confidence_ocr``: confidence floor drops low-confidence OCR words
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest
from pdf_forgery.ocr_crosscheck.config import OCRCrossCheckConfig
from pdf_forgery.ocr_crosscheck.guards import filter_low_confidence_ocr, filter_offpage_embedded
from pdf_forgery.ocr_crosscheck.models import WordBox, WordSource


def _wb(x0, y0, x1, y1, *, text="word", source=WordSource.EMBEDDED, conf=None, page=0):
    return WordBox(text=text, bbox=(x0, y0, x1, y1), source=source, conf=conf, page_index=page)


def _ocr(x0, y0, x1, y1, *, conf=0.9, text="word"):
    return _wb(x0, y0, x1, y1, source=WordSource.OCR, conf=conf, text=text)


PAGE_W = 500.0
PAGE_H = 300.0


# ---------------------------------------------------------------------------
# filter_offpage_embedded
# ---------------------------------------------------------------------------

class TestFilterOffpageEmbedded:
    cfg = OCRCrossCheckConfig(clip_margin_px=2.0)

    def _f(self, words):
        return filter_offpage_embedded(words, page_width_px=PAGE_W, page_height_px=PAGE_H, config=self.cfg)

    def test_empty_input(self):
        kept, dropped = self._f([])
        assert kept == [] and dropped == 0

    def test_in_page_word_kept(self):
        w = _wb(10, 10, 100, 30)   # center (55, 20) well inside [0,300]×[0,500]? No: W=500,H=300
        kept, dropped = self._f([w])
        assert len(kept) == 1 and dropped == 0

    def test_clearly_offpage_dropped(self):
        w = _wb(600, 10, 700, 30)  # center (650, 20) — x>500+2 → off-page
        kept, dropped = self._f([w])
        assert len(kept) == 0 and dropped == 1

    def test_negative_x_off_page(self):
        w = _wb(-100, 10, -10, 30)  # center (-55, 20) — x < -2 → off-page
        kept, dropped = self._f([w])
        assert len(kept) == 0 and dropped == 1

    def test_below_page_off_page(self):
        w = _wb(10, 400, 100, 450)  # center (55, 425) — y > 302 → off-page
        kept, dropped = self._f([w])
        assert len(kept) == 0 and dropped == 1

    def test_margin_allows_slight_overhang(self):
        # Word centered exactly at x=1 (inside margin=2): kept
        w = _wb(-2, 10, 4, 30)     # center (1, 20): 1 > -2 → kept
        kept, dropped = self._f([w])
        assert len(kept) == 1 and dropped == 0

    def test_center_on_boundary_kept(self):
        # Center exactly at x=0 (boundary with margin=2 → threshold=-2): kept
        w = _wb(-10, 10, 10, 30)   # center (0, 20) — 0 >= -2 → kept
        kept, dropped = self._f([w])
        assert len(kept) == 1 and dropped == 0

    def test_box_overruns_edge_but_center_inside(self):
        # Box extends off-page but center is within → kept (partial overlap rule)
        w = _wb(490, 10, 530, 30)   # center (510, 20) — 510 > 502 → off-page
        # Actually 510 > 500+2=502, so dropped
        kept, dropped = self._f([w])
        assert len(kept) == 0 and dropped == 1

    def test_box_overruns_edge_center_inside_kept(self):
        # Center inside, box extends slightly off right edge
        w = _wb(480, 10, 510, 30)   # center (495, 20) — 495 < 502 → kept
        kept, dropped = self._f([w])
        assert len(kept) == 1 and dropped == 0

    def test_multiple_mixed(self):
        words = [
            _wb(10, 10, 50, 30),       # center (30, 20) — kept
            _wb(-200, 10, -100, 30),   # center (-150, 20) — off-page
            _wb(200, 10, 300, 30),     # center (250, 20) — kept
            _wb(10, 400, 100, 500),    # center (55, 450) — off-page
        ]
        kept, dropped = self._f(words)
        assert len(kept) == 2 and dropped == 2

    def test_custom_margin(self):
        cfg_large = OCRCrossCheckConfig(clip_margin_px=10.0)
        w = _wb(-9, 10, -1, 30)     # center (-5, 20) — inside -10 margin → kept
        kept, dropped = filter_offpage_embedded([w], page_width_px=PAGE_W, page_height_px=PAGE_H, config=cfg_large)
        assert len(kept) == 1 and dropped == 0

    def test_drop_count_matches_dropped_words(self):
        words = [_wb(-100, 0, -50, 10) for _ in range(5)]
        kept, dropped = self._f(words)
        assert dropped == 5 and len(kept) == 0


# ---------------------------------------------------------------------------
# filter_low_confidence_ocr
# ---------------------------------------------------------------------------

class TestFilterLowConfidenceOCR:
    cfg = OCRCrossCheckConfig(ocr_conf_floor=0.50)

    def _f(self, words):
        return filter_low_confidence_ocr(words, self.cfg)

    def test_empty_input(self):
        kept, dropped = self._f([])
        assert kept == [] and dropped == 0

    def test_high_confidence_kept(self):
        w = _ocr(0, 0, 50, 20, conf=0.95)
        kept, dropped = self._f([w])
        assert len(kept) == 1 and dropped == 0

    def test_exactly_at_floor_kept(self):
        w = _ocr(0, 0, 50, 20, conf=0.50)
        kept, dropped = self._f([w])
        assert len(kept) == 1 and dropped == 0   # floor is inclusive

    def test_below_floor_dropped(self):
        w = _ocr(0, 0, 50, 20, conf=0.49)
        kept, dropped = self._f([w])
        assert len(kept) == 0 and dropped == 1

    def test_zero_confidence_dropped(self):
        w = _ocr(0, 0, 50, 20, conf=0.0)
        kept, dropped = self._f([w])
        assert len(kept) == 0 and dropped == 1

    def test_conf_none_kept(self):
        # Embedded words have conf=None → must not be filtered
        w = _wb(0, 0, 50, 20, source=WordSource.EMBEDDED, conf=None)
        kept, dropped = self._f([w])
        assert len(kept) == 1 and dropped == 0

    def test_mixed_confidences(self):
        words = [
            _ocr(0, 0, 10, 10, conf=0.9),
            _ocr(0, 0, 10, 10, conf=0.3),   # dropped
            _ocr(0, 0, 10, 10, conf=0.7),
            _ocr(0, 0, 10, 10, conf=0.1),   # dropped
            _ocr(0, 0, 10, 10, conf=0.5),
        ]
        kept, dropped = self._f(words)
        assert len(kept) == 3 and dropped == 2

    def test_custom_floor(self):
        cfg_strict = OCRCrossCheckConfig(ocr_conf_floor=0.80)
        words = [_ocr(0, 0, 10, 10, conf=c) for c in [0.90, 0.85, 0.75, 0.60]]
        kept, dropped = filter_low_confidence_ocr(words, cfg_strict)
        assert len(kept) == 2 and dropped == 2   # 0.90, 0.85 kept; 0.75, 0.60 dropped

    def test_drop_count_is_accurate(self):
        words = [_ocr(0, 0, 10, 10, conf=0.2) for _ in range(7)]
        kept, dropped = self._f(words)
        assert dropped == 7 and len(kept) == 0
