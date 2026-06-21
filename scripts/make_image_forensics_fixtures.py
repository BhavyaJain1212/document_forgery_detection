#!/usr/bin/env python3
"""Generate Stage-6 (raster / pixel forensics) test fixtures (deterministic).

Session 6.1 needs *extraction* + *activation* fixtures only (no tamper signal
yet — the spliced / copy-moved / double-compressed positives arrive with the
detectors in 6.2/6.3). Everything here is built with Pillow / numpy / pikepdf,
fully local, no network, and byte-reproducible (fixed seeds + ``deterministic_id``).

Builders (each returns raw ``bytes`` so unit tests can stay in-memory):

* :func:`build_jpeg_image_pdf` — a single-page PDF with one embedded **DCTDecode
  (JPEG)** image. Returns ``(pdf_bytes, jpeg_bytes)`` so a test can assert the
  extractor round-trips the *original* JPEG bytes verbatim (double-JPEG depends
  on it). Full-page placement ⇒ also serves as the **scanned / image-dominant**
  activation fixture.
* :func:`build_cmyk_image_pdf` — an embedded **DeviceCMYK** image (decodes → RGB).
* :func:`build_indexed_image_pdf` — an embedded **Indexed** (palette) image.
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


def write_fixtures(dest: Path = _DEFAULT_DEST) -> dict[str, Path]:
    """Write the fixtures into *dest*; return a name -> path map."""
    dest.mkdir(parents=True, exist_ok=True)
    jpeg_pdf, _ = build_jpeg_image_pdf()
    artifacts = {
        "image_jpeg": jpeg_pdf,
        "image_cmyk": build_cmyk_image_pdf(),
        "image_indexed": build_indexed_image_pdf(),
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
