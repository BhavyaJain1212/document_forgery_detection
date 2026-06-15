"""Stage — provenance metadata (lightweight, supporting evidence only).

Reads the document's Info dictionary, XMP packet, and trailer /ID (read-only,
shallow) to corroborate a forgery case:

    - Producer/Creator fingerprinting against known general-purpose / web PDF
      editors, AND a bare version-only Producer (a generic re-render fingerprint
      even without a brand name);
    - ModDate later than CreationDate;
    - XMP vs Info producer inconsistency;
    - trailer /ID two-element mismatch (modified after creation);
    - a composite "edited footprint" combining the above.

Hard boundary: this stage does NOT walk /Prev pointers, reconstruct incremental
updates, or parse xref tables — revision_recovery owns that.

Confidence: provenance NEVER independently reaches HIGH. It tops out at MEDIUM
and exists to strengthen a case when arithmetic / font / revision findings also
fire.
"""

from .adapter import (
    STAGE_NAME,
    render_json,
    render_stage_json,
    render_stage_summary,
    render_summary,
    report_to_dict,
    report_to_stage_result,
    stage_result_to_report,
)
from .analyze import (
    analyze_bytes,
    analyze_bytes_as_stage,
    analyze_path,
    analyze_path_as_stage,
)
from .config import ProvenanceConfig
from .dates import mod_after_creation, parse_pdf_date
from .detect import Metadata, detect, read_metadata
from .models import (
    ConfidenceTier,
    ProvenanceFinding,
    ProvenanceFindingKind,
    ProvenanceReport,
)
from .scoring import score
from .stage import ProvenanceMetadataStage

__all__ = [
    # config
    "ProvenanceConfig",
    # dates
    "parse_pdf_date",
    "mod_after_creation",
    # detection / scoring
    "Metadata",
    "read_metadata",
    "detect",
    "score",
    # orchestration
    "analyze_bytes",
    "analyze_path",
    "analyze_bytes_as_stage",
    "analyze_path_as_stage",
    # stage / adapter
    "ProvenanceMetadataStage",
    "STAGE_NAME",
    "report_to_stage_result",
    "stage_result_to_report",
    # rendering
    "render_json",
    "render_summary",
    "render_stage_json",
    "render_stage_summary",
    "report_to_dict",
    # models
    "ProvenanceReport",
    "ProvenanceFinding",
    "ProvenanceFindingKind",
    "ConfidenceTier",
]
