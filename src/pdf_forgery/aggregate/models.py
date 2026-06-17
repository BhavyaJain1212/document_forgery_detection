"""Data contracts for Stage 6 — aggregation, the PHI boundary, and advisory.

These are PURE data (frozen dataclasses). They carry no logic. Two families:

* **Server-side, behind the PHI boundary:** :class:`AggregateResult` and its
  :class:`AggregateFinding` roll the per-stage
  :class:`~pdf_forgery.core.types.StageResult` list up into one headline plus a
  flat, overlay-ready finding list. They reference the rich ``StageResult``\\s
  (whose findings still hold raw before/after text for the gated evidence view).

* **Across the PHI boundary (toward the LLM / frontend):**
  :class:`AdvisoryInput` (an explicit allow-list of finding DESCRIPTORS) and the
  advisory model's :class:`AdvisoryOutput`. NEVER raw extracted text, patient
  identifiers, or document content — only the fields in
  :data:`ADVISORY_FINDING_ALLOWLIST`.

The full design is in ``docs/STAGE6_DESIGN.md``.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..core.types import ConfidenceTier, StageResult


# ---------------------------------------------------------------------------
# Geometry — carried NOW so the future document-overlay is a pure render job.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BBox:
    """A finding's region on its page, in the CANONICAL overlay space.

    Normalized ``[0, 1]``, top-left origin, page-relative: ``(x0, y0)`` is the
    top-left corner, ``(x1, y1)`` the bottom-right. Normalizing makes the overlay
    independent of render DPI and page size — the frontend multiplies by the
    rendered page's pixel dimensions. ``None`` (on a finding) means the
    originating stage cannot localise that finding yet.
    """

    x0: float
    y0: float
    x1: float
    y1: float


# ---------------------------------------------------------------------------
# Aggregate (server-side, behind the boundary)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AggregateFinding:
    """One finding flattened across stages, as a DESCRIPTOR (no raw text).

    The flat unit a UI lists / filters / overlays. ``finding_id`` is stable and
    deterministic (``f"{stage}-{n}"``) so the advisory can cite it and the
    overlay can key on it.
    """

    finding_id: str
    stage: str
    type: str
    """Finding / forgery-method kind (short stable token; should converge on the
    canonical literals in ``docs/FORGERY_METHODS.md``)."""

    tier: ConfidenceTier
    score: int | None
    token_class: str | None
    """High-value class — ``"amount"`` / ``"date"`` / ``"id"`` / ``"prose"`` /
    ``None`` (the KIND of field, never its value)."""

    page: int | None
    bbox: BBox | None


@dataclass(frozen=True)
class AggregateResult:
    """The per-stage results rolled up into one advisory headline (server-side).

    Holds the rich :class:`StageResult`\\s (their findings still carry raw
    before/after text for the gated evidence view), so this object must stay
    behind the PHI boundary — only its scrubbed projection
    (:class:`AdvisoryInput`) may cross.
    """

    tier: ConfidenceTier
    score: int | None
    stage_results: tuple[StageResult, ...]
    findings: tuple[AggregateFinding, ...]
    reasons: tuple[str, ...] = ()
    contributing_stages: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Advisory input — the PHI boundary allow-list (crosses toward LLM / frontend)
# ---------------------------------------------------------------------------

#: The trust boundary, as code. A finding field may cross toward the LLM /
#: frontend ONLY if it appears here. This is an allow-list, never a deny-list:
#: a field is present only because it has been affirmatively cleared as non-PHI.
ADVISORY_FINDING_ALLOWLIST: tuple[str, ...] = (
    "finding_id",
    "stage",
    "type",
    "tier",
    "score",
    "token_class",
    "page",
    "bbox",
)


@dataclass(frozen=True)
class AdvisoryFinding:
    """A finding descriptor cleared to cross the PHI boundary.

    Its fields are EXACTLY :data:`ADVISORY_FINDING_ALLOWLIST`. Carries no raw
    extracted text, no identifiers, no document content. ``token_class`` is the
    KIND of a high-value field (``"amount"``) — never its value.
    """

    finding_id: str
    stage: str
    type: str
    tier: ConfidenceTier
    score: int | None
    token_class: str | None
    page: int | None
    bbox: BBox | None


@dataclass(frozen=True)
class AdvisoryStage:
    """Per-stage outcome descriptor (no content)."""

    stage: str
    tier: ConfidenceTier
    score: int | None
    ok: bool


@dataclass(frozen=True)
class AdvisoryInput:
    """The ONLY object that may cross toward the advisory LLM or the frontend.

    Descriptors only — see :data:`ADVISORY_FINDING_ALLOWLIST` and
    ``docs/STAGE6_DESIGN.md`` §2 for the enumerated forbidden set.
    """

    tier: ConfidenceTier
    score: int | None
    stages: tuple[AdvisoryStage, ...]
    findings: tuple[AdvisoryFinding, ...]
    notes: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Finding groups (derived from AdvisoryFindings; PHI-safe)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FindingGroup:
    """Near-identical findings collapsed by ``(stage, type, token_class)``.

    ``tier`` is the maximum (worst-case) across all members — escalation, not
    averaging. ``pages`` is sorted unique 0-based page indices. ``finding_ids``
    are the member ids so the UI can map groups back to individual findings.
    """

    stage: str
    type: str
    token_class: str | None
    tier: ConfidenceTier
    count: int
    pages: tuple[int, ...]
    finding_ids: tuple[str, ...]


# ---------------------------------------------------------------------------
# Advisory output
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FindingRationale:
    """The model's one-sentence rationale for a single finding, by id.

    Deprecated in favour of :class:`GroupExplanation`; kept for backward compat.
    """

    finding_id: str
    rationale: str


@dataclass(frozen=True)
class GroupExplanation:
    """The advisory's plain-language explanation for one finding group."""

    finding_ids: tuple[str, ...]
    """Members this explanation covers (for UI mapping and citation)."""

    label: str
    """Short human label, e.g. ``'Text/image mismatch (id fields, 5×, pages 1–2)'``."""

    what_we_found: str
    """Plain language description grounded in descriptors."""

    why_it_matters: str
    """What this could indicate (decision-support framing)."""

    what_to_check: str
    """Concrete next action for the reviewer."""


@dataclass(frozen=True)
class AdvisoryOutput:
    """The advisory model's grounded explanation (decision-support, advisory)."""

    summary: str
    tier_statement: str
    finding_rationales: tuple[FindingRationale, ...] = ()
    """Deprecated per-finding rationales; kept for backward compat. Prefer
    :attr:`group_explanations`."""
    group_explanations: tuple[GroupExplanation, ...] = ()
    """Per-group plain-language explanations (the primary advisory output)."""
    model: str = ""
    """Engine name + version, for the audit trail."""


__all__ = [
    "BBox",
    "AggregateFinding",
    "AggregateResult",
    "ADVISORY_FINDING_ALLOWLIST",
    "AdvisoryFinding",
    "AdvisoryStage",
    "AdvisoryInput",
    "FindingGroup",
    "FindingRationale",
    "GroupExplanation",
    "AdvisoryOutput",
]
