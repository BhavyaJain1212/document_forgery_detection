"""Stage 3 acceptance tests (integration, §7).

Three acceptance cases exercised with StubOCREngine (PaddleOCR not installed):

1. INCONCLUSIVE — PaddleOCR engine unavailable → graceful degradation.
2. HIGH — overlay simulation: embedded layer has "Rs 5,000", stub OCR returns
   "Rs 50,000" at the same pixel location → MISMATCH on AMOUNT token → tier HIGH.
3. HIGH (EMBEDDED_ONLY) — hidden-text simulation: embedded layer has "Rs 5,000"
   with no rendered counterpart → EMBEDDED_ONLY on AMOUNT token → tier HIGH.

Additionally tests:
4. LOW/AGREE — all-agree scenario (stub OCR matches embedded exactly) → LOW.
5. Adapter round-trip: report_to_stage_result carries tier through correctly.
"""

from __future__ import annotations

import io
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import pytest
import pikepdf

from pdf_forgery.core.types import ConfidenceTier
from pdf_forgery.ocr_crosscheck.adapter import report_to_stage_result
from pdf_forgery.ocr_crosscheck.analyze import analyze_bytes
from pdf_forgery.ocr_crosscheck.models import WordBox, WordSource
from pdf_forgery.ocr_crosscheck.ocr_engine import PaddleOCREngine, StubOCREngine


# ---------------------------------------------------------------------------
# Shared fixture: clean PDF with 18 embedded words including "Rs 5,000"
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def clean_pdf_bytes():
    import make_fixtures
    return make_fixtures.build_clean()


@pytest.fixture(scope="module")
def clean_embedded_words(clean_pdf_bytes):
    """Extract embedded words from clean.pdf in pixel space (300 DPI, R=0)."""
    from pdf_forgery.ocr_crosscheck.align import extract_embedded_words
    from pdfminer.high_level import extract_pages
    layouts = list(extract_pages(io.BytesIO(clean_pdf_bytes)))
    return extract_embedded_words(layouts, 300, [])


# ---------------------------------------------------------------------------
# Case 1: INCONCLUSIVE — OCR engine unavailable
# ---------------------------------------------------------------------------

class TestInconclusiveNoEngine:
    def test_paddleocr_not_installed_returns_inconclusive(self, clean_pdf_bytes, monkeypatch):
        """When PaddleOCR is unavailable, degrade to INCONCLUSIVE with a note."""
        engine = PaddleOCREngine()
        monkeypatch.setattr(engine, "is_available", lambda: False)

        report = analyze_bytes(clean_pdf_bytes, path="clean.pdf", engine=engine)

        assert report.ok is True
        assert report.result is not None
        assert report.result.tier is ConfidenceTier.INCONCLUSIVE
        assert report.result.score is None
        assert any("PaddleOCR" in n or "paddleocr" in n.lower() for n in report.notes)

    def test_adapter_inconclusive_stage_result(self, clean_pdf_bytes, monkeypatch):
        engine = PaddleOCREngine()
        monkeypatch.setattr(engine, "is_available", lambda: False)
        report = analyze_bytes(clean_pdf_bytes, engine=engine)
        sr = report_to_stage_result(report)
        assert sr.tier is ConfidenceTier.INCONCLUSIVE
        assert sr.stage == "ocr_crosscheck"
        assert sr.ok is True
        assert sr.payload is report


# ---------------------------------------------------------------------------
# Case 2: HIGH — overlay simulation (amount changed in raster, embedded intact)
# ---------------------------------------------------------------------------

class TestOverlayAmountMismatch:
    """Simulate an image overlay changing Rs 5,000 → Rs 50,000 in the render."""

    def _make_overlay_engine(self, embedded_words):
        """Build a StubOCREngine whose page 0 mirrors all embedded words EXCEPT
        the amount token '5,000' which is replaced by '50,000'."""
        page0_ocr = []
        for w in embedded_words:
            if w.text == "5,000":
                # The overlay covers the original and shows a different amount
                fake = WordBox(
                    text="50,000",
                    bbox=w.bbox,  # same pixel location
                    source=WordSource.OCR,
                    conf=0.97,
                    page_index=0,
                )
                page0_ocr.append(fake)
            else:
                mirror = WordBox(
                    text=w.text,
                    bbox=w.bbox,
                    source=WordSource.OCR,
                    conf=0.95,
                    page_index=0,
                )
                page0_ocr.append(mirror)
        return StubOCREngine(pages={0: page0_ocr})

    def test_overlay_produces_high_tier(self, clean_pdf_bytes, clean_embedded_words):
        engine = self._make_overlay_engine(clean_embedded_words)
        report = analyze_bytes(clean_pdf_bytes, path="clean.pdf", engine=engine)

        assert report.ok is True
        assert report.result is not None
        assert report.result.tier is ConfidenceTier.HIGH

    def test_overlay_score_is_amount_date_range(self, clean_pdf_bytes, clean_embedded_words):
        engine = self._make_overlay_engine(clean_embedded_words)
        report = analyze_bytes(clean_pdf_bytes, engine=engine)

        assert report.result.score == 95  # score_high_amount_date_mismatch

    def test_overlay_has_mismatch_divergence(self, clean_pdf_bytes, clean_embedded_words):
        from pdf_forgery.ocr_crosscheck.models import DivergenceType, TokenClass
        engine = self._make_overlay_engine(clean_embedded_words)
        report = analyze_bytes(clean_pdf_bytes, engine=engine)

        mismatches = [
            d for d in report.result.divergences
            if d.type is DivergenceType.MISMATCH
        ]
        amount_mismatches = [
            d for d in mismatches
            if d.token_class is TokenClass.AMOUNT
        ]
        assert len(amount_mismatches) >= 1

    def test_overlay_adapter_high_finding(self, clean_pdf_bytes, clean_embedded_words):
        engine = self._make_overlay_engine(clean_embedded_words)
        report = analyze_bytes(clean_pdf_bytes, engine=engine)
        sr = report_to_stage_result(report)

        assert sr.tier is ConfidenceTier.HIGH
        # At least one HIGH-tier finding from the MISMATCH
        high_findings = [f for f in sr.findings if f.tier is ConfidenceTier.HIGH]
        assert len(high_findings) >= 1

    def test_overlay_finding_has_before_after_text(self, clean_pdf_bytes, clean_embedded_words):
        """The adapter finding must carry the embedded before-text (PHI: in-memory only)."""
        engine = self._make_overlay_engine(clean_embedded_words)
        report = analyze_bytes(clean_pdf_bytes, engine=engine)
        sr = report_to_stage_result(report)

        mismatch_findings = [
            f for f in sr.findings
            if f.before is not None and f.after is not None
        ]
        assert len(mismatch_findings) >= 1


# ---------------------------------------------------------------------------
# Case 3: HIGH (EMBEDDED_ONLY) — hidden text simulation
# ---------------------------------------------------------------------------

class TestHiddenTextEmbeddedOnly:
    """Simulate hidden/invisible text: embedded has the amount, OCR doesn't see it."""

    def _make_hidden_engine(self, embedded_words):
        """Return OCR matching all words EXCEPT the amount tokens (Rs, 5,000).
        Those appear in the embedded layer but have no rendered counterpart."""
        hidden_texts = {"Rs", "5,000"}
        page0_ocr = [
            WordBox(
                text=w.text,
                bbox=w.bbox,
                source=WordSource.OCR,
                conf=0.95,
                page_index=0,
            )
            for w in embedded_words
            if w.text not in hidden_texts
        ]
        return StubOCREngine(pages={0: page0_ocr})

    def test_hidden_amount_produces_high(self, clean_pdf_bytes, clean_embedded_words):
        engine = self._make_hidden_engine(clean_embedded_words)
        report = analyze_bytes(clean_pdf_bytes, path="clean.pdf", engine=engine)

        assert report.ok is True
        assert report.result is not None
        assert report.result.tier is ConfidenceTier.HIGH

    def test_hidden_amount_score_is_orphan_range(self, clean_pdf_bytes, clean_embedded_words):
        engine = self._make_hidden_engine(clean_embedded_words)
        report = analyze_bytes(clean_pdf_bytes, engine=engine)
        assert report.result.score == 75  # score_high_value_orphan

    def test_hidden_has_embedded_only_amount(self, clean_pdf_bytes, clean_embedded_words):
        from pdf_forgery.ocr_crosscheck.models import DivergenceType, TokenClass
        engine = self._make_hidden_engine(clean_embedded_words)
        report = analyze_bytes(clean_pdf_bytes, engine=engine)

        emb_only = [
            d for d in report.result.divergences
            if d.type is DivergenceType.EMBEDDED_ONLY
            and d.token_class is TokenClass.AMOUNT
        ]
        assert len(emb_only) >= 1


# ---------------------------------------------------------------------------
# Case 4: LOW / all-agree — clean OCR matches embedded exactly
# ---------------------------------------------------------------------------

class TestAllAgreeLow:
    """When OCR perfectly mirrors the embedded layer, we get LOW (clean)."""

    def _make_perfect_engine(self, embedded_words):
        page0_ocr = [
            WordBox(
                text=w.text,
                bbox=w.bbox,
                source=WordSource.OCR,
                conf=0.99,
                page_index=0,
            )
            for w in embedded_words
        ]
        return StubOCREngine(pages={0: page0_ocr})

    def test_all_agree_low_tier(self, clean_pdf_bytes, clean_embedded_words):
        engine = self._make_perfect_engine(clean_embedded_words)
        report = analyze_bytes(clean_pdf_bytes, engine=engine)

        assert report.ok is True
        assert report.result.tier is ConfidenceTier.LOW

    def test_all_agree_score_zero(self, clean_pdf_bytes, clean_embedded_words):
        engine = self._make_perfect_engine(clean_embedded_words)
        report = analyze_bytes(clean_pdf_bytes, engine=engine)
        assert report.result.score == 0


# ---------------------------------------------------------------------------
# Adapter: render_stage_json / render_stage_summary (smoke-tests)
# ---------------------------------------------------------------------------

class TestAdapterRenderers:
    def test_json_roundtrip_inconclusive(self, clean_pdf_bytes, monkeypatch):
        import json
        from pdf_forgery.ocr_crosscheck.adapter import render_stage_json
        engine = PaddleOCREngine()
        monkeypatch.setattr(engine, "is_available", lambda: False)
        report = analyze_bytes(clean_pdf_bytes, engine=engine)
        sr = report_to_stage_result(report)
        js = render_stage_json(sr)
        data = json.loads(js)
        assert data["tier"] == "inconclusive"
        assert data["stage"] == "ocr_crosscheck"
        assert data["ok"] is True

    def test_summary_contains_tier(self, clean_pdf_bytes, clean_embedded_words):
        from pdf_forgery.ocr_crosscheck.adapter import render_stage_summary
        # Use overlay scenario for a non-trivial summary
        page0_ocr = [
            WordBox(
                text="50,000" if w.text == "5,000" else w.text,
                bbox=w.bbox, source=WordSource.OCR, conf=0.95, page_index=0,
            )
            for w in clean_embedded_words
        ]
        engine = StubOCREngine(pages={0: page0_ocr})
        report = analyze_bytes(clean_pdf_bytes, engine=engine)
        sr = report_to_stage_result(report)
        summary = render_stage_summary(sr)
        assert "HIGH" in summary
        assert "ocr_crosscheck" in summary.lower() or "OCR" in summary


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------

class TestDiagnostics:
    def test_diagnostics_populated(self, clean_pdf_bytes, clean_embedded_words):
        page0_ocr = [
            WordBox(text=w.text, bbox=w.bbox, source=WordSource.OCR, conf=0.95, page_index=0)
            for w in clean_embedded_words
        ]
        engine = StubOCREngine(pages={0: page0_ocr})
        report = analyze_bytes(clean_pdf_bytes, engine=engine)
        assert "offpage_dropped" in report.diagnostics
        assert "low_conf_dropped" in report.diagnostics
        assert "matched" in report.diagnostics

    def test_diagnostics_match_count(self, clean_pdf_bytes, clean_embedded_words):
        page0_ocr = [
            WordBox(text=w.text, bbox=w.bbox, source=WordSource.OCR, conf=0.95, page_index=0)
            for w in clean_embedded_words
        ]
        engine = StubOCREngine(pages={0: page0_ocr})
        report = analyze_bytes(clean_pdf_bytes, engine=engine)
        # All 18 words should match
        assert report.diagnostics["matched"] == 18
        assert report.diagnostics["agree"] == 18
        assert report.diagnostics.get("mismatch", 0) == 0
