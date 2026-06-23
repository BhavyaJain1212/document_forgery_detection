"""Classical (CPU) pixel-forensics math for Stage 6 (§4 classical-first set).

This is the real signal processing behind :class:`engine.ClassicalProvider`. Each
public ``*_heatmap`` function takes a :class:`images.DecodedImage` and returns a
per-pixel / per-block suspicion map as a float ndarray in ``[0, 1]`` (or ``None``
when the method does not apply / has too little data), in the image's own grid.
Localization (:mod:`.localize`) works in *fractional* coordinates, so a
block-resolution map is sufficient and cheaper than a full-resolution one.

Critical pixel source (design §2): the math runs on the ORIGINAL embedded raster
— ``DecodedImage.pixels`` (decoded once from the embedded XObject) and, for the
JPEG methods, ``DecodedImage.jpeg_bytes`` (the verbatim DCTDecode stream). It is
NEVER the 300-DPI page re-render: re-rasterisation is a second compression that
erases the quantisation / JPEG-grid evidence the DQ / grid methods depend on.

No ``jpegio`` / ``torchjpeg`` C extension is required (neither installs cleanly in
the restricted-network environment). The JPEG methods read the quantisation table
straight from the original stream via Pillow (``Image.quantization``) and recover
the block DCT coefficients with ``scipy.fftpack`` over the original JPEG's decoded
luminance — i.e. the coefficients of the *original* compressed image, not a
re-encode. (Recomputed-DCT vs entropy-decoded coefficients differ only by IDCT
rounding/clipping; adequate for localization. Reading entropy-coded coefficients
directly would be marginally sharper — flagged as an owner-calibration item.)

Normalization is RELATIVE (robust z-score against the image's own median), so a
clean, uniformly compressed page stays dark everywhere (precision) while a genuine
local anomaly — a spliced patch with a different compression / noise history —
lights up only over its region (recall). A uniform whole-image recompression has
no positive outlier (the median IS the recompression level) → no local fire, which
is exactly the "innocent rescan" the design must not call HIGH.

PHI: every array here is document pixels. Nothing is logged; only the returned
heatmap (consumed in-process by :mod:`.detect`) and never a raw crop.
"""

from __future__ import annotations

import io
from typing import TYPE_CHECKING

from .config import ImageForensicsConfig
from .images import DecodedImage

if TYPE_CHECKING:  # pragma: no cover - typing only
    import numpy as np

# JPEG zig-zag scan order: ZIGZAG[k] is the natural (row*8+col) index of the
# k-th coefficient as stored in a quantisation table. Used to un-zigzag Pillow's
# ``Image.quantization`` (which is in scan order) into a natural 8×8 grid.
_ZIGZAG = (
    0, 1, 8, 16, 9, 2, 3, 10,
    17, 24, 32, 25, 18, 11, 4, 5,
    12, 19, 26, 33, 40, 48, 41, 34,
    27, 20, 13, 6, 7, 14, 21, 28,
    35, 42, 49, 56, 57, 50, 43, 36,
    29, 22, 15, 23, 30, 37, 44, 51,
    58, 59, 52, 45, 38, 31, 39, 46,
    53, 60, 61, 54, 47, 55, 62, 63,
)


# --------------------------------------------------------------------------- #
# Public methods (one per §5 classical method)
# --------------------------------------------------------------------------- #

def ela_heatmap(
    image: DecodedImage, cfg: ImageForensicsConfig
) -> "np.ndarray | None":
    """Error-level analysis: re-save at a known quality, map the local error rise.

    A region pasted from a different compression history reaches a different error
    floor on recompression than its host → its block error stands out as a local
    positive outlier. Edges are ELA-bright by nature, so we threshold on *local
    contrast* (robust z-score over blocks), never absolute error (§5 FP note).
    """
    import numpy as np
    from PIL import Image

    rgb = _rgb(image)
    if rgb is None:
        return None
    pil = Image.fromarray(rgb).convert("RGB")
    buf = io.BytesIO()
    pil.save(buf, format="JPEG", quality=cfg.ela_quality)
    recompressed = np.asarray(Image.open(io.BytesIO(buf.getvalue())).convert("RGB"))
    orig = np.asarray(pil, dtype=np.float64)
    err = np.abs(orig - recompressed.astype(np.float64)).max(axis=2)
    block_err = _block_reduce(err, cfg.anomaly_block, np.mean)
    if block_err is None:
        return None
    hm = _anomaly_map(block_err, cfg.anomaly_z0, cfg.anomaly_slope)
    if not cfg.ela_structure_gate or hm is None:
        return hm

    # ELA is bright on ordinary glyph/rule edges by construction. Only retain
    # anomalous recompression error in low-structure paper blocks, where it is
    # meaningful evidence of a foreign compression/noise history.
    gray = _gray(image)
    if gray is None:
        return hm
    import cv2

    # Classify *document structure*, not fine paper grain / foreign noise. A
    # block-scale low-pass leaves glyph/rule/layout edges visible while treating
    # the noisy-but-flat synthetic splice interior as flat paper.
    kernel = max(3, cfg.anomaly_block * 2 - 1)
    low = cv2.GaussianBlur(gray, (kernel, kernel), 0)
    flat = _flat_block_mask(low, cfg, cfg.ela_flat_percentile)
    if flat is None:
        return hm
    return hm * flat.astype(np.float64)


def noise_heatmap(
    image: DecodedImage, cfg: ImageForensicsConfig
) -> "np.ndarray | None":
    """Noise / residual inconsistency (Splicebuster/Noisesniffer-style).

    High-pass residual (image minus a 3×3 median) → per-block residual std. A
    spliced region carries a *different* noise level than the host (scanner
    denoising, added noise, a foreign source), so the anomaly is a deviation in
    EITHER direction → bidirectional robust z-score.
    """
    import cv2
    import numpy as np

    gray = _gray(image)
    if gray is None:
        return None
    g8 = np.clip(gray, 0, 255).astype(np.uint8)
    low = cv2.medianBlur(g8, 3).astype(np.float64)        # edge-preserving low-pass
    residual = gray - low                                  # noise + edge leakage
    block_std = _block_reduce(residual, cfg.anomaly_block, np.std)
    if block_std is None:
        return None

    # Flat-block gate (Noisesniffer principle, §5 FP note): noise can only be
    # estimated on LOW-structure blocks — an edge / line / glyph inflates the
    # residual without being a noise anomaly. Suppress textured blocks so the
    # method fires on a foreign noise level in flat paper, not on legitimate
    # structure (this is what keeps a lined / texty clean scan out of MEDIUM).
    hm = _anomaly_map(block_std, cfg.noise_z0, cfg.anomaly_slope, bidirectional=True)
    # The median pass above deliberately preserves edges for the residual. The
    # structure gate has a different job: blur at block scale so it sees printed
    # layout, not the very foreign paper grain the detector is meant to retain.
    kernel = max(3, cfg.anomaly_block * 2 - 1)
    structure_low = cv2.GaussianBlur(gray, (kernel, kernel), 0)
    flat = _flat_block_mask(structure_low, cfg, cfg.noise_flat_percentile)
    if hm is None or flat is None:
        return hm
    return hm * flat.astype(np.float64)


def double_jpeg_heatmap(
    image: DecodedImage, cfg: ImageForensicsConfig
) -> "np.ndarray | None":
    """DCT double-quantisation / double-JPEG (JPEG source only).

    For every 8×8 luminance block of the ORIGINAL JPEG, measure how well its DCT
    coefficients lie on the host quantisation lattice: ``D/q - round(D/q)``. A
    block that came through a *different* JPEG history (a splice, a locally
    re-compressed edit) does not sit on the host's lattice → a higher quantisation
    misfit → a local positive outlier. A uniformly (singly OR whole-image doubly)
    compressed page has a flat misfit field → no local fire (the innocent-rescan
    guard). Uses the quant table read verbatim from the stream + a recomputed
    block DCT of the original luminance (§2 — no page re-render).
    """
    import numpy as np

    if image.jpeg_bytes is None:
        return None
    luma_q = _jpeg_luma_qtable(image.jpeg_bytes)
    if luma_q is None:
        return None
    luma, qtable = luma_q
    blocks, nby, nbx = _to_8x8_blocks(luma)
    if blocks is None:
        return None
    coeffs = _blockwise_dct(blocks)                      # (N, 8, 8)
    q = qtable.reshape(1, 8, 8)
    ratio = coeffs / q
    misfit = np.abs(ratio - np.round(ratio))             # in [0, 0.5]
    ac = np.ones((8, 8), dtype=bool)
    ac[0, 0] = False                                     # ignore DC
    block_misfit = misfit[:, ac].mean(axis=1).reshape(nby, nbx)
    hm = _anomaly_map(block_misfit, cfg.dq_z0, cfg.anomaly_slope)
    if not cfg.dq_structure_gate or hm is None:
        return hm

    # Recomputed coefficients include IDCT rounding/clipping error, which is
    # strongly content-correlated on glyph/rule edges. At DQ's native 8x8 grid,
    # retain only low-structure paper blocks where lattice misfit can distinguish
    # a foreign/local compression history from ordinary document content.
    gray = _gray(image)
    if gray is None:
        return hm
    import cv2

    kernel = max(3, 8 * 2 - 1)
    low = cv2.GaussianBlur(gray, (kernel, kernel), 0)
    flat = _flat_block_mask(low, cfg, cfg.dq_flat_percentile, block=8)
    if flat is None or flat.shape != hm.shape:
        return hm
    return hm * flat.astype(np.float64)


def jpeg_grid_heatmap(
    image: DecodedImage, cfg: ImageForensicsConfig
) -> "np.ndarray | None":
    """JPEG 8×8 blocking-grid alignment (ZERO-style; JPEG source only).

    Genuine JPEG content has its strongest pixel discontinuities exactly on the
    8×8 grid. A spliced / overlaid region whose own grid is misaligned with the
    host has weak on-grid blockiness (or strong off-grid blockiness). We map the
    *deficit* of on-grid boundary energy per block as the anomaly — a region that
    breaks the host grid stands out. Distinct signal from :func:`double_jpeg_heatmap`
    (boundary energy vs coefficient lattice) so the two can co-locate as two
    independent fires (§7 HIGH gate).
    """
    import numpy as np

    if image.jpeg_bytes is None:
        return None
    gray = _gray(image)
    if gray is None:
        return None
    h, w = gray.shape
    if h < 24 or w < 24:
        return None
    dx = np.abs(np.diff(gray, axis=1))
    dy = np.abs(np.diff(gray, axis=0))
    # On-grid columns/rows are multiples of 8 (skip index 0). Build per-pixel
    # "on-grid boundary energy", then reduce to 8×8 blocks.
    col_grid = np.zeros((h, w), dtype=np.float64)
    row_grid = np.zeros((h, w), dtype=np.float64)
    col_grid[:, 8:w:8] = dx[:, 7:w - 1:8]
    row_grid[8:h:8, :] = dy[7:h - 1:8, :]
    on_grid = col_grid + row_grid
    block_energy = _block_reduce(on_grid, 8, np.sum)
    if block_energy is None:
        return None
    # Deficit: blocks with much LESS on-grid energy than typical break the host
    # grid. Invert so a deficit is the positive outlier (bidirectional would also
    # flag strong-blocking blocks; a deficit is the splice-relevant direction).
    deficit = -block_energy
    return _anomaly_map(deficit, cfg.jpeg_grid_z0, cfg.anomaly_slope)


def copy_move_heatmap(
    image: DecodedImage, cfg: ImageForensicsConfig
) -> "np.ndarray | None":
    """Copy-move duplication (ORB keypoints + RANSAC affine), conservative.

    Detects a region duplicated elsewhere on the page (a cloned stamp/signature, or
    paper cloned over a deleted line). ORB is deterministic for a given image;
    OpenCV's RANSAC RNG is seeded for reproducibility. A match cluster must have
    ``>= cfg.copy_move_min_matches`` RANSAC inliers AND a non-trivial translation
    (``>= cfg.copy_move_min_offset_frac`` of the image diagonal) to fire — repeated
    *legitimate* elements (logos, rules, identical glyphs) at small offsets are
    rejected. Marks BOTH the source and destination clusters in the heatmap. Never
    HIGH alone (§5/§7).
    """
    import cv2
    import numpy as np

    gray = _gray(image)
    if gray is None:
        return None
    h, w = gray.shape
    if h < 32 or w < 32:
        return None
    g8 = np.clip(gray, 0, 255).astype(np.uint8)
    cv2.setRNGSeed(_seed(image))

    orb = cv2.ORB_create(nfeatures=cfg.copy_move_orb_features)
    kp, des = orb.detectAndCompute(g8, None)
    if des is None or len(kp) < cfg.copy_move_min_matches * 2:
        return None

    pts = np.array([k.pt for k in kp], dtype=np.float64)
    diag = float(np.hypot(h, w))
    min_off = cfg.copy_move_min_offset_frac * diag

    bf = cv2.BFMatcher(cv2.NORM_HAMMING)
    knn = bf.knnMatch(des, des, k=3)
    src_idx: list[int] = []
    dst_idx: list[int] = []
    for cand in knn:
        for m in cand:
            if m.queryIdx == m.trainIdx:
                continue
            if np.hypot(*(pts[m.queryIdx] - pts[m.trainIdx])) < min_off:
                continue
            if m.distance > cfg.copy_move_orb_max_dist:
                continue
            src_idx.append(m.queryIdx)
            dst_idx.append(m.trainIdx)
            break
    if len(src_idx) < cfg.copy_move_min_matches:
        return None

    src = pts[src_idx]
    dst = pts[dst_idx]
    _model, inliers = cv2.estimateAffinePartial2D(
        src, dst, method=cv2.RANSAC, ransacReprojThreshold=3.0
    )
    if inliers is None:
        return None
    mask = inliers.ravel().astype(bool)
    if int(mask.sum()) < cfg.copy_move_min_matches:
        return None

    src_inliers = src[mask]
    dst_inliers = dst[mask]
    if (
        _cluster_span_frac(src_inliers, w, h)
        >= cfg.copy_move_max_cluster_span_frac
        or _cluster_span_frac(dst_inliers, w, h)
        >= cfg.copy_move_max_cluster_span_frac
    ):
        # A single translation repeated over a large part of the page is a
        # structural duplicate (multi-copy form/repeated layout), not a compact
        # cloned stamp or patched amount.
        return None

    # Build a block-resolution mask over both matched clusters.
    nby = max(1, h // cfg.anomaly_block)
    nbx = max(1, w // cfg.anomaly_block)
    heat = np.zeros((nby, nbx), dtype=np.float64)
    for p in np.vstack([src_inliers, dst_inliers]):
        bx = min(nbx - 1, int(p[0] / w * nbx))
        by = min(nby - 1, int(p[1] / h * nby))
        heat[by, bx] = 1.0
    # Dilate a touch so each cluster is a connected blob, not isolated dots.
    heat = cv2.dilate(heat, np.ones((3, 3), np.uint8))
    return np.clip(heat, 0.0, 1.0)


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #

def _rgb(image: DecodedImage) -> "np.ndarray | None":
    """Original decoded image as an ``H×W×3`` uint8 array, or ``None``."""
    import numpy as np

    px = image.pixels
    if px is None:
        return None
    arr = np.asarray(px)
    if arr.ndim == 2:
        arr = np.stack([arr] * 3, axis=-1)
    elif arr.ndim == 3 and arr.shape[2] == 1:
        arr = np.repeat(arr, 3, axis=2)
    elif arr.ndim != 3 or arr.shape[2] < 3:
        return None
    return arr[:, :, :3].astype(np.uint8)


def _gray(image: DecodedImage) -> "np.ndarray | None":
    """Original decoded image as an ``H×W`` float64 luminance, or ``None``."""
    import numpy as np

    px = image.pixels
    if px is None:
        return None
    arr = np.asarray(px).astype(np.float64)
    if arr.ndim == 2:
        return arr
    if arr.ndim == 3 and arr.shape[2] >= 3:
        r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]
        return 0.299 * r + 0.587 * g + 0.114 * b
    if arr.ndim == 3 and arr.shape[2] == 1:
        return arr[:, :, 0]
    return None


def _block_reduce(arr: "np.ndarray", block: int, func) -> "np.ndarray | None":
    """Reduce ``arr`` (2D) to a per-``block``×``block`` grid via ``func``."""
    import numpy as np

    h, w = arr.shape
    if h < block or w < block:
        return None
    h2, w2 = (h // block) * block, (w // block) * block
    a = arr[:h2, :w2].reshape(h2 // block, block, w2 // block, block)
    return func(a, axis=(1, 3))


def _flat_block_mask(
    lowpass_luma: "np.ndarray",
    cfg: ImageForensicsConfig,
    percentile: float,
    *,
    block: int | None = None,
) -> "np.ndarray | None":
    """Return the lowest-structure block mask for document anomaly estimation.

    ``lowpass_luma`` is already method-appropriate low-pass luminance. Keeping
    the Sobel/block reduction here gives ELA, DQ, and noise one definition of a
    flat document block while allowing each method to use its native grid.
    """
    import cv2
    import numpy as np

    gx = cv2.Sobel(lowpass_luma, cv2.CV_64F, 1, 0, ksize=3)
    gy = cv2.Sobel(lowpass_luma, cv2.CV_64F, 0, 1, ksize=3)
    block_size = cfg.anomaly_block if block is None else block
    structure = _block_reduce(np.abs(gx) + np.abs(gy), block_size, np.mean)
    if structure is None:
        return None
    return structure <= np.quantile(structure, percentile)


def _cluster_span_frac(points: "np.ndarray", width: int, height: int) -> float:
    """Normalized bounding-box area spanned by a copy-move inlier cluster."""
    import numpy as np

    pts = np.asarray(points, dtype=np.float64)
    if pts.size == 0 or width <= 0 or height <= 0:
        return 0.0
    width_frac = float(np.ptp(pts[:, 0])) / float(width)
    height_frac = float(np.ptp(pts[:, 1])) / float(height)
    return width_frac * height_frac


def _anomaly_map(
    block_measure: "np.ndarray",
    z0: float,
    slope: float,
    *,
    bidirectional: bool = False,
) -> "np.ndarray | None":
    """Robust-z-score a per-block measure into a ``[0,1]`` suspicion heatmap.

    ``z = (m - median) / (1.4826·MAD)``; the map is ``sigmoid((z - z0)·slope)`` so
    only blocks several MAD above the image's own median (a genuine local anomaly)
    light up, while a clean / uniformly compressed page — symmetric around its
    median — stays dark. ``bidirectional`` (noise) flags deviations either side.
    """
    import numpy as np

    m = np.asarray(block_measure, dtype=np.float64)
    if m.size == 0:
        return None
    med = float(np.median(m))
    mad = float(np.median(np.abs(m - med))) * 1.4826
    if mad < 1e-9:
        # No spread → nothing is an outlier (a flat / uniform field). Dark map.
        return np.zeros_like(m)
    z = (m - med) / mad
    if bidirectional:
        z = np.abs(z)
    # Clipping is numerically equivalent at float precision and avoids overflow
    # warnings on extremely strong real-document outliers.
    logit = np.clip((z - z0) * slope, -700.0, 700.0)
    return 1.0 / (1.0 + np.exp(-logit))


# --------------------------------------------------------------------------- #
# JPEG helpers (DCT coefficients + quant table from the ORIGINAL stream)
# --------------------------------------------------------------------------- #

def _jpeg_luma_qtable(
    jpeg_bytes: bytes,
) -> "tuple[np.ndarray, np.ndarray] | None":
    """Decode the original JPEG to luminance + its natural-order Y quant table.

    Reads the quantisation table verbatim from the stream (Pillow
    ``Image.quantization``, in zig-zag scan order) and un-zigzags it to an 8×8
    grid matching :func:`_blockwise_dct`'s natural layout. Returns ``None`` when
    the table is unavailable (e.g. not a baseline JPEG).
    """
    import numpy as np
    from PIL import Image

    try:
        im = Image.open(io.BytesIO(jpeg_bytes))
        quant = getattr(im, "quantization", None)
        ycc = im.convert("YCbCr")
    except Exception:
        return None
    if not quant or 0 not in quant or len(quant[0]) < 64:
        return None
    luma = np.asarray(ycc, dtype=np.float64)[:, :, 0]
    scan = list(quant[0])[:64]
    qtable = np.empty(64, dtype=np.float64)
    for k, natural in enumerate(_ZIGZAG):
        qtable[natural] = float(scan[k])
    return luma, qtable.reshape(8, 8)


def _to_8x8_blocks(luma: "np.ndarray") -> "tuple[np.ndarray | None, int, int]":
    """Split an ``H×W`` luminance into a stack of 8×8 blocks (``N×8×8``)."""
    import numpy as np

    h, w = luma.shape
    if h < 8 or w < 8:
        return None, 0, 0
    nby, nbx = h // 8, w // 8
    cropped = luma[: nby * 8, : nbx * 8]
    blocks = (
        cropped.reshape(nby, 8, nbx, 8)
        .transpose(0, 2, 1, 3)
        .reshape(nby * nbx, 8, 8)
    )
    return blocks, nby, nbx


def _blockwise_dct(blocks: "np.ndarray") -> "np.ndarray":
    """Orthonormal 2-D DCT of each centred 8×8 block (``N×8×8`` → ``N×8×8``)."""
    import numpy as np
    from scipy.fftpack import dct

    centred = blocks.astype(np.float64) - 128.0
    tmp = dct(centred, type=2, norm="ortho", axis=1)
    return dct(tmp, type=2, norm="ortho", axis=2)


def _seed(image: DecodedImage) -> int:
    """Deterministic 31-bit seed from the image content hash (RANSAC RNG)."""
    return int(image.content_hash[:8], 16) & 0x7FFFFFFF


__all__ = [
    "ela_heatmap",
    "noise_heatmap",
    "double_jpeg_heatmap",
    "jpeg_grid_heatmap",
    "copy_move_heatmap",
]
