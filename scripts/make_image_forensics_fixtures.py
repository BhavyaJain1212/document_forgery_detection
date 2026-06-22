#!/usr/bin/env python3
"""Generate Stage-6 (raster / pixel forensics) test fixtures (deterministic).

Everything here is built with Pillow / numpy / pikepdf, fully local, no network,
and byte-reproducible (fixed seeds + ``deterministic_id``).

Extraction / activation builders (6.1):

* :func:`build_jpeg_image_pdf` — a single-page PDF with one embedded **DCTDecode
  (JPEG)** image. Returns ``(pdf_bytes, jpeg_bytes)`` so a test can assert the
  extractor round-trips the *original* JPEG bytes verbatim. Full-page ⇒ also the
  **scanned / image-dominant** activation fixture.
* :func:`build_cmyk_image_pdf` — an embedded **DeviceCMYK** image (decodes → RGB).
* :func:`build_indexed_image_pdf` — an embedded **Indexed** (palette) image.

Real-pixel tamper / precision builders (6.4 — the classical-DSP acceptance set):
:func:`build_clean_scan_pdf` (LOW), :func:`build_spliced_amount_pdf` (co-located
HIGH), :func:`build_double_compressed_pdf` (local re-compression localized),
:func:`build_recompressed_pdf` (innocent whole-page rescan → no local region),
:func:`build_copy_move_pdf` (duplicated stamp). The tamper builders also return a
fractional top-left ground-truth bbox so tests can assert region overlap.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

import numpy as np
import pikepdf
from PIL import Image
from pikepdf import Array, Dictionary, Name, Pdf, String

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_DEST = _REPO_ROOT / "tests" / "fixtures"


# --------------------------------------------------------------------------- #
# Raster sources (deterministic)
# --------------------------------------------------------------------------- #

def _rgb_array(w: int, h: int, *, seed: int) -> np.ndarray:
    """A smooth-ish deterministic RGB image (compresses to a real JPEG)."""
    rng = np.random.default_rng(seed)
    base = rng.integers(0, 256, size=(h, w, 3), dtype=np.uint8)
    # Blur a touch so the JPEG isn't pure noise (more realistic DCT content).
    img = Image.fromarray(base, "RGB").resize((w * 2, h * 2)).resize((w, h))
    return np.asarray(img)


def make_jpeg_bytes(w: int = 64, h: int = 48, *, seed: int = 7, quality: int = 90) -> bytes:
    """Encode a deterministic RGB JPEG and return its exact file bytes."""
    buf = io.BytesIO()
    Image.fromarray(_rgb_array(w, h, seed=seed), "RGB").save(
        buf, format="JPEG", quality=quality
    )
    return buf.getvalue()


# --------------------------------------------------------------------------- #
# PDF assembly (mirrors scripts/make_fixtures.py)
# --------------------------------------------------------------------------- #

def _add_image_page(pdf: Pdf, image: pikepdf.Object, *, page_w: float, page_h: float) -> None:
    """Append a page that paints ``image`` to fill the whole MediaBox."""
    content = pdf.make_stream(
        f"q {page_w} 0 0 {page_h} 0 0 cm /Im0 Do Q".encode("latin-1")
    )
    page = pdf.make_indirect(
        Dictionary(
            Type=Name.Page,
            MediaBox=Array([0, 0, page_w, page_h]),
            Contents=content,
            Resources=Dictionary(XObject=Dictionary(Im0=image)),
        )
    )
    pdf.Root.Pages.Kids.append(page)
    pdf.Root.Pages.Count = len(pdf.Root.Pages.Kids)


def build_jpeg_image_pdf(
    w: int = 64, h: int = 48, *, seed: int = 7
) -> tuple[bytes, bytes]:
    """Single-page PDF embedding one DCTDecode JPEG. Returns (pdf, jpeg_bytes)."""
    jpeg = make_jpeg_bytes(w, h, seed=seed)
    pdf = Pdf.new()
    img = pdf.make_stream(jpeg)
    img.Type = Name.XObject
    img.Subtype = Name.Image
    img.Width = w
    img.Height = h
    img.ColorSpace = Name.DeviceRGB
    img.BitsPerComponent = 8
    img.Filter = Name.DCTDecode
    _add_image_page(pdf, img, page_w=w, page_h=h)
    buf = io.BytesIO()
    pdf.save(buf, deterministic_id=True)
    return buf.getvalue(), jpeg


def build_cmyk_image_pdf(w: int = 16, h: int = 12) -> bytes:
    """Single-page PDF embedding one uncompressed DeviceCMYK image."""
    rng = np.random.default_rng(11)
    samples = rng.integers(0, 256, size=(h * w * 4,), dtype=np.uint8).tobytes()
    pdf = Pdf.new()
    img = pdf.make_stream(samples)
    img.Type = Name.XObject
    img.Subtype = Name.Image
    img.Width = w
    img.Height = h
    img.ColorSpace = Name.DeviceCMYK
    img.BitsPerComponent = 8
    _add_image_page(pdf, img, page_w=w, page_h=h)
    buf = io.BytesIO()
    pdf.save(buf, deterministic_id=True)
    return buf.getvalue()


def build_indexed_image_pdf(w: int = 16, h: int = 12) -> bytes:
    """Single-page PDF embedding one Indexed (palette) image."""
    palette = bytes([
        0, 0, 0,        # 0 -> black
        255, 0, 0,      # 1 -> red
        0, 255, 0,      # 2 -> green
        0, 0, 255,      # 3 -> blue
    ])
    hival = len(palette) // 3 - 1
    rng = np.random.default_rng(13)
    indices = rng.integers(0, hival + 1, size=(h * w,), dtype=np.uint8).tobytes()
    pdf = Pdf.new()
    img = pdf.make_stream(indices)
    img.Type = Name.XObject
    img.Subtype = Name.Image
    img.Width = w
    img.Height = h
    img.ColorSpace = Array([Name.Indexed, Name.DeviceRGB, hival, String(palette)])
    img.BitsPerComponent = 8
    _add_image_page(pdf, img, page_w=w, page_h=h)
    buf = io.BytesIO()
    pdf.save(buf, deterministic_id=True)
    return buf.getvalue()


# --------------------------------------------------------------------------- #
# Session 6.4 — real-pixel tamper fixtures (acceptance proof)
# --------------------------------------------------------------------------- #
#
# Each builder returns a single-page PDF embedding one full-page DCTDecode JPEG
# (so activation = image-dominant / scanned), built from a smooth "scanned bill"
# background. The tamper fixtures also return a fractional top-left ground-truth
# bbox ``(x0, top, x1, bottom)`` in ``[0,1]`` so tests can assert the flagged
# region overlaps it (real maps are noisy → overlap, not pixel-exact).

#: The amount band sits in the lower half of the page (matches the Stage-6
#: ``high_value_band_top_frac`` default of 0.50).
_PATCH_TOP_FRAC = 0.72
_PATCH_BOT_FRAC = 0.84
_PATCH_LEFT_FRAC = 0.55
_PATCH_RIGHT_FRAC = 0.92


def _bill_background(w: int, h: int, *, seed: int = 3) -> np.ndarray:
    """A smooth, low-contrast 'scanned bill' RGB background (few ELA/noise edges).

    A gentle diagonal gradient + a couple of faint horizontal rules + very low
    paper grain. Deliberately smooth so a clean single compression produces NO
    localized ELA / noise outlier (the precision baseline)."""
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    grad = 188.0 + (xx / max(w, 1)) * 34.0 + (yy / max(h, 1)) * 26.0
    rng = np.random.default_rng(seed)
    img = np.clip(grad + rng.normal(0.0, 1.5, (h, w)), 0, 255)
    return np.stack([img.astype(np.uint8)] * 3, axis=-1)


def _patch_box(w: int, h: int) -> tuple[int, int, int, int]:
    """Pixel ``(r0, r1, c0, c1)`` of the amount-band patch region."""
    return (
        int(_PATCH_TOP_FRAC * h),
        int(_PATCH_BOT_FRAC * h),
        int(_PATCH_LEFT_FRAC * w),
        int(_PATCH_RIGHT_FRAC * w),
    )


def _gt_bbox_frac() -> tuple[float, float, float, float]:
    """The patch ground-truth box as fractional top-left ``(x0, top, x1, bottom)``."""
    return (_PATCH_LEFT_FRAC, _PATCH_TOP_FRAC, _PATCH_RIGHT_FRAC, _PATCH_BOT_FRAC)


def _embed_jpeg_pdf(rgb: np.ndarray, *, quality: int) -> bytes:
    """Embed an RGB array as one full-page DCTDecode JPEG in a single-page PDF."""
    h, w = rgb.shape[:2]
    buf = io.BytesIO()
    Image.fromarray(rgb, "RGB").save(buf, format="JPEG", quality=quality)
    pdf = Pdf.new()
    img = pdf.make_stream(buf.getvalue())
    img.Type = Name.XObject
    img.Subtype = Name.Image
    img.Width = w
    img.Height = h
    img.ColorSpace = Name.DeviceRGB
    img.BitsPerComponent = 8
    img.Filter = Name.DCTDecode
    _add_image_page(pdf, img, page_w=w, page_h=h)
    out = io.BytesIO()
    pdf.save(out, deterministic_id=True)
    return out.getvalue()


def _jpeg_roundtrip(rgb: np.ndarray, *, quality: int) -> np.ndarray:
    """Compress then decode an RGB array (a real JPEG generation)."""
    buf = io.BytesIO()
    Image.fromarray(rgb, "RGB").save(buf, format="JPEG", quality=quality)
    return np.asarray(Image.open(io.BytesIO(buf.getvalue())).convert("RGB"))


def build_clean_scan_pdf(w: int = 400, h: int = 560) -> bytes:
    """Known-NEGATIVE: a clean scanned bill, single JPEG compression → LOW."""
    bg = _bill_background(w, h)
    return _embed_jpeg_pdf(bg, quality=90)


def build_spliced_amount_pdf(
    w: int = 400, h: int = 560
) -> tuple[bytes, tuple[float, float, float, float]]:
    """Known-POSITIVE: an amount-band patch from a foreign source → co-located HIGH.

    The host bill is compressed once (Q92), decoded, and the amount region is
    overwritten with a foreign patch (a light box + dark 'digit' bars + injected
    noise — a different texture *and* noise history), then the whole page is
    re-saved as JPEG. The patch lights up ELA (foreign edges + recompression) AND
    noise-residual (injected grain) over the same region → the §7 HIGH gate."""
    host = _jpeg_roundtrip(_bill_background(w, h), quality=92).copy()
    r0, r1, c0, c1 = _patch_box(w, h)
    rng = np.random.default_rng(101)
    # A 'whiteout + re-type' patch: a slightly lighter flat box (sharp boundary →
    # ELA / DQ localize) carrying a foreign noise level (flat interior → noise
    # inconsistency localizes). Two independent signals co-locate over the region.
    region = host[r0:r1, c0:c1].astype(np.float64) + 24.0
    region += rng.normal(0.0, 26.0, region.shape)
    host[r0:r1, c0:c1] = np.clip(region, 0, 255).astype(np.uint8)
    return _embed_jpeg_pdf(host, quality=90), _gt_bbox_frac()


def build_double_compressed_pdf(
    w: int = 400, h: int = 560
) -> tuple[bytes, tuple[float, float, float, float]]:
    """Known-POSITIVE: a LOCALLY re-compressed region → DQ lattice misfit localizes.

    The amount region alone is harshly re-compressed (Q25) before being pasted
    back and the whole page re-saved (Q85). The region's DCT coefficients no longer
    sit on the host quant lattice → the double-JPEG method localizes it. A *local*
    break, unlike the innocent whole-page recompression below."""
    host = _jpeg_roundtrip(_bill_background(w, h, seed=5), quality=92).copy()
    r0, r1, c0, c1 = _patch_box(w, h)
    region = host[r0:r1, c0:c1]
    host[r0:r1, c0:c1] = _jpeg_roundtrip(region, quality=25)
    return _embed_jpeg_pdf(host, quality=85), _gt_bbox_frac()


def build_recompressed_pdf(w: int = 400, h: int = 560) -> bytes:
    """Known-NEGATIVE (precision): a WHOLE-page innocent rescan → no LOCAL region.

    The clean bill is uniformly re-compressed (Q92 → Q72). Globally double-quantised
    but with no local break → robust-z anomaly maps see no positive outlier → no
    localized fire (must stay ≤ MEDIUM; not a false HIGH)."""
    once = _jpeg_roundtrip(_bill_background(w, h, seed=7), quality=92)
    return _embed_jpeg_pdf(once, quality=72)


def build_copy_move_pdf(
    w: int = 400, h: int = 560
) -> tuple[bytes, tuple[float, float, float, float]]:
    """Known-POSITIVE (conservative): a duplicated textured 'stamp' → copy-move MEDIUM.

    A keypoint-rich stamp is placed once, then an identical copy is pasted at a
    non-trivial offset (a cloned approval stamp). ORB + RANSAC matches the
    duplicate. Copy-move alone is MEDIUM (never HIGH without a corroborating
    boundary break). Returns the ground-truth box of the *duplicate* (destination)."""
    bg = _bill_background(w, h, seed=9).copy()
    rng = np.random.default_rng(202)
    sw, sh = 70, 50
    stamp = rng.integers(0, 256, size=(sh, sw, 3), dtype=np.uint8)  # rich texture
    # Source stamp (upper-left area) and an identical duplicate (lower-right band).
    sr0, sc0 = int(0.12 * h), int(0.10 * w)
    dr0, dc0 = int(0.74 * h), int(0.62 * w)
    bg[sr0 : sr0 + sh, sc0 : sc0 + sw] = stamp
    bg[dr0 : dr0 + sh, dc0 : dc0 + sw] = stamp
    pdf = _embed_jpeg_pdf(bg, quality=95)
    gt = (dc0 / w, dr0 / h, (dc0 + sw) / w, (dr0 + sh) / h)
    return pdf, gt


def write_fixtures(dest: Path = _DEFAULT_DEST) -> dict[str, Path]:
    """Write the fixtures into *dest*; return a name -> path map."""
    dest.mkdir(parents=True, exist_ok=True)
    jpeg_pdf, _ = build_jpeg_image_pdf()
    spliced_pdf, _ = build_spliced_amount_pdf()
    double_pdf, _ = build_double_compressed_pdf()
    copymove_pdf, _ = build_copy_move_pdf()
    artifacts = {
        "image_jpeg": jpeg_pdf,
        "image_cmyk": build_cmyk_image_pdf(),
        "image_indexed": build_indexed_image_pdf(),
        # Session 6.4 real-pixel tamper / precision fixtures.
        "image_clean_scan": build_clean_scan_pdf(),
        "image_spliced_amount": spliced_pdf,
        "image_double_compressed": double_pdf,
        "image_recompressed": build_recompressed_pdf(),
        "image_copy_move": copymove_pdf,
    }
    paths: dict[str, Path] = {}
    for name, data in artifacts.items():
        p = dest / f"{name}.pdf"
        p.write_bytes(data)
        paths[name] = p
    return paths


if __name__ == "__main__":  # pragma: no cover
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else _DEFAULT_DEST
    for name, path in write_fixtures(out).items():
        print(f"wrote {name}: {path}")
