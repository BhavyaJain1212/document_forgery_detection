"""Font-inconsistency detectors over per-character glyph attribution.

Two detectors, both gated on overlap with a high-value token (amount / date /
ID) so we only escalate when a sensitive field is involved:

1. **Subset-tag inconsistency** — within one text line, the same base face
   appears with two different 6-letter subset tags (``ABCDEF+Arial`` vs
   ``GHIJKL+Arial``). If the odd tag covers a high-value token -> HIGH
   (re-embedding fingerprint); otherwise -> MEDIUM (intra-line prose switch).

2. **Document-baseline deviation** — a high-value token whose font family
   differs from the dominant family of its surrounding line context (font
   substitution -> HIGH), or, when its line has no context to compare, from the
   document baseline family (-> MEDIUM).

High-value classification reuses ``revision_recovery.highvalue`` and the shared
normaliser so the two stages identify amounts/dates/IDs identically.
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
    ConfidenceTier,
    FontFinding,
    FontFindingKind,
    Glyph,
    HighValueKind,
    TextLine,
    Token,
)
from ..revision_recovery.extract.normalize import normalize
from ..revision_recovery.highvalue import classify_token


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
    pervasive = _intra_token_mixing_is_pervasive(lines, cfg)

    findings: list[FontFinding] = []
    for line in lines:
        findings.extend(_analyze_line(line, doc_baseline, pervasive, cfg))
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


def _hv_kind(token: Token) -> HighValueKind | None:
    """High-value classification of a token's normalised text."""
    text = normalize(token.text)
    if not text:
        return None
    return classify_token(text)


def _analyze_line(
    line: TextLine, doc_baseline: str | None, pervasive_mix: bool, cfg: FontConfig
) -> list[FontFinding]:
    non_space = [g for g in line.glyphs if not g.is_space]
    line_fonts = {g.fontname for g in non_space}
    findings: list[FontFinding] = []

    if len(line_fonts) <= 1:
        # Uniform line: no intra-token mix possible (one font everywhere); only
        # the cross-line baseline-deviation detector applies.
        if cfg.enable_baseline_deviation and doc_baseline is not None:
            findings.extend(_baseline_deviation(line, non_space, doc_baseline, cfg))
        return findings

    # Multi-font line: per-token line-context checks.
    flagged_token_ids: set[int] = set()
    flagged_glyph_ids: set[int] = set()
    for token in line.tokens:
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

    # Minority glyph(s) INSIDE a single token (the masked single-glyph edit),
    # skipping any token a coarser detector already flagged (dedup).
    if cfg.enable_intra_token_mix:
        findings.extend(
            _intra_token_mix_findings(line, flagged_token_ids, pervasive_mix, cfg)
        )
    return findings


def _analyze_token_in_line(
    line: TextLine, token: Token, cfg: FontConfig
) -> FontFinding | None:
    """HIGH check: does a high-value token's font break its line context?"""
    hv = _hv_kind(token)
    if hv is None:
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
        kind = FontFindingKind.HIGH_VALUE_SUBSET_SPLIT
        reason = (
            f"High-value {hv.value} token {token.text!r} re-embedded as "
            f"{token_font!r}, a different subset of {ctx_id.base!r} than its "
            f"line ({context_font!r})"
        )
    elif cfg.enable_substitution and is_substitution(tok_id, ctx_id):
        kind = FontFindingKind.HIGH_VALUE_SUBSTITUTION
        reason = (
            f"High-value {hv.value} token {token.text!r} set in {token_font!r}, "
            f"a different font family than its line ({context_font!r})"
        )
    else:
        # Same base no tag split, or a mere style variant (bold/italic) -> benign.
        return None

    return FontFinding(
        page_index=line.page_index,
        kind=kind,
        tier=ConfidenceTier.HIGH,
        token=token.text,
        token_font=token_font,
        context_font=context_font,
        bbox=token.bbox,
        reason=reason,
        high_value=hv,
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
    flagged_token_ids: set[int],
    pervasive_mix: bool,
    cfg: FontConfig,
) -> list[FontFinding]:
    """Emit findings for tokens with an in-token font seam (deduped)."""
    line_font = dominant_font(line.glyphs) or ""
    findings: list[FontFinding] = []
    for token in line.tokens:
        if id(token) in flagged_token_ids:
            continue  # a coarser detector already covered this token
        mix = _token_intra_mix(token, cfg)
        if mix is None:
            continue

        hv = _hv_kind(token)
        if hv is not None:
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
        if hv is not None:
            reason = (
                f"High-value {hv.value} token {token.text!r}: glyph(s) "
                f"{mix.suspicious_text!r} at index {list(mix.suspicious_indexes)} "
                f"set in {mix.minority_font!r}, a different {seam} than the token's "
                f"majority font {mix.token_font!r}{guard}"
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
                conflicting_fonts=tuple(sorted({mix.token_font, mix.minority_font})),
                minority_font=mix.minority_font,
                suspicious_text=mix.suspicious_text,
                suspicious_glyph_indexes=mix.suspicious_indexes,
                suspicious_bboxes=mix.suspicious_bboxes,
            )
        )
    return findings


def _baseline_deviation(
    line: TextLine, non_space: list[Glyph], doc_baseline: str, cfg: FontConfig
) -> list[FontFinding]:
    """MEDIUM: a high-value token on a uniform line deviates from doc baseline."""
    line_font = dominant_font(non_space)
    if line_font is None or line_font == doc_baseline:
        return []
    base_id = parse_font_identity(doc_baseline)
    line_id = parse_font_identity(line_font)
    if not is_substitution(line_id, base_id):
        return []  # same base / style variant -> normal styling

    findings: list[FontFinding] = []
    for token in line.tokens:
        hv = _hv_kind(token)
        if hv is None:
            continue
        if cfg.baseline_deviation_strong_only and hv is HighValueKind.ID_LIKE:
            continue
        findings.append(
            FontFinding(
                page_index=line.page_index,
                kind=FontFindingKind.HIGH_VALUE_BASELINE_DEVIATION,
                tier=ConfidenceTier.MEDIUM,
                token=token.text,
                token_font=line_font,
                context_font=doc_baseline,
                bbox=token.bbox,
                reason=(
                    f"High-value {hv.value} token {token.text!r} set in "
                    f"{line_font!r}, a different family than the document "
                    f"baseline ({doc_baseline!r}); no line context to compare"
                ),
                high_value=hv,
                conflicting_fonts=tuple(sorted({line_font, doc_baseline})),
            )
        )
    return findings


# Re-exported for tests that want to drive the line/token checks directly.
__all__ = ["detect_findings"]
