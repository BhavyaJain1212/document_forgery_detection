"""Tests for Stage 3 scanned / text-sparse routing (§5)."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from pdf_forgery.ocr_crosscheck.config import OCRCrossCheckConfig
from pdf_forgery.ocr_crosscheck.models import WordBox, WordSource
from pdf_forgery.ocr_crosscheck.routing import is_text_sparse


def _emb(n: int) -> list[WordBox]:
    return [WordBox(text=f"w{i}", bbox=(0, 0, 10, 10), source=WordSource.EMBEDDED, conf=None, page_index=0) for i in range(n)]


def _ocr(n: int) -> list[WordBox]:
    return [WordBox(text=f"o{i}", bbox=(0, 0, 10, 10), source=WordSource.OCR, conf=0.9, page_index=0) for i in range(n)]


CFG = OCRCrossCheckConfig(min_embedded_words=10, embedded_ocr_ratio_floor=0.10)


class TestIsTextSparse:
    def test_rich_digital_native_not_sparse(self):
        # 100 embedded words, 110 OCR: ratio=100/110≈0.91 >> 0.10; count=100 >= 10
        assert not is_text_sparse(_emb(100), _ocr(110), CFG)

    def test_scanned_zero_embedded(self):
        assert is_text_sparse(_emb(0), _ocr(50), CFG)

    def test_below_absolute_floor(self):
        # 5 embedded < min_embedded_words=10 → sparse
        assert is_text_sparse(_emb(5), _ocr(5), CFG)

    def test_exactly_at_floor_not_sparse(self):
        # 10 embedded = min_embedded_words=10 and ratio ≥ 0.10 → NOT sparse
        assert not is_text_sparse(_emb(10), _ocr(10), CFG)

    def test_ratio_too_low(self):
        # 5 embedded, 200 OCR: ratio=5/200=0.025 < 0.10 → sparse
        # But also 5 < 10 (absolute floor) → sparse either way
        assert is_text_sparse(_emb(5), _ocr(200), CFG)

    def test_ratio_check_with_enough_absolute_count(self):
        # 10 embedded (≥ floor), 200 OCR: ratio=10/200=0.05 < 0.10 → sparse via ratio
        assert is_text_sparse(_emb(10), _ocr(200), CFG)

    def test_ratio_just_above_floor(self):
        # 20 embedded, 190 OCR: ratio=20/190≈0.105 ≥ 0.10 → NOT sparse
        assert not is_text_sparse(_emb(20), _ocr(190), CFG)

    def test_no_ocr_not_sparse_if_enough_embedded(self):
        # ocr=[] → ratio check skipped; only absolute floor matters
        assert not is_text_sparse(_emb(50), _ocr(0), CFG)

    def test_no_ocr_sparse_if_too_few_embedded(self):
        assert is_text_sparse(_emb(5), _ocr(0), CFG)

    def test_custom_config_higher_floor(self):
        cfg = OCRCrossCheckConfig(min_embedded_words=100, embedded_ocr_ratio_floor=0.10)
        assert is_text_sparse(_emb(50), _ocr(60), cfg)  # 50 < 100

    def test_custom_config_lower_ratio(self):
        cfg = OCRCrossCheckConfig(min_embedded_words=5, embedded_ocr_ratio_floor=0.01)
        # 10 embedded, 200 OCR: ratio=10/200=0.05 ≥ 0.01 → NOT sparse; 10 ≥ 5
        assert not is_text_sparse(_emb(10), _ocr(200), cfg)
