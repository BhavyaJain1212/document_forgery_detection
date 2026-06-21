"""Stage 6 (raster / pixel forensics) — Session 6.1 scaffolding tests.

Covers the three scaffolding modules only (no detectors / scoring):

* ``images.py``   — extraction round-trips an embedded JPEG and preserves the
  raw undecoded bytes; CMYK and Indexed images decode; caching on the context.
* ``activation.py`` — analyse for a scanned (image-dominant) page, skip for a
  digital-native one; the per-page predicate branches.
* ``engine.py``   — provider availability degrades cleanly when PhotoHolmes is
  absent; the stub provider is deterministic; classical method gating.
"""

from __future__ import annotations

import numpy as np
import pytest

import make_fixtures  # scripts/ is on sys.path via conftest
import make_image_forensics_fixtures as fixtures

from pdf_forgery.core.context import AnalysisContext
from pdf_forgery.image_forensics import (
    ClassicalProvider,
    DecodedImage,
    ImageForensicsConfig,
    PhotoHolmesProvider,
    StubForensicProvider,
    decoded_images,
    default_provider,
    extract_images,
)
from pdf_forgery.image_forensics.activation import (
    activate,
    activate_page,
    max_image_coverage,
)
from pdf_forgery.image_forensics.engine import (
    ForensicMap,
    ForensicMethod,
    ForensicProvider,
)

CFG = ImageForensicsConfig()


# --------------------------------------------------------------------------- #
# images.py — extraction
# --------------------------------------------------------------------------- #

class TestExtraction:
    def test_jpeg_roundtrip_preserves_raw_bytes(self):
        pdf, jpeg = fixtures.build_jpeg_image_pdf()
        images = extract_images(pdf, CFG)
        assert len(images) == 1
        img = images[0]
        # The ORIGINAL compressed JPEG must survive verbatim (double-JPEG needs
        # the un-re-encoded stream).
        assert img.is_jpeg
        assert img.jpeg_bytes == jpeg
        assert "/DCTDecode" in img.filters

    def test_jpeg_decodes_to_pixels(self):
        pdf, _ = fixtures.build_jpeg_image_pdf(w=64, h=48)
        img = extract_images(pdf, CFG)[0]
        assert img.pixels is not None
        assert img.pixels.shape == (48, 64, 3)
        assert img.width == 64 and img.height == 48
        assert img.note is None

    def test_cmyk_decodes_to_rgb(self):
        img = extract_images(fixtures.build_cmyk_image_pdf(w=16, h=12), CFG)[0]
        assert img.pixels is not None
        # CMYK is normalised to RGB for the methods.
        assert img.pixels.shape == (12, 16, 3)
        assert img.jpeg_bytes is None  # not a DCTDecode source

    def test_indexed_decodes(self):
        img = extract_images(fixtures.build_indexed_image_pdf(w=16, h=12), CFG)[0]
        assert img.pixels is not None
        assert img.pixels.shape[:2] == (12, 16)

    def test_placement_is_full_page_top_left_points(self):
        pdf, _ = fixtures.build_jpeg_image_pdf(w=64, h=48)
        img = extract_images(pdf, CFG)[0]
        assert img.placement == pytest.approx((0.0, 0.0, 64.0, 48.0))
        assert img.page_width_pt == pytest.approx(64.0)
        assert img.page_height_pt == pytest.approx(48.0)

    def test_content_hash_is_salted_and_present(self):
        pdf, _ = fixtures.build_jpeg_image_pdf()
        img = extract_images(pdf, CFG)[0]
        assert img.content_hash and len(img.content_hash) == 16
        # A different salt changes the id but not the (PHI) pixels.
        other = extract_images(pdf, ImageForensicsConfig(image_hash_salt="other"))[0]
        assert other.content_hash != img.content_hash

    def test_malformed_input_yields_empty_never_raises(self):
        assert extract_images(b"not a pdf", CFG) == []
        assert extract_images(b"", CFG) == []

    def test_decoded_images_cached_on_context(self):
        pdf, _ = fixtures.build_jpeg_image_pdf()
        ctx = AnalysisContext(pdf)
        first = decoded_images(ctx, CFG)
        second = decoded_images(ctx, CFG)
        # Same list object returned from the stage cache (computed once).
        assert first is second
        assert "image_forensics.decoded" in ctx.stage_cache


# --------------------------------------------------------------------------- #
# activation.py — predicate
# --------------------------------------------------------------------------- #

class TestActivation:
    def test_scanned_page_is_image_dominant(self):
        pdf, _ = fixtures.build_jpeg_image_pdf()  # full-page image, no text
        ctx = AnalysisContext(pdf)
        act = activate(ctx, CFG)
        assert act.any_image_dominant
        assert act.pages[0].image_dominant
        assert act.image_dominant_pages == (0,)

    def test_digital_native_page_skips(self):
        ctx = AnalysisContext(make_fixtures.build_clean())  # ~18 words, no image
        act = activate(ctx, CFG)
        assert not act.any_image_dominant
        assert act.pages[0].embedded_words >= CFG.min_embedded_words
        assert not act.pages[0].image_dominant

    def test_text_floor_branch(self):
        page = activate_page(page_index=0, embedded_words=3, images=[], config=CFG)
        assert page.image_dominant
        assert "text floor" in page.reason

    def test_image_dominance_branch(self):
        # Text-rich page, but a single image covers most of it.
        big = _fake_image(placement=(0, 0, 90, 90), page=(100, 100))
        page = activate_page(page_index=0, embedded_words=500, images=[big], config=CFG)
        assert page.max_image_coverage == pytest.approx(0.81)
        assert page.image_dominant
        assert "covers" in page.reason

    def test_small_image_does_not_dominate(self):
        small = _fake_image(placement=(0, 0, 10, 10), page=(100, 100))
        page = activate_page(page_index=0, embedded_words=500, images=[small], config=CFG)
        assert not page.image_dominant
        assert page.reason.startswith("digital-native")

    def test_missing_placement_never_inflates_coverage(self):
        no_box = _fake_image(placement=None, page=(100, 100))
        assert max_image_coverage([no_box]) == 0.0


# --------------------------------------------------------------------------- #
# engine.py — provider abstraction
# --------------------------------------------------------------------------- #

class TestEngine:
    def test_classical_is_default_and_available(self):
        prov = default_provider(CFG)
        assert prov.name == "classical"
        assert ClassicalProvider().is_available()
        names = [m.name for m in ClassicalProvider().methods(CFG)]
        assert "ela" in names and "double_jpeg" in names and "copy_move" in names

    def test_photoholmes_absent_degrades_cleanly(self):
        prov = PhotoHolmesProvider()
        # Not installed in this environment → unavailable, offers no methods,
        # never raises (the PaddleOCR availability pattern).
        assert prov.is_available() is False
        assert prov.methods(CFG) == []
        assert prov.dl_vram_ok(ImageForensicsConfig(enable_dl_methods=True)) is False
        prov_meta = prov.provenance(CFG)
        assert prov_meta.provider == "photoholmes"
        assert prov_meta.device == "unavailable"

    def test_providers_satisfy_protocol(self):
        for prov in (ClassicalProvider(), PhotoHolmesProvider(), StubForensicProvider()):
            assert isinstance(prov, ForensicProvider)

    def test_classical_methods_satisfy_protocol_and_gate_jpeg(self):
        methods = {m.name: m for m in ClassicalProvider().methods(CFG)}
        for m in methods.values():
            assert isinstance(m, ForensicMethod)
        jpeg_img = _fake_image(jpeg=True)
        flat_img = _fake_image(jpeg=False)
        # DQ / JPEG-grid only apply to a JPEG source.
        assert methods["double_jpeg"].applicable(jpeg_img, CFG)
        assert not methods["double_jpeg"].applicable(flat_img, CFG)
        assert methods["ela"].applicable(flat_img, CFG)

    def test_classical_analyze_is_deferred(self):
        ela = ClassicalProvider().methods(CFG)[0]
        with pytest.raises(NotImplementedError):
            ela.analyze(_fake_image(), CFG)

    def test_stub_is_deterministic_and_never_raises(self):
        prov = StubForensicProvider()
        assert prov.is_available()
        img = _fake_image()
        method = prov.methods(CFG)[0]
        a = method.analyze(img, CFG)
        b = method.analyze(img, CFG)
        assert isinstance(a, ForensicMap)
        assert a.scalar == b.scalar
        assert np.array_equal(a.heatmap, b.heatmap)
        # A different image (different content hash) gives a different result.
        c = method.analyze(_fake_image(content_hash="zzz"), CFG)
        assert c.scalar != a.scalar

    def test_provenance_records_versions_and_methods(self):
        prov = ClassicalProvider().provenance(CFG)
        assert prov.provider == "classical"
        assert prov.device == "cpu"
        assert prov.enable_dl_methods is False
        assert ("ela", "0.1.0-stub") in prov.methods
        assert "numpy" in prov.library_versions


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def _fake_image(
    *,
    placement=(0, 0, 10, 10),
    page=(100, 100),
    jpeg: bool = False,
    content_hash: str = "abc123",
) -> DecodedImage:
    return DecodedImage(
        page_index=0,
        xobject_id="1 0",
        colorspace="/DeviceRGB",
        filters=("/DCTDecode",) if jpeg else ("/FlateDecode",),
        width=10,
        height=10,
        bits=8,
        has_smask=False,
        content_hash=content_hash,
        pixels=np.zeros((10, 10, 3), dtype=np.uint8),
        jpeg_bytes=b"\xff\xd8jpeg" if jpeg else None,
        placement=placement,
        page_width_pt=page[0],
        page_height_pt=page[1],
    )
