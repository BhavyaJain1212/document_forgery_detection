"""Plain-language glossary for finding types used by the advisory layer.

Keys are the canonical type tokens from ``docs/FORGERY_METHODS.md``; values are
two-field tuples ``(meaning, reviewer_implication)`` used by the stub advisory
and injected into the LLM prompt so the model can explain rather than restate.
"""

from __future__ import annotations

FINDING_TYPE_GLOSSARY: dict[str, tuple[str, str]] = {
    # OCR cross-check (Stage 3)
    "embedded_only": (
        "Text exists in the PDF's text layer but was NOT found in the rendered page image"
        " — it may be hidden, white-on-white, or covered by an overlay.",
        "This pattern is consistent with text being hidden beneath a visual cover-up,"
        " or with the text layer having been injected without a visible counterpart.",
    ),
    "ocr_only": (
        "Text is visible in the rendered page image but is MISSING from the PDF's text"
        " layer — typical of an image patch pasted over the original content.",
        "The edit is visible to the eye but was not made in the real text layer,"
        " suggesting a raster overlay was placed on top of the original page.",
    ),
    "mismatch": (
        "The PDF text layer and the rendered image disagree on the same region"
        " — the visible text was changed without updating the underlying text.",
        "A divergence between what is stored and what is shown is a classic sign of"
        " post-creation editing; a reviewer should compare both representations.",
    ),
    # Revision recovery (Stage 1)
    "content_edit": (
        "A page content stream changed between two saved revisions of the PDF"
        " — earlier text was overwritten in a subsequent incremental save.",
        "Incremental saves preserve the original content; a content stream change"
        " shows what was altered and provides a before/after comparison.",
    ),
    "field_edit": (
        "A form field value was changed after it had already been filled in"
        " — the field had a prior value, and a later revision replaced it.",
        "Changing a form field after the fact (not filling an empty field)"
        " is a strong indicator of post-submission editing.",
    ),
    "overlay": (
        "An annotation whose boundary overlaps a text region was added or modified"
        " — a stamp or redaction placed over existing text.",
        "Overlay annotations can visually obscure original content;"
        " a reviewer should check whether underlying text has been covered.",
    ),
    "form_fill": (
        "An empty form field was filled in a later revision of the PDF.",
        "Filling an empty field is typically legitimate;"
        " verify the fill matches the expected workflow.",
    ),
    "signature_change": (
        "A digital signature object was added or modified in a revision.",
        "Changes to signature objects after the document was signed"
        " may indicate the document was altered post-signing.",
    ),
    "metadata_change": (
        "Document metadata (author, dates, producer) changed between revisions.",
        "Metadata-only changes are often benign but may indicate"
        " an attempt to obscure the editing history.",
    ),
    # Font forensics (Stage 2)
    "intra_token_font_mix": (
        "Different fonts were used within a single word or token"
        " — characters that should share a font do not.",
        "Font mixing inside a token is a strong indicator of character-level editing,"
        " where individual characters were replaced.",
    ),
    "inter_token_font_mix": (
        "Fonts differ across adjacent tokens on the same line"
        " — neighbouring words use inconsistent typefaces.",
        "Cross-token font inconsistency suggests one or more words were replaced"
        " using a different source or tool than the surrounding text.",
    ),
    "line_outlier": (
        "A line of text uses a font that is statistically inconsistent"
        " with the rest of the document.",
        "An outlier line may have been added or replaced;"
        " its font does not match the document's dominant typeface.",
    ),
    # Invoice arithmetic (Stage 4)
    "broken_relationship": (
        "A numeric relationship that should hold (line total = qty × unit price,"
        " or grand total = sum of line totals) does not reconcile.",
        "Arithmetic inconsistency is a strong forgery signal when it cannot be"
        " explained by rounding; a reviewer should re-check the figures manually.",
    ),
    # Provenance / metadata (Stage 5)
    "edit_tool_detected": (
        "The document's producer metadata names a PDF editing tool"
        " — the file was processed by software after initial creation.",
        "An editing-tool producer is not by itself proof of forgery"
        " but raises the question of what was changed and why.",
    ),
    "modification_after_signing": (
        "The document's modification date is later than its creation date"
        " or later than a digital signature timestamp.",
        "A modification after signing may indicate the document was altered"
        " after it was certified; a reviewer should verify the dates.",
    ),
    "provenance_anomaly": (
        "An anomaly in the document's provenance or metadata was detected.",
        "Provenance anomalies corroborate other findings;"
        " review alongside the detector breakdown for context.",
    ),
}

_GENERIC: tuple[str, str] = (
    "An anomaly was detected by this detector.",
    "Review the cited finding in context with the other findings.",
)


def get_glossary_entry(type_token: str) -> tuple[str, str]:
    """Return ``(meaning, reviewer_implication)`` for ``type_token``.

    Falls back to generic text for unknown types — never raises.
    """
    return FINDING_TYPE_GLOSSARY.get(type_token, _GENERIC)


__all__ = ["FINDING_TYPE_GLOSSARY", "get_glossary_entry"]
