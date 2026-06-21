"""Configuration for Stage 6 — raster / pixel forensics (``image_forensics``).

ALL tunables for the stage live here so nothing magic is hard-coded outside it
(the project-wide rubric requirement, mirroring every other stage's ``config``).
This is the canonical knob set named in ``docs/STAGE6_DESIGN.md`` §7/§10.

Session 6.1 wires only the *activation* and *extraction* knobs into live code;
the per-method thresholds / score bands / DL knobs are declared now (the contract
is frozen) and consumed by the scoring + method implementations in 6.2/6.3.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ImageForensicsConfig:
    """Thresholds + toggles for the raster/pixel-forensics stage.

    Frozen so a run's configuration can be snapshotted into the reproducibility
    manifest (``engine.ForensicProvenance``) without risk of later mutation.
    """

    # ---- Activation predicate (§1) -------------------------------------- #
    min_embedded_words: int = 10
    """A page with fewer embedded words than this is image-dominant (text floor).
    Mirrors Stage 3's ``min_embedded_words`` so the two stages partition the
    document the same way (Stage 3 owns text-rich pages, Stage 6 owns the rest)."""

    image_area_dominance_frac: float = 0.60
    """A single decoded raster image covering >= this fraction of the page area
    makes the page image-dominant even when embedded text is present."""

    # ---- Pixel source / decoding (§2) ----------------------------------- #
    recurse_form_xobjects: bool = True
    """Recurse one level into form XObjects to catch images nested in a form's
    own ``/Resources``."""

    image_hash_salt: str = "image_forensics-v1"
    """Salt for the per-image content hash (PHI-safe id; never the pixels)."""

    # ---- Localization (§6) ---------------------------------------------- #
    min_blob_area_frac: float = 0.005
    """Drop heatmap blobs smaller than this fraction of the image area (speckle)."""

    global_coverage_frac: float = 0.65
    """A method whose thresholded heatmap (or any single blob) covers >= this
    fraction of the image is treated as a GLOBAL / diffuse signal — whole-image
    recompression or a uniform scanner artifact, NOT a local edit. Such a signal
    is recorded but never emitted as a localized tamper region (§5/§7 LOW rule:
    'only a GLOBAL/diffuse signal ... consistent with an innocent rescan')."""

    colocate_iou: float = 0.30
    """Two method fires are co-located when their page-point bboxes overlap by
    at least this IoU — the corroboration test that gates HIGH (§7)."""

    # ---- High-value region heuristic (§4 — positional, NOT a token class) -- #
    high_value_band_top_frac: float = 0.50
    high_value_band_bottom_frac: float = 1.0
    """Vertical band of the page (fractions of page height, measured top-down)
    treated as the 'amount / total' area on a scanned bill. A surviving region
    overlapping this band is TAGGED high-value. This is a deliberately coarse
    POSITIONAL prior — scanned pages have no reliable text layer, so we do NOT
    fabricate token classes (amount/date/etc.); the tag is advisory only and at
    most a weak booster downstream (6.3). Default: the lower half of the page,
    where invoice totals usually sit. Widen/narrow per document population."""

    # ---- Per-method heatmap thresholds (§5; consumed in 6.2/6.3) -------- #
    ela_threshold: float = 0.50
    dq_threshold: float = 0.50
    jpeg_grid_threshold: float = 0.50
    noise_threshold: float = 0.50
    copy_move_min_matches: int = 12
    """Minimum RANSAC-verified ORB matches before a copy-move cluster fires."""

    # ---- DL methods (PhotoHolmes, opt-in + VRAM-guarded; §3) ------------ #
    enable_dl_methods: bool = False
    """DL methods (CAT-Net / PSCC-Net / FOCAL) are OFF by default — they drag in
    torch and contend for VRAM with PaddleOCR + Ollama. TruFor is never enabled
    (non-profit license)."""

    dl_min_free_vram_mb: int = 2048
    """A DL method is instantiated only when at least this much free VRAM exists;
    otherwise the stage degrades to classical-only with a note."""

    # ---- Score bands (§7; shared core semantics) ------------------------ #
    score_low: int = 15
    score_medium: int = 50
    score_high: int = 80


__all__ = ["ImageForensicsConfig"]
