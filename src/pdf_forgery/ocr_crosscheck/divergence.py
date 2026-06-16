"""Divergence classification (CPU queue).

Turns matched groups and unmatched words (from ``align``) into typed, weighted
:class:`Divergence` objects per the taxonomy in ``docs/STAGE3_DESIGN.md`` §2:
AGREE / MISMATCH / EMBEDDED_ONLY / OCR_ONLY. Each divergence carries its token
class (from ``normalize.classify``) and its weight
(``base_weight[type] * token_multiplier[class]``, §6a).
"""

from __future__ import annotations

from .config import OCRCrossCheckConfig
from .models import Divergence, DivergenceType, TokenClass, WordBox, WordSource

# Priority ordering for picking the most-sensitive class among sub-tokens.
_CLASS_PRIORITY: dict[TokenClass, int] = {
    TokenClass.AMOUNT: 0,
    TokenClass.DATE: 1,
    TokenClass.ID: 2,
    TokenClass.PROSE: 3,
}


def weight_for(
    div_type: DivergenceType,
    token_class: TokenClass,
    config: OCRCrossCheckConfig | None = None,
) -> float:
    """``base_weight[div_type] * token_multiplier[token_class]`` (§6a)."""
    cfg = config or OCRCrossCheckConfig()

    base = {
        DivergenceType.AGREE: cfg.weight_agree,
        DivergenceType.MISMATCH: cfg.weight_mismatch,
        DivergenceType.EMBEDDED_ONLY: cfg.weight_embedded_only,
        DivergenceType.OCR_ONLY: cfg.weight_ocr_only,
    }[div_type]

    mult = {
        TokenClass.AMOUNT: cfg.mult_amount,
        TokenClass.DATE: cfg.mult_date,
        TokenClass.ID: cfg.mult_id,
        TokenClass.PROSE: cfg.mult_prose,
    }[token_class]

    return base * mult


def _classify_for_elevation(
    tok: str, config: OCRCrossCheckConfig | None = None
) -> TokenClass:
    """Classify ``tok``, demoting a non-monetary bare-digit AMOUNT to PROSE.

    A bare 1-2 digit integer (no currency symbol/word, decimal, or thousands
    separator) is not treated as AMOUNT-elevating when
    ``amount_requires_monetary_context`` is set — see
    ``docs/STAGE3_LONG_PDF_FALSE_POSITIVE.md`` RC#1.
    """
    from .normalize import classify, is_monetary_amount

    cfg = config or OCRCrossCheckConfig()
    cls = classify(tok)
    if (
        cls is TokenClass.AMOUNT
        and cfg.amount_requires_monetary_context
        and not is_monetary_amount(tok)
    ):
        return TokenClass.PROSE
    return cls


def _group_token_class(
    embedded: tuple[WordBox, ...], config: OCRCrossCheckConfig | None = None
) -> TokenClass:
    """Return the most-sensitive token class among all sub-tokens in a group.

    Scans each embedded word's text (split into whitespace-delimited tokens),
    classifies each, and returns the highest-priority class so a single altered
    amount inside a prose group is not diluted.
    """
    best = TokenClass.PROSE
    for w in embedded:
        for tok in w.text.split():
            cls = _classify_for_elevation(tok, config)
            if _CLASS_PRIORITY[cls] < _CLASS_PRIORITY[best]:
                best = cls
    return best


def _high_value_subtokens(
    embedded: tuple[WordBox, ...],
    tok_class: TokenClass,
    config: OCRCrossCheckConfig | None = None,
) -> list[str]:
    """Sub-tokens of ``embedded`` that themselves classify as ``tok_class``.

    These are the specific high-value units that elevated the group's overall
    class — the only parts of a multi-token line the localized check (Task 2,
    RC#1 fix A) requires to survive intact.
    """
    out: list[str] = []
    for w in embedded:
        for tok in w.text.split():
            if _classify_for_elevation(tok, config) is tok_class:
                out.append(tok)
    return out


def classify_group(
    embedded: tuple[WordBox, ...],
    ocr: WordBox,
    config: OCRCrossCheckConfig | None = None,
) -> Divergence:
    """Classify one matched (one-to-many) group as AGREE or MISMATCH.

    Joins ``embedded`` in their given order (already reading-order from align),
    folds both sides, and checks tolerance using the most-sensitive token class
    among the group's sub-tokens.  A single altered high-value sub-token (amount,
    date) cannot be diluted by surrounding prose: the group's class is the
    strictest class present.

    For a MULTI-TOKEN AMOUNT/DATE group, the whole joined line is NOT judged at
    zero tolerance (that would let one unrelated 1-char OCR artifact anywhere in
    a long prose+SKU line trip a false high-value MISMATCH — see
    ``docs/STAGE3_LONG_PDF_FALSE_POSITIVE.md`` RC#1). Instead: each high-value
    sub-token must survive intact (folded, zero-tolerance containment) in the
    folded OCR text, AND the whole line must agree at PROSE tolerance. A
    single-token high-value group keeps the original strict whole-string check
    — a true ``5,000`` → ``6,000`` edit must still be caught.
    """
    from .normalize import fold, is_within_tolerance

    cfg = config or OCRCrossCheckConfig()

    # Join embedded words into one comparison string.
    joined_emb = " ".join(w.text for w in embedded)

    # The group's class drives the tolerance.
    tok_class = _group_token_class(embedded, cfg)

    page = embedded[0].page_index if embedded else (ocr.page_index if ocr else 0)

    is_multi_token = len(joined_emb.split()) > 1

    if tok_class in (TokenClass.AMOUNT, TokenClass.DATE) and is_multi_token:
        hv_tokens = _high_value_subtokens(embedded, tok_class, cfg)
        folded_ocr = fold(ocr.text, cfg)
        hv_intact = all(fold(tok, cfg) in folded_ocr for tok in hv_tokens)
        prose_ok = is_within_tolerance(joined_emb, ocr.text, TokenClass.PROSE, cfg)
        agree = hv_intact and prose_ok
    else:
        agree = is_within_tolerance(joined_emb, ocr.text, tok_class, cfg)

    if agree:
        div_type = DivergenceType.AGREE
    else:
        div_type = DivergenceType.MISMATCH

    return Divergence(
        type=div_type,
        embedded=embedded,
        ocr=ocr,
        token_class=tok_class,
        weight=weight_for(div_type, tok_class, cfg),
        page_index=page,
    )


def classify_unmatched(
    word: WordBox,
    config: OCRCrossCheckConfig | None = None,
) -> Divergence:
    """Classify an unmatched word as EMBEDDED_ONLY or OCR_ONLY (by its source).

    The token class is determined from the individual word's text; a single-token
    unmatched currency amount will already carry AMOUNT class and full weight.
    """
    cfg = config or OCRCrossCheckConfig()

    # Pick the most-sensitive token class from the word's own tokens.
    tok_class = TokenClass.PROSE
    for tok in word.text.split():
        cls = _classify_for_elevation(tok, cfg)
        if _CLASS_PRIORITY[cls] < _CLASS_PRIORITY[tok_class]:
            tok_class = cls

    if word.source is WordSource.EMBEDDED:
        div_type = DivergenceType.EMBEDDED_ONLY
        embedded = (word,)
        ocr_box = None
    else:
        div_type = DivergenceType.OCR_ONLY
        embedded = ()
        ocr_box = word

    return Divergence(
        type=div_type,
        embedded=embedded,
        ocr=ocr_box,
        token_class=tok_class,
        weight=weight_for(div_type, tok_class, cfg),
        page_index=word.page_index,
    )


def classify_page(
    groups: list[tuple[tuple[WordBox, ...], WordBox]],
    unmatched_embedded: list[WordBox],
    unmatched_ocr: list[WordBox],
    config: OCRCrossCheckConfig | None = None,
) -> list[Divergence]:
    """Classify every comparison on one page into :class:`Divergence` objects.

    Returns AGREE / MISMATCH divergences for matched groups, then
    EMBEDDED_ONLY / OCR_ONLY for residues — in that order.
    """
    cfg = config or OCRCrossCheckConfig()
    result: list[Divergence] = []

    for emb_words, ocr_word in groups:
        result.append(classify_group(emb_words, ocr_word, cfg))

    for w in unmatched_embedded:
        result.append(classify_unmatched(w, cfg))

    for w in unmatched_ocr:
        result.append(classify_unmatched(w, cfg))

    return result


__all__ = ["weight_for", "classify_group", "classify_unmatched", "classify_page"]
