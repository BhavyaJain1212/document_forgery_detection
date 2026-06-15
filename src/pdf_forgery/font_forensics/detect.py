"""Font-inconsistency detectors over per-character glyph attribution.

Evidence is evaluated in descending reliability: token-internal consistency,
line-local context, page-local baseline, then document-global fallback. Only a
font seam *inside* one token can independently produce HIGH. A uniformly-set
token that merely differs from its surroundings is supporting evidence only.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from .config import FontConfig
from .extract import dominant_font, group_lines
from .fonts import (
    is_substitution,
    parse_font_identity,
    same_base_different_subset,
)
from .models import (
    ClassificationStrength,
    ConfidenceTier,
    FontFinding,
    FontFindingKind,
    Glyph,
    HighValueKind,
    TextLine,
    Token,
    TokenCandidate,
)
from ..revision_recovery.extract.normalize import normalize
from ..revision_recovery.highvalue import TokenClassification, classify_token


def detect_findings(
    glyphs: list[Glyph], config: FontConfig | None = None
) -> tuple[list[FontFinding], list[TextLine]]:
    """Run both detectors over *glyphs*; return findings and the grouped lines.

    The lines are returned too so callers (analyze / tests) can inspect the
    grouping without re-computing it.
    """
    cfg = config or FontConfig()
    lines = group_lines(glyphs, cfg)
    doc_baseline = dominant_font(glyphs)
    page_baselines = {
        page_index: dominant_font(g for g in glyphs if g.page_index == page_index)
        for page_index in {g.page_index for g in glyphs}
    }
    pervasive = _intra_token_mixing_is_pervasive(lines, cfg)

    findings: list[FontFinding] = []
    for line in lines:
        findings.extend(
            _analyze_line(
                line,
                page_baselines.get(line.page_index),
                doc_baseline,
                pervasive,
                cfg,
            )
        )
    # Stable, reviewer-friendly order: by page, then by tier severity, then x.
    findings.sort(key=lambda f: (f.page_index, _TIER_RANK[f.tier], f.bbox[0]))
    return findings, lines


# Higher rank = listed first (more severe).
_TIER_RANK = {
    ConfidenceTier.HIGH: 0,
    ConfidenceTier.MEDIUM: 1,
    ConfidenceTier.LOW: 2,
    ConfidenceTier.INCONCLUSIVE: 3,
}


def _classification(token: Token, line: TextLine) -> TokenClassification | None:
    """Structured classification using nearby tokens on the same line."""
    text = normalize(token.text)
    if not text:
        return None
    try:
        index = next(i for i, candidate in enumerate(line.tokens) if candidate is token)
    except StopIteration:
        index = 0
    nearby = [
        candidate.text
        for i, candidate in enumerate(line.tokens)
        if candidate is not token and abs(i - index) <= 3
    ]
    return classify_token(text, context=nearby)


def _hv_kind(token: Token, line: TextLine) -> HighValueKind | None:
    classification = _classification(token, line)
    return classification.high_value_kind if classification is not None else None


def _analyze_line(
    line: TextLine,
    page_baseline: str | None,
    doc_baseline: str | None,
    pervasive_mix: bool,
    cfg: FontConfig,
) -> list[FontFinding]:
    non_space = [g for g in line.glyphs if not g.is_space]
    line_fonts = {g.fontname for g in non_space}
    findings: list[FontFinding] = []

    if len(line_fonts) <= 1:
        # Uniform line: no token-internal seam is possible. Prefer page-local
        # comparison; use document-global only as a weak fallback.
        if cfg.enable_baseline_deviation and doc_baseline is not None:
            findings.extend(
                _baseline_deviation(
                    line, non_space, page_baseline, doc_baseline, cfg
                )
            )
        return findings

    # Strongest evidence first: inspect every glyph within each token. Coarser
    # line-context findings are skipped for tokens already covered here.
    flagged_token_ids: set[int] = set()
    flagged_glyph_ids: set[int] = set()
    if cfg.enable_intra_token_mix:
        intra_findings, intra_token_ids = _intra_token_mix_findings(
            line, pervasive_mix, cfg
        )
        findings.extend(intra_findings)
        flagged_token_ids |= intra_token_ids
        for token in line.tokens:
            if id(token) in intra_token_ids:
                flagged_glyph_ids.update(id(g) for g in token.glyphs)

    # Second: whole-token differences against line-local context. These are
    # supporting evidence and are capped below HIGH.
    for token in line.tokens:
        if id(token) in flagged_token_ids:
            continue
        f = _analyze_token_in_line(line, token, cfg)
        if f is not None:
            findings.append(f)
            flagged_token_ids.add(id(token))
            flagged_glyph_ids.update(id(g) for g in token.glyphs)

    # Remaining same-base subset splits not tied to a high-value token -> MEDIUM.
    if cfg.enable_subset_split:
        subset_findings, subset_token_ids = _intra_line_subset_splits(
            line, flagged_glyph_ids, cfg
        )
        findings.extend(subset_findings)
        flagged_token_ids |= subset_token_ids

    return findings


def _analyze_token_in_line(
    line: TextLine, token: Token, cfg: FontConfig
) -> FontFinding | None:
    """Supporting check: does a uniform token differ from line-local context?"""
    classification = _classification(token, line)
    if classification is None:
        return None
    hv = classification.high_value_kind
    # Whole-token identifiers are commonly styled differently from their labels
    # (ABN, account number, invoice number). Without an internal seam this is not
    # useful tamper evidence.
    if classification.primary is TokenCandidate.IDENTIFIER:
        return None

    token_glyphs = [g for g in token.glyphs if not g.is_space]
    if not token_glyphs:
        return None
    token_font = dominant_font(token_glyphs)
    if token_font is None:
        return None

    context_glyphs = [
        g
        for g in line.glyphs
        if not g.is_space and id(g) not in {id(t) for t in token.glyphs}
    ]
    if len(context_glyphs) < cfg.min_context_glyphs:
        return None
    context_font = dominant_font(context_glyphs)
    if context_font is None or context_font == token_font:
        return None

    tok_id = parse_font_identity(token_font)
    ctx_id = parse_font_identity(context_font)

    if cfg.enable_subset_split and same_base_different_subset(tok_id, ctx_id):
        kind = FontFindingKind.WHOLE_TOKEN_SUBSET_DIFFERENCE
        reason = (
            f"Uniform token {token.text!r} uses {token_font!r}, a different "
            f"subset of {ctx_id.base!r} than its line ({context_font!r}); "
            "supporting evidence only because the token is internally consistent"
        )
    elif cfg.enable_substitution and is_substitution(tok_id, ctx_id):
        kind = FontFindingKind.WHOLE_TOKEN_FAMILY_DIFFERENCE
        reason = (
            f"Uniform token {token.text!r} uses {token_font!r}, a different font "
            f"family than its line ({context_font!r}); supporting evidence only "
            "because no glyph-level seam exists inside the token"
        )
    else:
        # Same base no tag split, or a mere style variant (bold/italic) -> benign.
        return None

    # Strong amount/date context can justify review, but weak or ambiguous
    # numeric classification is capped at LOW.
    tier = (
        ConfidenceTier.MEDIUM
        if hv in (HighValueKind.AMOUNT, HighValueKind.DATE)
        and classification.strength is ClassificationStrength.STRONG
        else ConfidenceTier.LOW
    )
    return FontFinding(
        page_index=line.page_index,
        kind=kind,
        tier=tier,
        token=token.text,
        token_font=token_font,
        context_font=context_font,
        bbox=token.bbox,
        reason=reason,
        high_value=hv,
        classification_strength=classification.strength,
        classification_candidates=classification.candidates,
        classification_signals=classification.signals,
        baseline_scope="line",
        conflicting_fonts=tuple(sorted({token_font, context_font})),
    )


def _intra_line_subset_splits(
    line: TextLine, flagged_glyph_ids: set[int], cfg: FontConfig
) -> tuple[list[FontFinding], set[int]]:
    """MEDIUM: same-base / different-subset splits NOT on a high-value token.

    Returns the findings plus the set of ``id(token)`` it flagged, so the caller
    can dedup the later intra-token-mix pass against them.
    """
    # Group this line's fonts by base face -> set of subset tags seen.
    idents = {fn: parse_font_identity(fn) for fn in {g.fontname for g in line.glyphs if not g.is_space}}
    by_base: dict[str, set[str]] = {}
    for ident in idents.values():
        if ident.subset_tag is not None:
            by_base.setdefault(ident.base, set()).add(ident.subset_tag)
    split_bases = {base for base, tags in by_base.items() if len(tags) > 1}
    if not split_bases:
        return [], set()

    line_font = dominant_font(line.glyphs)
    findings: list[FontFinding] = []
    flagged_token_ids: set[int] = set()
    seen_tokens: set[str] = set()
    for token in line.tokens:
        if all(id(g) in flagged_glyph_ids for g in token.glyphs):
            continue  # already covered by a HIGH finding
        token_glyphs = [g for g in token.glyphs if not g.is_space]
        token_font = dominant_font(token_glyphs)
        if token_font is None or token_font == line_font:
            continue
        tident = parse_font_identity(token_font)
        if tident.base not in split_bases or tident.subset_tag is None:
            continue
        flagged_token_ids.add(id(token))
        key = (token.text, token_font)
        if key in seen_tokens:
            continue
        seen_tokens.add(key)
        findings.append(
            FontFinding(
                page_index=line.page_index,
                kind=FontFindingKind.INTRA_LINE_SUBSET_SPLIT,
                tier=ConfidenceTier.MEDIUM,
                token=token.text,
                token_font=token_font,
                context_font=line_font or "",
                bbox=token.bbox,
                reason=(
                    f"Intra-line subset switch: token {token.text!r} uses "
                    f"{token_font!r}, a different subset of {tident.base!r} than "
                    f"the rest of its line ({line_font!r})"
                ),
                high_value=None,
                conflicting_fonts=tuple(sorted({token_font, line_font or ""})),
            )
        )
    return findings, flagged_token_ids


# ---------------------------------------------------------------------------
# Intra-token font mixing (FIX 1): a minority glyph inside one token in a
# different font than the token's own majority — masked by a per-token
# dominant-font view.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _IntraMix:
    """Result of inspecting one token's glyphs for an in-token font seam."""

    token_font: str
    """Majority font WITHIN the token (the anchor we measure minorities against)."""

    minority_font: str
    """Representative (most common) suspicious minority font."""

    suspicious_text: str
    suspicious_indexes: tuple[int, ...]
    suspicious_bboxes: tuple[tuple[float, float, float, float], ...]
    same_base_split: bool
    """True when the seam is a same-base / different-subset split (vs a different
    family substitution)."""


def _token_intra_mix(token: Token, cfg: FontConfig) -> _IntraMix | None:
    """Inspect EVERY non-space glyph in *token* for a minority foreign font.

    Returns an :class:`_IntraMix` when one or more minority glyphs use a
    different font family, or the same base face with a different subset tag,
    than the token's majority font — otherwise ``None``. Placeholder/unknown
    fonts (majority or minority) never count, so they cannot independently raise
    a finding. Whole-token style changes (one uniform bold/italic font) and pure
    style-variant mixing (Helvetica + Helvetica-Bold) are not seams and return
    ``None``.
    """
    non_space = [g for g in token.glyphs if not g.is_space]
    if len(non_space) < 2:
        return None
    if len({g.fontname for g in non_space}) <= 1:
        return None  # uniform token (incl. a fully bold/italic token)

    token_font = dominant_font(non_space)
    if token_font is None:
        return None
    tok_id = parse_font_identity(token_font)
    if tok_id.is_placeholder:
        return None  # majority font unnamed -> cannot anchor a confident call

    suspicious_indexes: list[int] = []
    suspicious_chars: list[str] = []
    suspicious_bboxes: list[tuple[float, float, float, float]] = []
    minority_fonts: list[str] = []
    same_base_hits = 0
    for idx, g in enumerate(non_space):
        if g.fontname == token_font:
            continue
        g_id = parse_font_identity(g.fontname)
        if g_id.is_placeholder:
            continue  # unknown/placeholder minority -> not independent evidence
        same_base = same_base_different_subset(g_id, tok_id)
        if same_base or is_substitution(g_id, tok_id):
            suspicious_indexes.append(idx)
            suspicious_chars.append(g.text)
            suspicious_bboxes.append((g.x0, g.y0, g.x1, g.y1))
            minority_fonts.append(g.fontname)
            if same_base:
                same_base_hits += 1

    if not suspicious_indexes:
        return None

    rep = Counter(minority_fonts).most_common(1)[0][0]
    return _IntraMix(
        token_font=token_font,
        minority_font=rep,
        suspicious_text="".join(suspicious_chars),
        suspicious_indexes=tuple(suspicious_indexes),
        suspicious_bboxes=tuple(suspicious_bboxes),
        # Classify the seam by the representative minority font.
        same_base_split=same_base_different_subset(parse_font_identity(rep), tok_id),
    )


def _intra_token_mixing_is_pervasive(
    lines: list[TextLine], cfg: FontConfig
) -> bool:
    """Base-rate guard: is intra-token font mixing the producer's normal style?

    Counts how many multi-glyph tokens across the whole document carry a
    suspicious in-token font seam. If that fraction is high (>= the configured
    ratio over a non-trivial sample), the mixing is systemic — not a local edit
    seam — so findings are later downgraded. The Acrobat case (1 of ~150) sits
    far below the threshold and is therefore NOT suppressed.
    """
    inspected = 0
    mixed = 0
    for line in lines:
        for token in line.tokens:
            if sum(1 for g in token.glyphs if not g.is_space) < 2:
                continue
            inspected += 1
            if _token_intra_mix(token, cfg) is not None:
                mixed += 1
    if inspected < cfg.intra_token_pervasive_min_tokens or inspected == 0:
        return False
    return mixed / inspected >= cfg.intra_token_pervasive_ratio


def _intra_token_mix_findings(
    line: TextLine,
    pervasive_mix: bool,
    cfg: FontConfig,
) -> tuple[list[FontFinding], set[int]]:
    """Emit strongest-priority findings for tokens with an internal font seam."""
    line_font = dominant_font(line.glyphs) or ""
    findings: list[FontFinding] = []
    flagged_token_ids: set[int] = set()
    for token in line.tokens:
        mix = _token_intra_mix(token, cfg)
        if mix is None:
            continue

        classification = _classification(token, line)
        hv = classification.high_value_kind if classification is not None else None
        confident_high_value = (
            hv is not None
            and classification is not None
            and classification.strength
            in (ClassificationStrength.STRONG, ClassificationStrength.MEDIUM)
        )
        if confident_high_value:
            tier = ConfidenceTier.HIGH
        elif cfg.flag_intra_token_mix_prose:
            tier = ConfidenceTier.MEDIUM
        else:
            continue  # prose mixing disabled

        # Base-rate guard: downgrade one tier when mixing is systemic.
        if pervasive_mix:
            if tier is ConfidenceTier.HIGH:
                tier = ConfidenceTier.MEDIUM
            else:
                continue  # a downgraded prose MEDIUM isn't worth surfacing

        seam = "subset" if mix.same_base_split else "family"
        guard = " (downgraded: intra-token mixing is pervasive in this document)" if pervasive_mix else ""
        if confident_high_value:
            reason = (
                f"High-value {hv.value} token {token.text!r}: glyph(s) "
                f"{mix.suspicious_text!r} at index {list(mix.suspicious_indexes)} "
                f"set in {mix.minority_font!r}, a different {seam} than the token's "
                f"majority font {mix.token_font!r}{guard}"
            )
        elif classification is not None:
            reason = (
                f"Uncertain {classification.primary.value} token {token.text!r}: "
                f"glyph(s) {mix.suspicious_text!r} at index "
                f"{list(mix.suspicious_indexes)} use {mix.minority_font!r}, a "
                f"different {seam} than majority font {mix.token_font!r}; "
                f"classification strength {classification.strength.value}{guard}"
            )
        else:
            reason = (
                f"Token {token.text!r}: glyph(s) {mix.suspicious_text!r} at index "
                f"{list(mix.suspicious_indexes)} set in {mix.minority_font!r}, a "
                f"different {seam} than the token's majority font "
                f"{mix.token_font!r}{guard}"
            )

        findings.append(
            FontFinding(
                page_index=line.page_index,
                kind=FontFindingKind.INTRA_TOKEN_FONT_MIX,
                tier=tier,
                token=token.text,
                token_font=mix.token_font,
                context_font=line_font,
                bbox=token.bbox,
                reason=reason,
                high_value=hv,
                classification_strength=(
                    classification.strength if classification is not None else None
                ),
                classification_candidates=(
                    classification.candidates if classification is not None else ()
                ),
                classification_signals=(
                    classification.signals if classification is not None else ()
                ),
                baseline_scope="token",
                conflicting_fonts=tuple(sorted({mix.token_font, mix.minority_font})),
                minority_font=mix.minority_font,
                suspicious_text=mix.suspicious_text,
                suspicious_glyph_indexes=mix.suspicious_indexes,
                suspicious_bboxes=mix.suspicious_bboxes,
            )
        )
        flagged_token_ids.add(id(token))
    return findings, flagged_token_ids


def _baseline_deviation(
    line: TextLine,
    non_space: list[Glyph],
    page_baseline: str | None,
    doc_baseline: str,
    cfg: FontConfig,
) -> list[FontFinding]:
    """Compare a uniform line with page baseline, then weak document fallback."""
    line_font = dominant_font(non_space)
    if line_font is None:
        return []
    baseline = page_baseline
    scope = "page"
    kind = FontFindingKind.PAGE_BASELINE_DEVIATION
    tier = ConfidenceTier.MEDIUM
    if baseline is None or baseline == line_font:
        baseline = doc_baseline
        scope = "document"
        kind = FontFindingKind.DOCUMENT_BASELINE_DEVIATION
        tier = ConfidenceTier.LOW
    if baseline == line_font:
        return []

    base_id = parse_font_identity(baseline)
    line_id = parse_font_identity(line_font)
    if not is_substitution(line_id, base_id):
        return []  # same base / style variant -> normal styling

    findings: list[FontFinding] = []
    for token in line.tokens:
        classification = _classification(token, line)
        if classification is None:
            continue
        hv = classification.high_value_kind
        if hv not in (HighValueKind.AMOUNT, HighValueKind.DATE):
            continue
        finding_tier = tier
        if classification.strength is not ClassificationStrength.STRONG:
            finding_tier = ConfidenceTier.LOW
        findings.append(
            FontFinding(
                page_index=line.page_index,
                kind=kind,
                tier=finding_tier,
                token=token.text,
                token_font=line_font,
                context_font=baseline,
                bbox=token.bbox,
                reason=(
                    f"Uniform {hv.value} token {token.text!r} uses {line_font!r}, "
                    f"a different family than the {scope} baseline ({baseline!r}); "
                    f"{scope}-baseline evidence cannot independently produce HIGH"
                ),
                high_value=hv,
                classification_strength=classification.strength,
                classification_candidates=classification.candidates,
                classification_signals=classification.signals,
                baseline_scope=scope,
                conflicting_fonts=tuple(sorted({line_font, baseline})),
            )
        )
    return findings


# Re-exported for tests that want to drive the line/token checks directly.
__all__ = ["detect_findings"]
