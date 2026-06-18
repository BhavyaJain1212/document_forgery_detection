"""Tests for the baked annotated-page PNG renderer (aggregate/overlay.py)."""

from __future__ import annotations

import sys
from io import BytesIO
from pathlib import Path

import pytest

_SCRIPTS = str(Path(__file__).resolve().parent.parent / "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

import make_localization_fixtures as F  # noqa: E402

from pdf_forgery.aggregate.config import AggregateConfig  # noqa: E402
from pdf_forgery.aggregate.overlay import render_page_overlay  # noqa: E402

pytest.importorskip("pypdfium2")
pytest.importorskip("PIL")


def test_render_returns_png_of_expected_dimensions():
    _, forged = F.amount_pair()
    cfg = AggregateConfig(overlay_dpi=150)
    png = render_page_overlay(forged, 0, [(120.0, 100.0, 170.0, 116.0)], config=cfg)
    assert png is not None and png[:8] == b"\x89PNG\r\n\x1a\n"

    from PIL import Image

    img = Image.open(BytesIO(png))
    scale = cfg.overlay_dpi / 72.0
    # pypdfium2 may round a half-pixel differently; allow +/-1 px per axis.
    assert abs(img.size[0] - round(612 * scale)) <= 1
    assert abs(img.size[1] - round(792 * scale)) <= 1


def test_box_pixels_are_drawn_in_the_expected_region():
    _, forged = F.amount_pair()
    cfg = AggregateConfig(overlay_dpi=150, overlay_box_rgb=(220, 38, 38))
    box = (120.0, 100.0, 170.0, 116.0)
    png = render_page_overlay(forged, 0, [box], config=cfg)
    from PIL import Image

    img = Image.open(BytesIO(png)).convert("RGB")
    scale = cfg.overlay_dpi / 72.0
    # Sample a pixel on the top edge of the drawn rectangle.
    px = int((box[0] + box[2]) / 2 * scale), int(box[1] * scale)
    r, g, b = img.getpixel(px)
    assert r > 150 and g < 120 and b < 120  # crimson stroke present


def test_out_of_range_page_returns_none():
    _, forged = F.amount_pair()
    assert render_page_overlay(forged, 99, [(0.0, 0.0, 10.0, 10.0)]) is None


def test_empty_boxes_still_renders_the_page():
    _, forged = F.amount_pair()
    png = render_page_overlay(forged, 0, [])
    assert png is not None and png[:8] == b"\x89PNG\r\n\x1a\n"


def test_concurrent_renders_all_succeed():
    """pypdfium2 is not thread-safe; the reviewer UI fires many parallel
    page-image requests. Without the render lock most came back None/corrupt;
    serialized, every concurrent render must produce a valid PNG."""
    import threading

    _, forged = F.amount_pair()
    results: dict[int, bytes | None] = {}

    def work(i: int) -> None:
        results[i] = render_page_overlay(forged, 0, [])

    threads = [threading.Thread(target=work, args=(i,)) for i in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == 12
    for png in results.values():
        assert png is not None and png[:8] == b"\x89PNG\r\n\x1a\n"
