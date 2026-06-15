"""Pure data models for the provenance-metadata stage.

Frozen dataclasses, no logic. The stage reads the Info dict, XMP, and trailer
/ID (read-only, shallow) and emits :class:`ProvenanceFinding` objects aggregated
into a :class:`ProvenanceReport`. It NEVER reaches HIGH on its own — it is a
corroborator that strengthens an arithmetic / font / revision case.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ..core.types import ConfidenceTier

__all__ = [
    "ProvenanceFindingKind",
    "ProvenanceFinding",
    "ProvenanceReport",
    "ConfidenceTier",
]


class ProvenanceFindingKind(str, Enum):
    """Why a provenance finding was raised."""

    WEB_EDITOR_PRODUCER = "web_editor_producer"
    """Producer/Creator matches a known general-purpose / web PDF editor."""

    VERSION_ONLY_PRODUCER = "version_only_producer"
    """Producer is a bare version string with no product name (re-render tool)."""

    BROWSER_CREATOR = "browser_creator"
    """Creator indicates a browser print/export path."""

    MODDATE_AFTER_CREATION = "moddate_after_creation"
    """ModDate is later than CreationDate (modified after creation)."""

    XMP_INFO_PRODUCER_MISMATCH = "xmp_info_producer_mismatch"
    """Producer differs between the XMP packet and the Info dictionary."""

    ID_HALVES_MISMATCH = "id_halves_mismatch"
    """Trailer /ID's two halves differ (file changed after creation)."""

    COMPOSITE_EDIT_FOOTPRINT = "composite_edit_footprint"
    """A version/web Producer + browser Creator + ModDate>CreationDate together —
    the footprint of a consumer re-render of an institutional document."""


@dataclass(frozen=True)
class ProvenanceFinding:
    """One provenance signal, with the exact strings/dates that triggered it."""

    kind: ProvenanceFindingKind
    tier: ConfidenceTier
    reason: str
    detail: tuple[tuple[str, str], ...] = ()
    """Ordered ``(label, value)`` evidence pairs (e.g. ``("producer", "...")``)."""


@dataclass(frozen=True)
class ProvenanceReport:
    """Per-file result of the provenance-metadata stage (rich, stage-native)."""

    path: str
    ok: bool
    tier: ConfidenceTier
    score: int | None
    producer: str | None = None
    creator: str | None = None
    creation_date: str | None = None
    mod_date: str | None = None
    xmp_producer: str | None = None
    id_pair: tuple[str, str] | None = None
    findings: tuple[ProvenanceFinding, ...] = ()
    reasons: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    error: str | None = None
    raw_size: int = 0
