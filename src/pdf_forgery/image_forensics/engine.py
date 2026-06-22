"""Engine / method abstraction for Stage 6 (§3) — the swappable forensics layer.

Mirrors the ``ocr_crosscheck.OCREngine`` pattern: downstream code depends ONLY on
the Protocols here, the provider is swappable, optional deps import lazily and
degrade via ``is_available()``. Three providers ship:

* :class:`ClassicalProvider` — the **default**, CPU (OpenCV / numpy / scipy /
  Pillow). No torch. Its method set (ELA, double-JPEG/DQ, JPEG-grid,
  noise-residual, copy-move) is real — the per-pixel math lives in
  :mod:`.classical`; each method here just binds a name + that function.
* :class:`PhotoHolmesProvider` — **optional, opt-in**. Lazy-imports PhotoHolmes +
  torch; ``is_available()`` is ``False`` when either is absent (exactly the
  PaddleOCR availability pattern). Its DL methods stay behind ``enable_dl_methods``
  + a VRAM guard; TruFor is never enabled (non-profit license).
* :class:`StubForensicProvider` — deterministic, seeded by the image content
  hash, CPU, always available. Lets the whole stage be exercised with no
  skimage / OpenCV / torch.

PHI: a :class:`ForensicMap` carries a real heatmap of document pixels — PHI.
Nothing here logs the array; only its scalar / shape / method name are loggable
(§8). Versions + device + library versions go into :class:`ForensicProvenance`
for the reproducibility manifest (§9).

The classical methods are fully implemented (Session 6.4). The PhotoHolmes
provider stays opt-in and lazy; the Stub provider's ``analyze`` is deterministic
so the stage can be exercised with no heavy deps.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from . import classical
from .config import ImageForensicsConfig
from .images import DecodedImage

if TYPE_CHECKING:  # pragma: no cover - typing only
    import numpy as np


# --------------------------------------------------------------------------- #
# Result + provenance records
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class ForensicMap:
    """One method's output in the image's pixel grid (§3).

    ``heatmap`` is a float ndarray in ``[0, 1]`` (per-pixel suspicion), or ``None``
    for a scalar-only method. ``scalar`` is an optional global score. ``params``
    records the exact thresholds applied (for the manifest). PHI: the heatmap is
    document pixels — never logged; only ``scalar`` / shape / ``method`` are.
    """

    method: str
    version: str
    # The heatmap is per-pixel document data = PHI; excluded from the repr so it
    # cannot leak via a log line. Only ``scalar`` / shape / ``method`` are loggable.
    heatmap: "np.ndarray | None" = field(default=None, repr=False)
    scalar: float | None = None
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ForensicProvenance:
    """Reproducibility manifest for one run (§9), carried on the payload.

    Records the provider identity + device, the library versions actually used,
    the method (name, version) pairs run, and whether DL methods were enabled —
    enough to reproduce a result without retaining any pixels.
    """

    provider: str
    device: str
    library_versions: dict[str, str] = field(default_factory=dict)
    methods: tuple[tuple[str, str], ...] = ()      # (name, version)
    enable_dl_methods: bool = False


# --------------------------------------------------------------------------- #
# Protocols (the only thing downstream depends on)
# --------------------------------------------------------------------------- #

@runtime_checkable
class ForensicMethod(Protocol):
    """A single forensic method (ELA, double-JPEG, …) over one decoded image."""

    name: str          # canonical, space-free token, e.g. "double_jpeg"
    version: str

    def applicable(self, image: DecodedImage, cfg: ImageForensicsConfig) -> bool:
        """Whether this method applies to ``image`` (e.g. DQ needs JPEG source)."""
        ...

    def analyze(self, image: DecodedImage, cfg: ImageForensicsConfig) -> ForensicMap:
        """Heatmap (+ optional scalar) in the image's pixel grid. Never raises."""
        ...


@runtime_checkable
class ForensicProvider(Protocol):
    """A swappable bundle of forensic methods (classical / PhotoHolmes / stub)."""

    name: str
    device: str

    def is_available(self) -> bool:
        """True when the backend's deps are importable and usable."""
        ...

    def methods(self, cfg: ImageForensicsConfig) -> list[ForensicMethod]:
        """The methods this provider offers under ``cfg`` (DL gated here)."""
        ...

    def provenance(self, cfg: ImageForensicsConfig) -> ForensicProvenance:
        """Identity + device + library/method versions for the manifest."""
        ...


# --------------------------------------------------------------------------- #
# Classical provider (default, CPU) — methods stubbed for 6.1
# --------------------------------------------------------------------------- #

class _ClassicalMethod:
    """Base for the classical CPU methods (§4 method set).

    Each subclass binds ``name`` + a ``_fn`` from :mod:`.classical` that returns a
    ``[0,1]`` heatmap (or ``None`` when the method has too little data to apply).
    ``analyze`` wraps it in a :class:`ForensicMap`. A heatmap of ``None`` is "ran,
    found nothing" — NOT an error: it produces no fire (→ LOW), distinct from a
    genuine exception (caught upstream in :mod:`.detect` → MEDIUM)."""

    name = "classical_method"
    version = "1.0.0"
    requires_jpeg = False
    #: staticmethod(image, cfg) -> heatmap | None, bound by each subclass.
    _fn = None

    def applicable(self, image: DecodedImage, cfg: ImageForensicsConfig) -> bool:
        if image.pixels is None and not image.is_jpeg:
            return False
        if self.requires_jpeg and not image.is_jpeg:
            return False
        return True

    def analyze(self, image: DecodedImage, cfg: ImageForensicsConfig) -> ForensicMap:
        heatmap = type(self)._fn(image, cfg)
        return ForensicMap(
            method=self.name,
            version=self.version,
            heatmap=heatmap,
            scalar=None,
            params={"version": self.version},
        )


class ELAMethod(_ClassicalMethod):
    """Error-level analysis (recompress + difference). Heatmap. (§5)"""

    name = "ela"
    _fn = staticmethod(classical.ela_heatmap)


class DoubleJPEGMethod(_ClassicalMethod):
    """DCT double-quantisation / double-JPEG. JPEG source only. Heatmap. (§5)"""

    name = "double_jpeg"
    requires_jpeg = True
    _fn = staticmethod(classical.double_jpeg_heatmap)


class JPEGGridMethod(_ClassicalMethod):
    """8×8 JPEG-grid alignment (ZERO-style). JPEG source only. (§5)"""

    name = "jpeg_grid"
    requires_jpeg = True
    _fn = staticmethod(classical.jpeg_grid_heatmap)


class NoiseResidualMethod(_ClassicalMethod):
    """Noise / residual inconsistency (Splicebuster/Noisesniffer-style). (§5)"""

    name = "noise_inconsistency"
    _fn = staticmethod(classical.noise_heatmap)


class CopyMoveMethod(_ClassicalMethod):
    """Copy-move (ORB keypoints + RANSAC affine). Conservative; never HIGH alone."""

    name = "copy_move"
    _fn = staticmethod(classical.copy_move_heatmap)


class ClassicalProvider:
    """Default CPU provider — skimage / OpenCV / numpy. Always tries to be available.

    Availability requires only the always-present hard deps (``numpy``, ``cv2``,
    ``PIL``); ``skimage`` is optional and only sharpens a subset of methods.
    """

    name = "classical"
    device = "cpu"

    def is_available(self) -> bool:
        """True when the classical CPU stack (numpy + OpenCV + Pillow) imports."""
        return all(_can_import(m) for m in ("numpy", "cv2", "PIL"))

    def methods(self, cfg: ImageForensicsConfig) -> list[ForensicMethod]:
        return [
            ELAMethod(),
            DoubleJPEGMethod(),
            JPEGGridMethod(),
            NoiseResidualMethod(),
            CopyMoveMethod(),
        ]

    def provenance(self, cfg: ImageForensicsConfig) -> ForensicProvenance:
        return ForensicProvenance(
            provider=self.name,
            device=self.device,
            library_versions=_library_versions("numpy", "cv2", "PIL", "skimage", "scipy"),
            methods=tuple((m.name, m.version) for m in self.methods(cfg)),
            enable_dl_methods=False,
        )


# --------------------------------------------------------------------------- #
# PhotoHolmes provider (optional, opt-in) — unavailable here, degrades cleanly
# --------------------------------------------------------------------------- #

class PhotoHolmesProvider:
    """Optional PhotoHolmes wrapper. Lazy import; degrades when torch is absent.

    Mirrors ``PaddleOCREngine``: importing this module never fails when
    PhotoHolmes / torch are missing; ``is_available()`` reports it and
    :meth:`methods` returns ``[]`` so the stage falls back to classical-only.

    Its classical methods (DQ / ZERO / Splicebuster / Noisesniffer, Apache-2.0)
    are always offered when available; its DL methods (CAT-Net / PSCC-Net /
    FOCAL) only when ``cfg.enable_dl_methods`` AND the VRAM guard passes. TruFor
    is never offered (non-profit license).
    """

    name = "photoholmes"

    def __init__(self, *, device: str = "cuda:0") -> None:
        self.device = device

    def is_available(self) -> bool:
        """True only when both ``photoholmes`` and ``torch`` import."""
        return _can_import("photoholmes") and _can_import("torch")

    def methods(self, cfg: ImageForensicsConfig) -> list[ForensicMethod]:
        if not self.is_available():
            return []
        # Real method wiring (classical + gated DL set) lands in 6.3; until then
        # an available PhotoHolmes still offers nothing rather than half a wrap.
        return []

    def provenance(self, cfg: ImageForensicsConfig) -> ForensicProvenance:
        return ForensicProvenance(
            provider=self.name,
            device=self.device if self.is_available() else "unavailable",
            library_versions=_library_versions("photoholmes", "torch"),
            methods=tuple((m.name, m.version) for m in self.methods(cfg)),
            enable_dl_methods=bool(cfg.enable_dl_methods),
        )

    def dl_vram_ok(self, cfg: ImageForensicsConfig) -> bool:
        """Whether enough free VRAM exists to instantiate a DL method (§3).

        Requires ``>= cfg.dl_min_free_vram_mb`` free. Returns ``False`` (degrade
        to classical-only) when torch/CUDA is unavailable or the query fails —
        never raises.
        """
        if not cfg.enable_dl_methods:
            return False
        try:
            import torch

            if not torch.cuda.is_available():
                return False
            free_bytes, _total = torch.cuda.mem_get_info()
            return free_bytes >= cfg.dl_min_free_vram_mb * 1024 * 1024
        except Exception:
            return False


# --------------------------------------------------------------------------- #
# Stub provider (tests) — deterministic, always available, no heavy deps
# --------------------------------------------------------------------------- #

class _StubMethod:
    """A deterministic method seeded by the image content hash."""

    def __init__(self, name: str, *, requires_jpeg: bool = False) -> None:
        self.name = name
        self.version = "stub-1.0"
        self.requires_jpeg = requires_jpeg

    def applicable(self, image: DecodedImage, cfg: ImageForensicsConfig) -> bool:
        if self.requires_jpeg and not image.is_jpeg:
            return False
        return True

    def analyze(self, image: DecodedImage, cfg: ImageForensicsConfig) -> ForensicMap:
        """Return a deterministic canned heatmap + scalar (never raises)."""
        seed = int(hashlib.sha256(
            (self.name + image.content_hash).encode("utf-8")
        ).hexdigest()[:8], 16)
        heatmap = None
        scalar = (seed % 1000) / 1000.0
        try:
            import numpy as np

            rng = np.random.default_rng(seed)
            heatmap = rng.random((32, 32), dtype=np.float64)
        except Exception:
            heatmap = None
        return ForensicMap(
            method=self.name,
            version=self.version,
            heatmap=heatmap,
            scalar=scalar,
            params={"seed": seed},
        )


class StubForensicProvider:
    """Deterministic CPU provider for tests — always available, no real math."""

    name = "stub"
    device = "cpu"

    def __init__(self, method_names: tuple[str, ...] | None = None) -> None:
        self._method_names = method_names or (
            "ela",
            "double_jpeg",
            "noise_inconsistency",
        )

    def is_available(self) -> bool:
        return True

    def methods(self, cfg: ImageForensicsConfig) -> list[ForensicMethod]:
        return [
            _StubMethod(name, requires_jpeg=(name in ("double_jpeg", "jpeg_grid")))
            for name in self._method_names
        ]

    def provenance(self, cfg: ImageForensicsConfig) -> ForensicProvenance:
        return ForensicProvenance(
            provider=self.name,
            device=self.device,
            library_versions=_library_versions("numpy"),
            methods=tuple((m.name, m.version) for m in self.methods(cfg)),
            enable_dl_methods=False,
        )


# --------------------------------------------------------------------------- #
# Selection + import helpers
# --------------------------------------------------------------------------- #

def default_provider(cfg: ImageForensicsConfig | None = None) -> ForensicProvider:
    """The production default — classical CPU when available, else the stub.

    PhotoHolmes is never the default (opt-in only); the stub is the last-resort
    fallback so the stage always has a working provider.
    """
    classical = ClassicalProvider()
    if classical.is_available():
        return classical
    return StubForensicProvider()


def _can_import(module: str) -> bool:
    import importlib.util

    try:
        return importlib.util.find_spec(module) is not None
    except Exception:
        return False


def _library_versions(*modules: str) -> dict[str, str]:
    """Best-effort ``{module: version}`` for the manifest; absent → ``"absent"``."""
    import importlib.metadata

    # Distribution names differ from import names for a few libs.
    dist = {"cv2": "opencv-python", "PIL": "pillow", "skimage": "scikit-image"}
    out: dict[str, str] = {}
    for mod in modules:
        if not _can_import(mod):
            out[mod] = "absent"
            continue
        try:
            out[mod] = importlib.metadata.version(dist.get(mod, mod))
        except Exception:
            out[mod] = "unknown"
    return out


__all__ = [
    "ForensicMap",
    "ForensicProvenance",
    "ForensicMethod",
    "ForensicProvider",
    "ELAMethod",
    "DoubleJPEGMethod",
    "JPEGGridMethod",
    "NoiseResidualMethod",
    "CopyMoveMethod",
    "ClassicalProvider",
    "PhotoHolmesProvider",
    "StubForensicProvider",
    "default_provider",
]
