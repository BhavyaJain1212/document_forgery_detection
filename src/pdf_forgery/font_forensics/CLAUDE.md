# font_forensics — working notes

Stage 2 (font / subset inconsistency). The 2026-06-14 bug-fix session below also
touches `core/glyphs.py` (shared extractor) and `revision_recovery` (glyph
fallback). Canonical spec / layout / status live in the repo-root `CLAUDE.md`.

- [x] **Stage 2 — `font_forensics/` (font/subset inconsistency)** (2026-06-12)
  - Goal: catch text edited BEFORE the PDF was flattened, in single-revision
    files where `revision_recovery` returns INCONCLUSIVE. Uses pdfminer's
    PER-CHARACTER attribution (`LTChar.fontname` / size / bbox), never page-level
    font lists, so per-glyph font switches survive.
  - [x] `fonts.py` (pure): `parse_font_identity` → subset tag / base / family;
    `same_base_different_subset` (the re-embedding fingerprint: same base face,
    two different 6-letter subset tags); `is_style_variant` vs `is_substitution`
    (Helvetica vs Helvetica-Bold = benign emphasis, NOT a substitution — the key
    false-positive control).
  - [x] `extract.py`: flatten `LTChar`s to `Glyph`s (`glyphs_from_layouts` /
    `glyphs_from_bytes`), own deterministic line clustering by baseline + token
    split on spaces/wide gaps, `dominant_font` / `distinct_fonts`. Tolerant: bad
    input → `[]`.
  - [x] `detect.py` — two detectors, both escalated ONLY when overlapping a
    high-value token (reuses `revision_recovery.highvalue.classify_token` +
    shared `normalize`): (1) intra-line subset-tag split; (2) line-context /
    document-baseline family deviation. HIGH = high-value token whose font/subset
    breaks its line context (subset split or substitution); MEDIUM = intra-line
    subset split off a high-value token, or a high-value baseline deviation on a
    uniform line; benign style variants are not flagged.
  - [x] `config.py` `FontConfig` (all thresholds/score values/detector toggles),
    `scoring.py` rule tree (INCONCLUSIVE single-font / LOW benign multi-font /
    MEDIUM / HIGH), `models.py` (`Glyph`, `Token`, `TextLine`, `FontFinding`,
    `FontFindingKind`, `FontReport`), `analyze.py`
    (`analyze_bytes`/`analyze_path` → `FontReport`; `_as_stage` → `StageResult`;
    consumes `ctx.page_layouts` when present), `adapter.py` (`FontReport` ↔
    `StageResult`, JSON + advisory human summary), `stage.py` `FontForensicsStage`
    (conforms to `core.Stage`). Facade `__init__` re-exports the public surface.
  - [x] `scripts/make_font_fixtures.py` (deterministic, single-revision, importable
    by tests like `make_fixtures`): KNOWN-POSITIVE `font_edited_subset.pdf` —
    amount `50,000` re-embedded as `GHIJKL+Helvetica` while its line label is
    `ABCDEF+Helvetica` (subset fonts carry a FontDescriptor + constant Widths so
    pdfminer attributes the subset name with real glyph geometry) → **HIGH 95**;
    KNOWN-NEGATIVE `font_multifont_invoice.pdf` — genuine bold-header invoice
    (`Helvetica-Bold` headers over `Helvetica` body, amount in body font) →
    **LOW** (false-positive guard). On the positive fixture `revision_recovery`
    is INCONCLUSIVE while `font_forensics` is HIGH — exactly the gap this stage
    fills.
  - [x] Tests: `test_font_fonts.py` (14 — name parsing/comparison),
    `test_font_detect.py` (15 — synthetic-glyph grouping + every tier/rule in
    isolation + scoring boundaries), `test_font_end_to_end.py` (9 — fixtures →
    HIGH/LOW, pipeline alongside revision recovery, adapter round-trip + core
    Finding evidence/bbox, JSON/summary, graceful degradation). Full suite:
    **302 passed** (264 prior, 0 regressions).

- [x] **Bug-fix session — mixed-font tokens + glyph fallback** (2026-06-14)
  - **Shared glyph extractor lifted to `core/glyphs.py`** (no duplication across
    stages): the pure `Glyph`/`Token`/`TextLine` models + per-character
    extraction (`glyphs_from_layouts`/`glyphs_from_bytes`) + line/token grouping
    (`group_lines`, params as kwargs) now live in stage-agnostic `core`.
    `font_forensics.models` re-exports the models; `font_forensics.extract`
    keeps its `FontConfig`-aware `group_lines` wrapper — existing imports/tests
    unchanged. `revision_recovery` consumes the same extractor for its fallback
    (direction stays `stages → core`, never the reverse).
  - **FIX 1 — intra-token font mixing (`font_forensics`).** The per-token
    *dominant* font masked a single inserted glyph in a foreign font. New
    detector (`_token_intra_mix` / `_intra_token_mix_findings` in `detect.py`,
    kind `INTRA_TOKEN_FONT_MIX`) inspects EVERY non-space glyph in amount/date/ID
    (and, configurably, prose) tokens, flags minority glyphs whose family/subset
    differs from the token majority (reusing `is_substitution` /
    `same_base_different_subset`, so whole-token bold/italic and pure
    style-variant mixing are NOT flagged). Placeholder/unknown fonts never
    anchor or raise a finding. New `FontFinding` evidence: `minority_font`,
    `suspicious_text`, `suspicious_glyph_indexes`, `suspicious_bboxes` (surfaced
    in adapter JSON / core `Evidence` / summary). Dedup: tokens already flagged
    by the line-context or subset-split detectors are skipped. **Base-rate
    guard** (`_intra_token_mixing_is_pervasive`): if a large fraction of
    multi-glyph tokens mix fonts (producer style, not a seam) findings downgrade
    one tier — tuned so the Acrobat case (1 of ~150 tokens) stays HIGH. Tier:
    HIGH for amount/date/ID, MEDIUM for prose; all scores via `FontConfig`.
  - **FIX 2 — glyph fallback for revision text diff (`revision_recovery`).**
    Acrobat's primary container-level extractor pulled only a fragment
    (`37004.49`) and missed the edited amount, stalling at MEDIUM 60. New
    `extract/glyph_text.py` (`glyph_page_texts`, `looks_incomplete`) reuses the
    shared glyph extractor to reconstruct per-page text. `analyze._diff_text_with_fallback`
    keeps primary extraction PRIMARY and only retries from glyphs when primary
    found no substantive change AND looks incomplete (low char ratio, or missing
    high-value tokens). The fallback result REPLACES the primary one only when it
    touches a high-value token (MEDIUM→HIGH); prose-only fallback stays advisory.
    Files where primary already works are untouched (regression guard).
    Config: `enable_glyph_fallback`, `fallback_min_glyph_chars`,
    `fallback_incomplete_ratio`.
  - **Acceptance met.** `Acrobat_Demo_File.pdf`: font_forensics → HIGH, token
    `18071.23`, suspicious char `0`, majority `YWNRZS+Calibri`, minority
    `SUMSRI+SourceSansPro-Regular`; revision_recovery → HIGH 95,
    `1871.23 → 18071.23` mapped to a changed CONTENT object.
    `Microsoft-Sample-Invoice.pdf` stays LOW / INCONCLUSIVE with no mixed-font
    findings.
  - Tests: `test_font_intra_token.py` (17 — family/subset/multi-glyph/position,
    whole-token bold+italic guards, placeholder guard, dedup, prose MEDIUM,
    base-rate downgrade), `test_revrec_glyph_fallback.py` (9 — `glyph_page_texts`,
    `looks_incomplete` both directions + high-value-gap, regression guard),
    `test_acceptance_samples.py` (5 — real-sample acceptance, both stages; skip
    if `test_pdf's/` absent). Full suite: **331 passed** (302 prior, 0
    regressions; no existing test removed or weakened).
  - Note: `tests/test_microsoft_pdf.py` is a stray untracked scratch script
    (module-level `fitz` I/O, not a pytest test) that breaks collection; runs
    use `--ignore=tests/test_microsoft_pdf.py`. Left in place pending owner
    decision to delete.

- [x] **False-positive fix — form-data fonts + vendor suffixes** (2026-06-22)
  - Clean `test_files/W2_XL_input_clean_1000.pdf` previously produced 28
    `PAGE_BASELINE_DEVIATION` MEDIUM findings because every amount used the
    form's consistent `CourierNewPS-BoldMT` data-entry font over an Arial
    template. `detect.py` now establishes a config-gated peer baseline once at
    least 5 amount/date tokens share one font at >=80% coverage, and suppresses
    only context-relative family-deviation findings for tokens in that dominant
    font. Minority amount/date fonts, same-base subset fingerprints, and
    intra-token glyph seams remain fully detectable.
  - `fonts.py` now strips trailing foundry/format markers (`PSMT`, `PS`, `MT`)
    before style comparison, so `ArialMT` / `Arial-BoldMT` resolve to the same
    Arial family while real suffixes such as `Helvetica-Neue` stay distinct.
  - New `FontConfig` controls: `suppress_consistent_form_data_font=True`,
    `form_data_font_min_tokens=5`, `form_data_font_dominant_ratio=0.80`.
  - New W-2 font-stage baseline: **LOW 15**, with the 28 false MEDIUM amount
    findings removed. Two LOW `OCRAExtended` year-label differences remain and
    do not drive suspicion. Unit coverage pins suppression, minority preservation,
    config disablement, the small-population guard, subset-path preservation,
    and vendor/style parsing.
