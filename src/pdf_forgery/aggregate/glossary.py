"""Plain-language glossary for finding types used by the advisory layer.

Keys are the canonical type tokens from ``docs/FORGERY_METHODS.md`` and the
enum values each stage's ``_finding_type()`` can emit; values are two-field
tuples ``(meaning, reviewer_implication)`` used by the stub advisory and
injected into the LLM prompt so the model can explain rather than restate.

Every token that ``aggregate._finding_type()`` can produce MUST have an entry
here (enforced by ``tests/test_aggregate.py::test_glossary_covers_all_emitted_type_tokens``).
"""

from __future__ import annotations

FINDING_TYPE_GLOSSARY: dict[str, tuple[str, str]] = {
    # ---- OCR cross-check (Stage 3) ----------------------------------------
    "embedded_only": (
        "Text exists in the PDF's text layer but was NOT found in the rendered"
        " page image — it may be hidden, white-on-white, or covered by an overlay.",
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
    # ---- Revision recovery (Stage 1) --------------------------------------
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
    "markup": (
        "A comment, highlight, or note annotation was added or modified in a later"
        " revision of the PDF — an annotation placed over the existing content.",
        "Markup annotations are often legitimate (reviewer notes, highlights);"
        " verify the annotation is consistent with the document's expected workflow"
        " and does not obscure key content.",
    ),
    "metadata_change": (
        "Document metadata (author, dates, producer) changed between revisions.",
        "Metadata-only changes are often benign but may indicate"
        " an attempt to obscure the editing history.",
    ),
    # ---- Font forensics (Stage 2) -----------------------------------------
    "intra_token_font_mix": (
        "One or more characters INSIDE a single word or number use a different"
        " font from the rest of the token — a single-glyph insertion fingerprint.",
        "Font mixing inside a token is a strong indicator of character-level editing,"
        " where an individual character (e.g. a digit in an amount) was replaced.",
    ),
    "whole_token_subset_difference": (
        "A uniformly-rendered word or token uses a different font subset from the"
        " surrounding text on the same line, despite sharing the same base typeface.",
        "Different font subsets on the same base face can indicate the token was"
        " re-embedded from a different source document or editing session.",
    ),
    "whole_token_family_difference": (
        "A uniformly-rendered word or token uses a completely different font family"
        " from its neighbours on the same line.",
        "Cross-family font differences between adjacent tokens suggest one or more"
        " words were replaced using a different authoring tool than the surrounding text.",
    ),
    "page_baseline_deviation": (
        "A word, token, or line uses a font that is inconsistent with the dominant"
        " typeface used on the same page.",
        "A page-level font outlier may indicate a word or line was added or replaced;"
        " its font does not match what the rest of the page uses.",
    ),
    "document_baseline_deviation": (
        "A word, token, or line uses a font that differs from the document's overall"
        " dominant typeface — a weaker signal than a page-level deviation.",
        "A document-baseline font outlier is supporting evidence; verify in combination"
        " with other findings before drawing conclusions.",
    ),
    "intra_line_subset_split": (
        "Adjacent characters on the same line use different subsets of the same base"
        " typeface — a within-line subset tag change.",
        "A same-family subset split inside a line may indicate a character or word was"
        " re-embedded from a different source; review with the surrounding context.",
    ),
    # ---- Invoice arithmetic (Stage 4) — keyed by RelationshipKind value ----
    "line_item": (
        "A line total does not match quantity × unit price — the stated line amount"
        " cannot be reproduced from the other cells on the same row.",
        "A broken line-item equation is a direct signal that one of the three values"
        " (quantity, unit price, or line total) may have been edited independently.",
    ),
    "subtotal_sum": (
        "The sum of the individual line amounts does not equal the stated subtotal.",
        "A subtotal that doesn't reconcile with its component lines suggests at least"
        " one line amount or the subtotal itself was altered.",
    ),
    "grand_total": (
        "The grand total does not reconcile with the subtotal, taxes, and discounts"
        " as stated on the document.",
        "A broken grand-total equation is a strong signal that at least one of the"
        " constituent figures was changed without updating the others.",
    ),
    "gst_sum": (
        "The GST component breakdown (CGST + SGST + IGST) does not sum to the"
        " stated total tax amount.",
        "A mismatch in tax components may indicate the tax total or one of its"
        " component figures was edited; verify with the applicable tax rate.",
    ),
    "deposit_balance": (
        "The deposit amount plus the stated balance does not equal the final total.",
        "An inconsistent deposit/balance split may indicate the total or one of its"
        " parts was edited; verify with source payment records.",
    ),
    "room_charge": (
        "The room charge does not equal the room rate multiplied by the number of"
        " nights, as stated on the document.",
        "A broken room-charge equation may indicate the rate, the number of nights,"
        " or the total charge was altered independently.",
    ),
    "date_span": (
        "The number of charged days does not match the span between the admission"
        " and discharge (or equivalent) dates on the document.",
        "A date-span inconsistency may indicate dates or the charged-days figure"
        " was edited; verify against admission and discharge records.",
    ),
    # ---- Provenance / metadata (Stage 5) — keyed by ProvenanceFindingKind --
    "web_editor_producer": (
        "The document's producer or creator metadata names a known general-purpose"
        " web PDF editor (e.g. Sejda, iLovePDF, Smallpdf, PDFescape).",
        "A consumer web-editor producer is not proof of forgery on its own, but raises"
        " the question of whether the document was re-processed after official creation.",
    ),
    "version_only_producer": (
        "The producer field contains only a bare version string (e.g. '3.0.35') with"
        " no product name — the fingerprint of a re-render or conversion tool.",
        "A version-only producer string is the typical output of automated PDF"
        " re-render tools; check whether re-processing is expected for this document.",
    ),
    "browser_creator": (
        "The creator metadata indicates the document was printed or exported via a"
        " browser (e.g. Chrome, Chromium PDF export).",
        "A browser-print origin suggests the document may have been reconstructed"
        " from a web view rather than exported from the original authoring system.",
    ),
    "moddate_after_creation": (
        "The document's modification date is later than its creation date, indicating"
        " the file was changed after it was first created.",
        "A modification after creation does not by itself prove tampering, but"
        " combined with other signals it corroborates that the document was re-edited.",
    ),
    "xmp_info_producer_mismatch": (
        "The producer name in the XMP metadata packet differs from the producer in"
        " the PDF Info dictionary — two conflicting provenance records.",
        "A producer mismatch between XMP and Info may indicate the metadata was"
        " altered to obscure the true editing history of the document.",
    ),
    "id_halves_mismatch": (
        "The PDF trailer's /ID field has two halves that differ, indicating the file"
        " identifier changed after the document was first created.",
        "Mismatched /ID halves mean the file was modified after its initial creation;"
        " this corroborates other tampering signals.",
    ),
    "composite_edit_footprint": (
        "Multiple provenance signals appeared together: a consumer editing tool as"
        " producer, a browser as creator, and a modification date after creation."
        " This combination is the typical footprint of a consumer re-render.",
        "The composite footprint strongly suggests the document was processed through"
        " a consumer tool after its original creation; a reviewer should determine"
        " whether this is consistent with the document's expected workflow.",
    ),
    # ---- Fallback entries (reachable only when payload is absent) ----------
    "broken_relationship": (
        "An accounting relationship (line totals, subtotal, grand total) does not"
        " reconcile within the expected tolerance.",
        "Arithmetic inconsistency is a strong forgery signal; a reviewer should"
        " re-check the figures manually against source documents.",
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
