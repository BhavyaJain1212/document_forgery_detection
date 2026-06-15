"""Shared, stage-agnostic data types for the multi-stage detection pipeline.

Every detection stage (revision recovery, and later font fingerprinting / OCR
cross-check) speaks the SAME small vocabulary defined here so their outputs can
be collected and later fused into one report:

    ConfidenceTier  — the advisory verdict band (INCONCLUSIVE / LOW / MEDIUM / HIGH)
    Evidence        — one granular before -> after change supporting a finding
    Finding         — one flagged change, with a per-finding tier
    StageResult     — everything one stage produced for one PDF

These are PURE data (frozen dataclasses / enum). They carry no detection logic;
each stage maps its own rich internal models onto this schema via an adapter so
the orchestrator never needs to know a stage's internals.

The :class:`ConfidenceTier` semantics are the canonical ones first introduced by
``revision_recovery`` scoring; that subpackage now re-exports this very enum so
there is a single shared definition (see ``revision_recovery/models.py``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ConfidenceTier(str, Enum):
    """Advisory confidence tier shared across all detection stages.

    ``str`` mixin lets values serialize to plain JSON without extra work.
    A human reviewer makes the final call — the tier is never a binary verdict.

    The four members and their meaning are exactly those established by the
    revision-recovery scoring rubric:
    """

    INCONCLUSIVE = "inconclusive"
    """The stage could not reach a verdict (e.g. only one revision found).
    Route to later stages (font / OCR)."""

    LOW = "low"
    """Evidence points to benign activity (e.g. legitimate signing / markup).
    Score band: 0-30."""

    MEDIUM = "medium"
    """Suspicious but not conclusive; a cross-check is recommended.
    Score band: 30-70."""

    HIGH = "high"
    """Strong evidence of tampering. Score band: 70-100."""


@dataclass(frozen=True)
class Evidence:
    """One granular before -> after change that supports a :class:`Finding`.

    A finding's headline ``before``/``after`` is a human-readable summary of the
    change; ``evidence`` breaks that into the individual pieces (e.g. one entry
    per changed token) for reviewers who want the detail. Both sides are plain
    strings; an empty side means a pure insertion or deletion.
    """

    label: str
    """What this piece of evidence is (e.g. ``"token"``, ``"amount"``)."""

    before: str
    """The text in the earlier state (empty for a pure insertion)."""

    after: str
    """The text in the later state (empty for a pure deletion)."""


@dataclass(frozen=True)
class Finding:
    """One flagged change produced by a stage, with its own advisory tier.

    A finding is the unit a human reviewer reads. It names the stage that raised
    it, where in the document it occurs, the exact before/after text when
    available, an optional high-value tag, a human-readable reason, and the
    stage's per-finding confidence tier.
    """

    stage: str
    """Name of the stage that produced this finding (e.g. ``"revision_recovery"``)."""

    tier: ConfidenceTier
    """Per-finding advisory confidence. May differ from other findings in the
    same :class:`StageResult`."""

    reason: str
    """One-line human-readable description of the flagged change."""

    page: int | None = None
    """0-based page index the change appears on, or ``None`` if not page-bound."""

    object_ids: tuple[str, ...] = ()
    """Backing object ids as ``"<obj> <gen>"`` strings (empty if not object-bound)."""

    before: str | None = None
    """Headline before-text, or ``None`` when there is no text evidence."""

    after: str | None = None
    """Headline after-text, or ``None`` when there is no text evidence."""

    high_value: str | None = None
    """High-value tag (e.g. ``"amount"`` / ``"date"``) when a sensitive field
    was altered, else ``None``."""

    evidence: tuple[Evidence, ...] = ()
    """Granular per-change evidence backing the headline before/after."""


@dataclass(frozen=True)
class StageResult:
    """Everything one detection stage produced for one PDF.

    The orchestrator (:mod:`pdf_forgery.pipeline`) collects one of these per
    stage. ``ok`` reports whether the stage RAN to completion, independent of the
    verdict — a clean PDF is ``ok=True`` with an INCONCLUSIVE/LOW tier; a file the
    stage could not process is ``ok=False`` with an ``error``.

    ``payload`` carries the stage's own rich result object (for revision recovery,
    its :class:`~pdf_forgery.revision_recovery.models.AnalysisReport`). It lets a
    stage-specific adapter render the original JSON / human summary without the
    core schema having to model every stage's internals.
    """

    stage: str
    """Name of the stage that produced this result."""

    tier: ConfidenceTier
    """Overall advisory tier for this stage on this PDF."""

    score: int | None
    """Numeric score within the tier's band, or ``None`` (e.g. INCONCLUSIVE)."""

    findings: tuple[Finding, ...] = ()
    """Every flagged change this stage raised (may be empty)."""

    summary: str = ""
    """One-line human summary of the stage outcome."""

    reasons: tuple[str, ...] = ()
    """Ordered explanation of the tier decision (most significant first)."""

    notes: tuple[str, ...] = ()
    """Diagnostics / warnings that do not change the tier."""

    ok: bool = True
    """True if the stage ran to completion (NOT a verdict)."""

    error: str | None = None
    """Why the stage failed to run, or ``None`` when ``ok``."""

    payload: Any = field(default=None, repr=False)
    """Stage-specific rich result for adapters (e.g. an ``AnalysisReport``).
    Excluded from ``repr`` to avoid dumping large nested objects."""
