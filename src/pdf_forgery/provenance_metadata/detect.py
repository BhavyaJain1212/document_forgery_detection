"""Read Info / XMP / trailer-ID metadata and raise provenance findings.

Read-only and shallow: we look ONLY at the Info dictionary, the XMP packet's
producer, and the trailer ``/ID`` pair. We deliberately do NOT walk ``/Prev``
pointers, reconstruct incremental updates, or parse xref tables — that is
``revision_recovery``'s job. This stage is a cheap corroborator.

The strongest single signal is the composite "edited footprint": a generic
version-only / web-editor Producer + a browser Creator + a ModDate after the
CreationDate. Each ingredient is also reported on its own.
"""

from __future__ import annotations

import re

from .config import ProvenanceConfig
from .dates import mod_after_creation
from .models import ConfidenceTier, ProvenanceFinding, ProvenanceFindingKind


class Metadata:
    """The handful of fields this stage reads (already coerced to ``str``)."""

    def __init__(
        self,
        producer: str | None,
        creator: str | None,
        creation_date: str | None,
        mod_date: str | None,
        xmp_producer: str | None,
        id_pair: tuple[str, str] | None,
    ) -> None:
        self.producer = producer
        self.creator = creator
        self.creation_date = creation_date
        self.mod_date = mod_date
        self.xmp_producer = xmp_producer
        self.id_pair = id_pair


def read_metadata(pdf) -> Metadata:
    """Extract the relevant metadata from an open pikepdf document (tolerant)."""
    producer = _docinfo_str(pdf, "/Producer")
    creator = _docinfo_str(pdf, "/Creator")
    creation = _docinfo_str(pdf, "/CreationDate")
    mod = _docinfo_str(pdf, "/ModDate")
    xmp_producer = _xmp_producer(pdf)
    id_pair = _id_pair(pdf)
    return Metadata(producer, creator, creation, mod, xmp_producer, id_pair)


def _docinfo_str(pdf, key: str) -> str | None:
    try:
        info = pdf.docinfo
        if key in info:
            return str(info[key]).strip() or None
    except Exception:
        return None
    return None


def _xmp_producer(pdf) -> str | None:
    try:
        with pdf.open_metadata() as meta:
            for key in ("pdf:Producer", "xmp:CreatorTool", "dc:producer"):
                val = meta.get(key)
                if val:
                    return str(val).strip() or None
    except Exception:
        return None
    return None


def _id_pair(pdf) -> tuple[str, str] | None:
    try:
        ident = pdf.trailer.get("/ID")
        if ident is None or len(ident) < 2:
            return None
        a = bytes(ident[0])
        b = bytes(ident[1])
        return (a.hex(), b.hex())
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def detect(meta: Metadata, config: ProvenanceConfig | None = None) -> list[ProvenanceFinding]:
    """Run all provenance checks; return findings (provenance never -> HIGH)."""
    cfg = config or ProvenanceConfig()
    findings: list[ProvenanceFinding] = []

    web_editor = _match_web_editor(meta, cfg)
    version_only = _is_version_only_producer(meta.producer, cfg)
    browser = _match_browser_creator(meta.creator, cfg)
    moddate_anomaly = (
        cfg.enable_date_check
        and mod_after_creation(meta.creation_date, meta.mod_date)
    )

    if cfg.enable_producer_check and web_editor is not None:
        field_name, value = web_editor
        findings.append(
            ProvenanceFinding(
                kind=ProvenanceFindingKind.WEB_EDITOR_PRODUCER,
                tier=ConfidenceTier.MEDIUM,
                reason=(
                    f"{field_name} {value!r} matches a known general-purpose / web "
                    "PDF editor — unexpected for an institutional invoice"
                ),
                detail=((field_name.lower(), value),),
            )
        )

    if (
        cfg.enable_version_producer_check
        and version_only
        and web_editor is None  # don't double-report a named web editor
    ):
        findings.append(
            ProvenanceFinding(
                kind=ProvenanceFindingKind.VERSION_ONLY_PRODUCER,
                tier=ConfidenceTier.LOW,
                reason=(
                    f"Producer {meta.producer!r} is a bare version string with no "
                    "product name — typical of a generic re-render tool, not "
                    "billing software"
                ),
                detail=(("producer", meta.producer or ""),),
            )
        )

    if cfg.enable_browser_creator_check and browser is not None:
        findings.append(
            ProvenanceFinding(
                kind=ProvenanceFindingKind.BROWSER_CREATOR,
                tier=ConfidenceTier.LOW,
                reason=(
                    f"Creator {meta.creator!r} indicates a browser print/export "
                    "path — institutional bills are usually server-generated"
                ),
                detail=(("creator", meta.creator or ""),),
            )
        )

    if moddate_anomaly:
        findings.append(
            ProvenanceFinding(
                kind=ProvenanceFindingKind.MODDATE_AFTER_CREATION,
                tier=ConfidenceTier.LOW,
                reason=(
                    f"ModDate {meta.mod_date!r} is later than CreationDate "
                    f"{meta.creation_date!r} — the file was modified after creation"
                ),
                detail=(
                    ("creation_date", meta.creation_date or ""),
                    ("mod_date", meta.mod_date or ""),
                ),
            )
        )

    if cfg.enable_xmp_info_mismatch and _xmp_info_mismatch(meta):
        findings.append(
            ProvenanceFinding(
                kind=ProvenanceFindingKind.XMP_INFO_PRODUCER_MISMATCH,
                tier=ConfidenceTier.MEDIUM,
                reason=(
                    f"Producer differs between XMP ({meta.xmp_producer!r}) and the "
                    f"Info dict ({meta.producer!r}) — inconsistent provenance"
                ),
                detail=(
                    ("info_producer", meta.producer or ""),
                    ("xmp_producer", meta.xmp_producer or ""),
                ),
            )
        )

    if cfg.enable_id_mismatch and _id_mismatch(meta):
        a, b = meta.id_pair  # type: ignore[misc]
        findings.append(
            ProvenanceFinding(
                kind=ProvenanceFindingKind.ID_HALVES_MISMATCH,
                tier=ConfidenceTier.MEDIUM,
                reason=(
                    "Trailer /ID halves differ — the file was modified after "
                    "its original creation"
                ),
                detail=(("id_original", a), ("id_current", b)),
            )
        )

    # Composite: the consumer-re-render footprint (strongest provenance signal).
    if cfg.enable_composite:
        ingredients = (web_editor is not None or version_only, browser is not None, moddate_anomaly)
        if sum(bool(x) for x in ingredients) >= 2 and ingredients[0]:
            findings.append(
                ProvenanceFinding(
                    kind=ProvenanceFindingKind.COMPOSITE_EDIT_FOOTPRINT,
                    tier=ConfidenceTier.MEDIUM,
                    reason=(
                        "Composite edited-footprint: a generic/web-editor Producer "
                        f"({meta.producer!r})"
                        + (f" + browser Creator ({meta.creator!r})" if browser else "")
                        + (" + ModDate after CreationDate" if moddate_anomaly else "")
                        + " — consistent with a consumer re-render of an "
                        "institutional document (corroborating evidence only)"
                    ),
                    detail=(
                        ("producer", meta.producer or ""),
                        ("creator", meta.creator or ""),
                        ("creation_date", meta.creation_date or ""),
                        ("mod_date", meta.mod_date or ""),
                    ),
                )
            )

    return findings


# ---------------------------------------------------------------------------
# Matchers
# ---------------------------------------------------------------------------

def _match_web_editor(meta: Metadata, cfg: ProvenanceConfig) -> tuple[str, str] | None:
    """Return ``(field, value)`` if Producer or Creator names a web editor."""
    for field_name, value in (("Producer", meta.producer), ("Creator", meta.creator)):
        if not value:
            continue
        low = value.lower()
        if any(sig in low for sig in cfg.web_editor_producers):
            return (field_name, value)
    return None


def _is_version_only_producer(producer: str | None, cfg: ProvenanceConfig) -> bool:
    if not producer:
        return False
    return re.match(cfg.version_only_producer_pattern, producer) is not None


def _match_browser_creator(creator: str | None, cfg: ProvenanceConfig) -> str | None:
    if not creator:
        return None
    low = creator.lower()
    return creator if any(b in low for b in cfg.browser_creators) else None


def _xmp_info_mismatch(meta: Metadata) -> bool:
    if not meta.xmp_producer or not meta.producer:
        return False
    return meta.xmp_producer.strip() != meta.producer.strip()


def _id_mismatch(meta: Metadata) -> bool:
    if meta.id_pair is None:
        return False
    a, b = meta.id_pair
    return a != b
