"""Central configuration for the font-forensics stage.

Every threshold, score value, and detector toggle lives here so nothing magic is
hard-coded in the detectors or scorer. Pass a :class:`FontConfig` to the public
API; pass ``None`` for the defaults (equivalent to ``FontConfig()``).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FontConfig:
    """Tunable parameters for font-forensics extraction, detection, scoring."""

    # ------------------------------------------------------------------ #
    # Line / token grouping                                               #
    # ------------------------------------------------------------------ #

    line_baseline_tolerance: float = 0.5
    """Two glyphs share a line when their baselines (y0) differ by at most this
    fraction of the larger glyph size. Larger = more glyphs merged per line."""

    token_gap_ratio: float = 0.35
    """A horizontal gap wider than this fraction of glyph size starts a new
    token even without an explicit space glyph."""

    min_glyphs_for_analysis: int = 2
    """Below this many non-space glyphs there is nothing meaningful to compare;
    the stage reports INCONCLUSIVE."""

    min_context_glyphs: int = 3
    """A line's surrounding context must have at least this many non-space,
    non-token glyphs before a 'breaks line context' judgement is trusted."""

    # ------------------------------------------------------------------ #
    # Detector toggles                                                    #
    # ------------------------------------------------------------------ #

    enable_subset_split: bool = True
    """Detect same-base / different-subset-tag splits within a line."""

    enable_substitution: bool = True
    """Detect different-family substitution on a high-value token in a line."""

    enable_baseline_deviation: bool = True
    """Detect high-value tokens on uniform lines whose family differs from the
    document baseline (no line context). Conservative; strong kinds only."""

    baseline_deviation_strong_only: bool = True
    """Restrict baseline-deviation findings to AMOUNT/DATE tokens (exclude the
    noisier ID_LIKE) to keep false positives down."""

    enable_intra_token_mix: bool = True
    """Detect minority glyph(s) INSIDE a single token whose font family/subset
    differs from the token's majority font (a single inserted/edited glyph in a
    foreign font that the dominant-font-per-token view would otherwise mask)."""

    flag_intra_token_mix_prose: bool = True
    """Also flag intra-token font mixing on NON-high-value (prose) tokens as
    MEDIUM. When False, only amount/date/ID tokens are inspected."""

    # ------------------------------------------------------------------ #
    # Intra-token-mix base-rate guard                                     #
    # ------------------------------------------------------------------ #

    intra_token_pervasive_ratio: float = 0.30
    """If the fraction of inspected tokens that exhibit intra-token font mixing
    meets or exceeds this, the mixing is treated as the producer's normal
    embedding style (not a local edit seam) and findings are downgraded one tier.
    The Acrobat case (1 mixed token of ~150) sits far below this, so it is not
    suppressed."""

    intra_token_pervasive_min_tokens: int = 8
    """Minimum number of inspected tokens before the pervasive-mixing guard can
    fire, so a tiny document cannot trip it on one or two tokens."""

    # ------------------------------------------------------------------ #
    # Score values within each band (HIGH 70-100 / MEDIUM 30-70 / LOW 0-30)
    # ------------------------------------------------------------------ #

    score_high_amount_date: int = 95
    """HIGH score when the broken high-value token is an AMOUNT or DATE."""

    score_high_id_like: int = 80
    """HIGH score when the broken high-value token is only ID_LIKE (weaker)."""

    score_medium_subset_split: int = 55
    """MEDIUM score for an intra-line subset split off a high-value token."""

    score_medium_whole_token_difference: int = 55
    """MEDIUM ceiling for a uniformly-rendered amount/date token whose font
    differs from line-local context. It can never independently reach HIGH."""

    score_medium_baseline_deviation: int = 45
    """MEDIUM score for a high-value baseline deviation with no line context."""

    score_medium_intra_token_mix: int = 50
    """MEDIUM score for intra-token font mixing on a prose (non-high-value)
    token, or for a downgraded high-value intra-token mix under the base-rate
    guard."""

    score_low_default: int = 15
    """LOW score: multi-font document, only benign styling variation found."""

    score_low_document_fallback: int = 20
    """LOW score when only document-global baseline evidence is available."""
