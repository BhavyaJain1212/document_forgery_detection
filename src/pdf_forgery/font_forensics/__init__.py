"""Stage 2 — font forensics.

Detects text that was edited before a PDF was flattened, by examining pdfminer's
PER-CHARACTER font attribution (``LTChar.fontname`` / size / bbox) rather than
page-level font lists. When an editor re-types a value it frequently re-embeds
the changed glyphs under a different font subset than their surrounding line —
a fingerprint that survives even a single-revision "Save As" where revision
recovery has nothing to compare.

Detectors evaluate token-internal glyph seams first, then line-local, page-local,
and document-global baselines. Only a confident high-value token with an internal
mixed-font seam can independently reach HIGH; whole-token differences are
supporting evidence capped below HIGH.

Confidence is advisory: HIGH for a confident amount/date/ID with an intra-token
glyph seam; MEDIUM for supporting local differences; LOW for benign styling or
weak global evidence; INCONCLUSIVE for a single uniform font.
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
from .config import FontConfig
from .detect import detect_findings
from .extract import (
    distinct_fonts,
    dominant_font,
    glyphs_from_bytes,
    glyphs_from_layouts,
    group_lines,
)
from .fonts import (
    FontIdentity,
    is_style_variant,
    is_substitution,
    parse_font_identity,
    same_base_different_subset,
)
from .models import (
    ConfidenceTier,
    ClassificationStrength,
    FontFinding,
    FontFindingKind,
    FontReport,
    Glyph,
    HighValueKind,
    TextLine,
    Token,
    TokenCandidate,
)
from .scoring import score_findings
from .stage import FontForensicsStage

__all__ = [
    # config
    "FontConfig",
    # font-name helpers
    "FontIdentity",
    "parse_font_identity",
    "same_base_different_subset",
    "is_style_variant",
    "is_substitution",
    # extraction
    "glyphs_from_bytes",
    "glyphs_from_layouts",
    "group_lines",
    "dominant_font",
    "distinct_fonts",
    # detection / scoring
    "detect_findings",
    "score_findings",
    # orchestration
    "analyze_bytes",
    "analyze_path",
    "analyze_bytes_as_stage",
    "analyze_path_as_stage",
    # stage / adapter
    "FontForensicsStage",
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
    "FontReport",
    "FontFinding",
    "FontFindingKind",
    "Glyph",
    "TextLine",
    "Token",
    "ConfidenceTier",
    "HighValueKind",
    "ClassificationStrength",
    "TokenCandidate",
]
