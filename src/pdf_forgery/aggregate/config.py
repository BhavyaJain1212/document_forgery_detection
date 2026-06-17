"""Configuration for the Stage 6 aggregate / advisory layer.

Every tunable lives here — nothing magic is hard-coded outside it. The headline
fusion is delegated to the existing :class:`~pdf_forgery.fusion.FusionConfig`
(Stage 6 adds no new fusion math this slice); this config wraps it and adds the
advisory toggles and the allow-list reference.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..fusion import FusionConfig
from .models import ADVISORY_FINDING_ALLOWLIST


@dataclass
class AggregateConfig:
    """Tunables for rolling stage results up and producing the advisory."""

    fusion: FusionConfig = field(default_factory=FusionConfig)
    """The existing fusion rule used for the headline tier/score. Full fusion
    (cross-stage geometric correlation, dedup, calibration) is deferred."""

    advisory_enabled: bool = True
    """When ``False``, the aggregate is produced without calling any model."""

    advisory_engine: str = "stub"
    """Which :class:`~pdf_forgery.aggregate.advisory.AdvisoryEngine` to use —
    ``"stub"`` (deterministic, no GPU; the default so the pipe runs anywhere) or
    ``"local_llm"`` (GPU, swappable)."""

    advisory_model: str = "llama3.1"
    """Model tag the ``local_llm`` engine asks the local server (Ollama) to run.
    Ignored by ``stub``. The engine degrades gracefully when this tag is absent."""

    advisory_base_url: str = "http://localhost:11434"
    """Base URL of the local Ollama server backing ``local_llm``. Never reached
    by ``stub``; the engine treats an unreachable server as "unavailable"."""

    finding_allowlist: tuple[str, ...] = ADVISORY_FINDING_ALLOWLIST
    """The PHI boundary allow-list (exposed so it is auditable in one place)."""

    log_salt: str = ""
    """Salt for :func:`~pdf_forgery.aggregate.safe_log.salted_hash`. Set per
    deployment; empty in tests."""


__all__ = ["AggregateConfig"]
