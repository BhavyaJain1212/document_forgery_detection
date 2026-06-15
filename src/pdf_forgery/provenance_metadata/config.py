"""Central configuration for the provenance-metadata stage.

All editor signatures, date rules, score values, and toggles live here. The
producer fingerprint is deliberately NOT a list of brand names only — a bare
version string (e.g. ``3.0.35 (5.1.21)``) emitted by a web/desktop re-render
tool is itself a fingerprint, especially paired with a browser Creator and a
ModDate after the CreationDate. That composite is the signal.
"""

from __future__ import annotations

from dataclasses import dataclass, field


def _default_web_editor_producers() -> tuple[str, ...]:
    """Case-insensitive substrings of known general-purpose / web PDF editors."""
    return (
        "sejda",
        "ilovepdf",
        "smallpdf",
        "pdfescape",
        "foxit",            # Foxit web tools / PhantomPDF online
        "pdf editor",
        "pdf24",
        "soda pdf",
        "sodapdf",
        "nitro",
        "pdffiller",
        "docfly",
        "cleverpdf",
        "lightpdf",
        "deftpdf",
        "online2pdf",
        "pdfcandy",
    )


def _default_browser_creators() -> tuple[str, ...]:
    """Case-insensitive substrings of browsers (print-to-PDF / web export).

    An institutional invoice produced via a browser print path is mildly
    suspicious — genuine billing systems use a server-side PDF library."""
    return (
        "mozilla",
        "firefox",
        "chrome",
        "chromium",
        "safari",
        "edge",
        "opera",
    )


@dataclass
class ProvenanceConfig:
    """Tunable parameters for provenance-metadata detection and scoring."""

    # ------------------------------------------------------------------ #
    # Signature vocabularies                                              #
    # ------------------------------------------------------------------ #

    web_editor_producers: tuple[str, ...] = field(
        default_factory=_default_web_editor_producers
    )
    """Producer/Creator substrings (lower-cased) of general-purpose/web editors."""

    browser_creators: tuple[str, ...] = field(
        default_factory=_default_browser_creators
    )
    """Creator substrings (lower-cased) indicating a browser export path."""

    version_only_producer_pattern: str = (
        r"^\s*\d+\.\d+(?:\.\d+)?\s*(?:\(\d+(?:\.\d+)*\)\s*)?$"
    )
    """A Producer that is JUST a version number (optionally ``maj.min (a.b.c)``)
    with no product name — a generic re-render fingerprint (e.g. Sejda's
    ``3.0.35 (5.1.21)``). Genuine billing software stamps its product name."""

    # ------------------------------------------------------------------ #
    # Detector toggles                                                    #
    # ------------------------------------------------------------------ #

    enable_producer_check: bool = True
    enable_version_producer_check: bool = True
    enable_browser_creator_check: bool = True
    enable_date_check: bool = True
    enable_xmp_info_mismatch: bool = True
    enable_id_mismatch: bool = True
    enable_composite: bool = True
    """Combine a version/web Producer + browser Creator + ModDate>CreationDate
    into one MEDIUM composite "edited footprint" finding."""

    # ------------------------------------------------------------------ #
    # Score values (provenance is a CORROBORATOR — never exceeds MEDIUM)  #
    # ------------------------------------------------------------------ #

    medium_ceiling: int = 60
    """Hard cap: provenance alone can never score above this (never HIGH)."""

    score_composite: int = 55
    """The composite edited-footprint finding (strongest provenance signal)."""

    score_web_editor: int = 45
    """A named web/general-purpose editor in Producer/Creator."""

    score_version_producer: int = 35
    """A bare version-only Producer (no product name)."""

    score_xmp_info_mismatch: int = 40
    """Producer differs between XMP and the Info dict."""

    score_id_mismatch: int = 35
    """Trailer /ID halves differ (file modified after creation)."""

    score_date_anomaly: int = 25
    """ModDate after CreationDate (or ModDate on a doc that should be fresh)."""

    score_browser_creator: int = 20
    """A browser Creator on a document purporting to be an institutional bill."""

    score_clean: int = 0
    """No provenance anomalies found."""
