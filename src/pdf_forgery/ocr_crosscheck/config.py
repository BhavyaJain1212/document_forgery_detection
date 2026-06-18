"""Central configuration for Stage 3 (OCR ↔ embedded-text cross-check).

Every threshold, score value, weight, tolerance, and toggle lives here so
nothing magic is hard-coded in the aligners / classifiers / scorer. Pass an
:class:`OCRCrossCheckConfig` to the public API; pass ``None`` for the
spec-default values (equivalent to ``OCRCrossCheckConfig()``).

The numbers below are the design defaults from ``docs/STAGE3_DESIGN.md``; they
are deliberately exposed for override rather than embedded at call sites.
"""

from __future__ import annotations

from dataclasses import dataclass, field


def _default_confusion_classes() -> tuple[tuple[str, ...], ...]:
    """Curated OCR-confusion equivalence classes (§3a, fold step 4).

    Each inner tuple is a set of visually-confusable forms collapsed to its
    FIRST element on both sides of a comparison, so a known OCR confusion is
    never counted as a real edit. ``"rn"``/``"m"`` is a digraph fold applied as a
    substring pass.
    """
    return (
        ("0", "O", "o", "Q", "D"),
        ("1", "l", "I", "|", "i"),
        ("m", "rn"),
        ("5", "S", "s"),
        ("8", "B"),
        ("2", "Z", "z"),
    )


@dataclass
class OCRCrossCheckConfig:
    """Tunable parameters for Stage 3 rendering, alignment, tolerance, scoring."""

    # ------------------------------------------------------------------ #
    # Rendering (§1)                                                      #
    # ------------------------------------------------------------------ #

    render_dpi: int = 300
    """DPI to rasterise each page at for OCR. 300 is the reliable-OCR floor for
    8-10pt body text; Stage 3 does NOT reuse the 150-DPI preview cache."""

    paddle_use_doc_orientation_classify: bool = False
    """PaddleOCR 3.x document-orientation classifier. Off: pypdfium2 already
    renders the page upright, and the classifier's output coordinate space does
    not match the rendered raster the embedded→pixel transform assumes."""

    paddle_use_doc_unwarping: bool = False
    """PaddleOCR 3.x UVDoc geometric dewarp. Off: every page Stage 3 sees is a
    digital-native PDF rasterised flat — dewarping a flat page only displaces
    the OCR boxes relative to the embedded-text transform (root cause of mass
    false-positive divergence; see docs/STAGE3_OCR_FALSE_POSITIVE_FIX.md)."""

    paddle_use_textline_orientation: bool = False
    """PaddleOCR 3.x textline-orientation classifier. Off: digital-native PDF
    text lines are axis-aligned; the classifier is for rotated/skewed scans."""

    ocr_max_side_px: int = 2000
    """Cap the longest side (px) of the image actually fed to OCR. A 300-DPI A4
    page is ~3300px on its long edge; PaddleOCR's GPU working set scales with
    image area, so on an 8GB card the allocator fills up after the first dense
    page and every subsequent page throws CUDA OOM (silently → 0 words → every
    embedded word becomes a false 'hidden text' orphan). Downscaling the OCR
    input below this cap keeps the working set bounded; the engine rescales the
    returned boxes back to the full render_dpi raster space so alignment is
    unchanged. Set to 0 to disable the cap."""

    ocr_empty_cache_between_pages: bool = True
    """Release PaddlePaddle's GPU memory pool (``paddle.device.cuda.empty_cache``)
    after each page. The auto-growth allocator holds freed-but-cached blocks
    (e.g. 7.6GB held while only 2GB used after one page), so without this the
    pool fragments and later pages OOM. No-op when paddle/CUDA is unavailable."""

    # ------------------------------------------------------------------ #
    # Alignment / matching (§1)                                          #
    # ------------------------------------------------------------------ #

    use_center_containment: bool = True
    """Primary match: an embedded word matches an OCR box when its center point
    falls inside the OCR box (enables one-to-many grouping)."""

    iou_floor: float = 0.30
    """Fallback match: an embedded↔OCR pair with axis-aligned IoU at or above
    this matches when no center is contained."""

    # ------------------------------------------------------------------ #
    # Normalization + tolerance (§3)                                     #
    # ------------------------------------------------------------------ #

    fold_case: bool = True
    """Casefold both sides before comparison (OCR casing is unreliable)."""

    fold_internal_spaces: bool = True
    """Drop all internal spaces in the one-to-many joined comparison."""

    ocr_confusion_classes: tuple[tuple[str, ...], ...] = field(
        default_factory=_default_confusion_classes
    )
    """Visually-confusable forms folded to a canonical class on both sides."""

    prose_rel_tol: float = 0.15
    """Prose tolerance: allowed edits = max(prose_floor_edits, floor(len*this))."""

    prose_floor_edits: int = 1
    """Minimum allowed edits for a (short) prose token before MISMATCH."""

    # CRITICAL INVERSION (§3c): high-value tokens get STRICTER tolerance —
    # a single-char delta in an amount/date IS the signal, not noise.
    amount_allowed_edits: int = 0
    """Allowed edits for an AMOUNT token after confusion-folding (strict)."""

    date_allowed_edits: int = 0
    """Allowed edits for a DATE token after confusion-folding (strict)."""

    id_strict: bool = False
    """When True, ID tokens use zero tolerance like amounts/dates. Default False:
    the ID_LIKE classifier (any 6+-char alphanumeric run) matches most ordinary
    prose words on a real invoice, so zero tolerance treats routine OCR
    word-boundary noise as a high-value divergence. ID is Stage 1's own
    documented WEAK booster — give it a real tolerance like prose, never the
    zero tolerance reserved for amount/date."""

    id_rel_tol: float = 0.15
    """Relative tolerance for ID tokens when ``id_strict`` is False (IDs are the
    noisiest high-value class; relax only here, never amounts/dates). Matches
    ``prose_rel_tol`` — IDs get prose-grade tolerance, not prose-grade weight
    (``mult_id`` still elevated)."""

    amount_requires_monetary_context: bool = True
    """When True, a bare 1-2 digit integer with no currency symbol/word, decimal
    point, or thousands separator is NOT treated as AMOUNT-elevating for the
    group/unmatched class decision (it is demoted to PROSE there) — see
    ``normalize.is_monetary_amount``. On invoices a lone small integer is far
    more often a quantity/term/index than a monetary amount; letting it
    elevate a whole joined line to AMOUNT's zero tolerance produces false
    MISMATCHes from unrelated 1-char OCR noise elsewhere in the line (see
    docs/STAGE3_LONG_PDF_FALSE_POSITIVE.md). Local to Stage 3 only — does NOT
    change ``revision_recovery.highvalue``."""

    # ------------------------------------------------------------------ #
    # False-positive guards (§4)                                         #
    # ------------------------------------------------------------------ #

    clip_margin_px: float = 2.0
    """Slack (px) on the page rect for the off-page clipping guard (§4a)."""

    ocr_conf_floor: float = 0.50
    """Drop OCR words below this confidence before matching (§4b)."""

    # ------------------------------------------------------------------ #
    # Routing — scanned / text-sparse short-circuit (§5)                 #
    # ------------------------------------------------------------------ #

    min_embedded_words: int = 10
    """Below this many embedded words the PDF is treated as scanned/text-sparse
    → INCONCLUSIVE + hand-off to image forensics."""

    embedded_ocr_ratio_floor: float = 0.10
    """Also scanned when embedded_count < this * ocr_count (≈ OCR-only)."""

    image_forensics_route: str = "image_forensics"
    """``routed_to`` value set on the scanned short-circuit."""

    # ------------------------------------------------------------------ #
    # Divergence base weights (§2) and token multipliers (§6a)           #
    # ------------------------------------------------------------------ #

    weight_mismatch: float = 1.0
    weight_ocr_only: float = 0.7
    weight_embedded_only: float = 0.6
    weight_agree: float = 0.0

    mult_amount: float = 3.0
    mult_date: float = 3.0
    mult_id: float = 1.5
    mult_prose: float = 1.0

    # ------------------------------------------------------------------ #
    # Scoring thresholds + score values (§6)                             #
    # ------------------------------------------------------------------ #

    medium_divergence_mass: float = 2.0
    """Divergence mass at/above which non-high-value divergence reaches MEDIUM
    (absolute floor — see ``divergence_mass_ratio`` for the relative floor)."""

    divergence_mass_ratio: float = 0.02
    """Relative MEDIUM floor: mass must also be >= this fraction of
    ``compared_words`` (matched groups + unmatched on both sides) to reach
    MEDIUM. The MEDIUM gate is
    ``mass >= max(medium_divergence_mass, divergence_mass_ratio * compared_words)``.
    Without this, a long clean document's steady-state per-page OCR noise
    (e.g. a repeated header on every page) accumulates past the absolute floor
    purely from page count, not from any real anomaly (see
    docs/STAGE3_LONG_PDF_FALSE_POSITIVE.md). ``compared_words`` defaults to 0
    in ``score()`` so existing direct unit-test calls keep the absolute-floor
    behaviour."""

    repeated_orphan_cap: int = 2
    """Cap on how many times an identical (folded-text) EMBEDDED_ONLY/OCR_ONLY
    orphan contributes its full weight to the divergence mass. A single orphan
    text repeated once per page (a logo/header/footer the OCR engine sees but
    the embedded layer doesn't, or vice versa) is one systematic extraction
    artifact, not N independent anomalies — without this cap it scales mass
    linearly with page count."""

    score_high_amount_date_mismatch: int = 95
    """HIGH score for an AMOUNT/DATE render-divergence MISMATCH (90-100). ID is
    intentionally excluded from HIGH-originating MISMATCHes (Stage 1's own
    "ID = weak/noisy" rule) — an ID MISMATCH still contributes its weight to the
    divergence mass feeding MEDIUM/LOW."""

    score_high_value_orphan: int = 75
    """HIGH score for a CORROBORATED high-value (AMOUNT/DATE only — never ID)
    OCR_ONLY/EMBEDDED_ONLY orphan (70-85). See ``min_high_value_orphans``: a
    single such orphan is the expected steady-state OCR noise on a clean page
    and scores MEDIUM instead, not HIGH."""

    min_high_value_orphans: int = 2
    """Number of high-value (AMOUNT/DATE) orphans required before they
    originate HIGH on their own. Below this floor, an uncorroborated lone
    orphan still scores MEDIUM (review) unless an accompanying high-value
    MISMATCH corroborates it directly."""

    score_medium_default: int = 50
    """MEDIUM base score for prose mismatch / overlay-cluster divergence, and
    for an uncorroborated lone high-value orphan."""

    score_low_default: int = 15
    """LOW score: sparse residual OCR-noise divergence only."""

    # ------------------------------------------------------------------ #
    # Localization                                                        #
    # ------------------------------------------------------------------ #

    enable_localization: bool = True
    """When True, ``analyze_bytes`` records per-page pixel dimensions so the
    aggregate layer can normalize pixel-space bboxes into canonical [0,1] form."""

    localize_include_ocr_box: bool = True
    """For MISMATCH divergences, union the OCR box into the localization rect
    (gives the full disagreeing region).  When False, box only the embedded side."""


__all__ = ["OCRCrossCheckConfig"]
