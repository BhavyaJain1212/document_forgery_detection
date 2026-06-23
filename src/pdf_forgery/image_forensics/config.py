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

    # ---- Per-method heatmap thresholds (§5) ----------------------------- #
    ela_threshold: float = 0.50
    dq_threshold: float = 0.50
    jpeg_grid_threshold: float = 0.50
    noise_threshold: float = 0.50

    # ---- Classical DSP knobs (§4 method math) --------------------------- #
    ela_quality: int = 75
    """JPEG quality used for the ELA recompression pass. Kept below typical scan
    quality so the recompression actually exercises the high-frequency content: a
    region with a different compression / noise history reaches a different error
    floor than its host (recompressing AT the source quality would be ~identity)."""

    anomaly_block: int = 16
    """Side (px) of the block grid ELA / noise measures are reduced to before the
    robust-z anomaly map (suppresses pixel speckle; resolution-independent)."""

    anomaly_z0: float = 3.0
    """Robust-z centre for the unidirectional (ELA / DQ / grid) anomaly sigmoid —
    a block must sit this many MAD above the image's own median to light up. High
    enough that a clean / uniformly compressed page stays dark (precision)."""

    anomaly_slope: float = 1.5
    """Steepness of the anomaly sigmoid around its centre."""

    noise_z0: float = 3.0
    """Robust-z centre for the bidirectional noise-residual anomaly (a spliced
    region deviates in EITHER noise direction)."""

    noise_flat_percentile: float = 0.5
    """Noise inconsistency is only estimated on the flattest this-fraction of
    blocks — an edge / rule / glyph inflates the residual without being a noise
    anomaly, so textured blocks are gated out (Noisesniffer principle). Keeps a
    lined / text-bearing clean scan out of a false MEDIUM. The structure mask is
    computed after a block-scale low-pass so foreign paper grain remains eligible."""

    ela_structure_gate: bool = True
    """Restrict ELA anomalies to low-structure blocks. JPEG recompression error
    is naturally high on glyph, rule, and box edges, so edge-dense documents can
    otherwise manufacture ELA regions without any pixel edit. Disable only for
    detector calibration / comparison with the legacy behaviour."""

    ela_flat_percentile: float = 0.6
    """Fraction of lowest-structure blocks eligible to carry an ELA anomaly.
    Mirrors ``noise_flat_percentile``: high-structure glyph/rule blocks are not
    useful evidence, while anomalous error in flat paper remains eligible."""

    dq_structure_gate: bool = True
    """Restrict recomputed-DCT lattice anomalies to low-structure 8x8 blocks.
    Decoded JPEG rounding/clipping error is naturally largest on glyph and rule
    edges, so those blocks are not reliable double-compression evidence. Disable
    only for detector calibration / comparison with the legacy behaviour."""

    dq_flat_percentile: float = 0.6
    """Fraction of lowest-structure 8x8 blocks eligible to carry a DQ anomaly.
    The DQ detector has its own 8x8 grid, independent of ``anomaly_block``."""

    dq_z0: float = 3.0
    """Robust-z centre for the double-JPEG quantisation-misfit anomaly."""

    jpeg_grid_z0: float = 3.0
    """Robust-z centre for the JPEG-grid on-grid-energy-deficit anomaly."""

    copy_move_min_matches: int = 12
    """Minimum RANSAC-verified ORB matches before a copy-move cluster fires."""

    copy_move_orb_features: int = 2000
    """ORB keypoint budget for copy-move detection."""

    copy_move_orb_max_dist: float = 48.0
    """Max Hamming distance for an ORB self-match to count toward copy-move."""

    copy_move_min_offset_frac: float = 0.08
    """A copy-move match pair must be separated by at least this fraction of the
    image diagonal — rejects repeated *adjacent* legitimate elements / texture."""

    copy_move_max_cluster_span_frac: float = 0.25
    """Maximum normalized bounding-box area spanned by either RANSAC inlier
    cluster. Larger clusters represent structural/page-layout duplication (for
    example two printed copies of one form), not a compact copy-move edit. Set
    to ``1.0`` to recover the legacy no-span-gate behaviour."""

    # ---- DL methods (PhotoHolmes, opt-in + VRAM-guarded; §3) ------------ #
    enable_dl_methods: bool = False
    """DL methods (CAT-Net / PSCC-Net / FOCAL) are OFF by default — they drag in
    torch and contend for VRAM with PaddleOCR + Ollama. TruFor is never enabled
    (non-profit license)."""

    dl_min_free_vram_mb: int = 2048
    """A DL method is instantiated only when at least this much free VRAM exists;
    otherwise the stage degrades to classical-only with a note."""

    # ---- Scoring rule tree (§7; shared core semantics) ------------------ #
    region_medium_min_strength: float = 0.60
    """A lone (single-method, uncorroborated) region needs peak strength >= this
    to reach MEDIUM; a weaker isolated blob is LOW (the §7 'single weak isolated
    blob from ONE method' rule). Two independent methods provide corroboration,
    but the co-located region must also pass the locality caps below to originate
    HIGH; page-spanning bands remain reviewable at MEDIUM."""

    splice_max_width_frac: float = 0.90
    """Maximum page-width fraction a co-located region may span and still
    originate HIGH. Near-full-width bands are structural/document-wide signals;
    they remain visible but are capped at MEDIUM for human review."""

    splice_max_area_frac: float = 0.12
    """Maximum page-area fraction a co-located region may cover and still
    originate HIGH. This is a secondary locality safety net, not the primary
    ELA/copy-move discriminator."""

    diffuse_lone_min_count: int = 4
    """Lone regions from one method at or above this count are a diffuse signal,
    not repeated independent evidence of localized edits, and score LOW."""

    diffuse_lone_coverage_frac: float = 0.20
    """Aggregate page-area coverage at which lone regions from one method are
    treated as diffuse and score LOW. Co-located regions are never demoted."""

    score_low: int = 15
    score_medium: int = 50
    score_medium_method_error: int = 40
    """MEDIUM score when the only signal is a method that ERRORED on an
    image-dominant page (never silently dropped, but weaker than a real region)."""

    score_high: int = 80
    score_high_value_bump: int = 8
    """Added to a HIGH co-located region's score when it overlaps the high-value
    band (capped at 100). Keeps amount-band tampers at the top of the HIGH band."""


__all__ = ["ImageForensicsConfig"]
