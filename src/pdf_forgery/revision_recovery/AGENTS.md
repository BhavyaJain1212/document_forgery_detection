# revision_recovery — working notes

Stage 1 (revision recovery). Detailed task-by-task history below; the canonical
spec, module layout, and cross-cutting status live in the repo-root `CLAUDE.md`.

- [x] **Stage 1 / Task 0 — Project bootstrap** (2026-06-11)
  - [x] CLAUDE.md created; spec saved verbatim.
  - [x] `pyproject.toml` (src layout; deps: pikepdf, pdfminer.six, pdfplumber).
  - [x] `.gitignore` added.
  - [x] `src/pdf_forgery/__init__.py` created (package only; no logic).
  - [x] Short `README.md` (method + how to run).
  - [x] `git init` + initial commit.
  - [x] Module layout PROPOSED in this file.
  - [x] Owner reviewed & APPROVED layout (2026-06-11) with 3 refinements:
    stage-1 subpackage `revision_recovery/`; pdfminer for text / pdfplumber only
    for OVERLAY word boxes; CLI output/exit-code rules. Layout updated.
- [x] **Stage 1 / Task 1 — revision detection (`detect.py`)** (2026-06-11)
  - [x] `revision_recovery/` subpackage created; `models.py` with detection
    dataclasses (`EOFMarker`, `XrefSection`, `RevisionBoundary`, `DetectionResult`).
  - [x] `detect.py`: pure raw-byte scan — every `%%EOF`, `startxref`+pointer,
    `/Prev` chain → ordered candidate `RevisionBoundary` list with byte offsets.
  - [x] Cheap STRUCTURAL validity flag: a clean boundary ends with
    `startxref <n>` right before `%%EOF`; an in-stream `%%EOF` lacks that tail →
    kept but `valid=False` (never dropped). Authoritative pikepdf load-test still
    belongs to `reconstruct.py`. `revision_count`/`is_multi_revision` count only
    VALID boundaries; `candidate_count`/`valid_boundaries` expose both views.
    Verified the heuristic against real qpdf output (`startxref\n<n>\n%%EOF`).
  - [x] Read-only `detect_from_path`; empty/headerless/marker-less/missing-file
    inputs reported via `notes`, never raise.
  - [x] `tests/test_detect.py` — 18 tests over synthetic bytes (single/multi
    revision, in-stream `%%EOF` flagged invalid, EOL handling, read-only
    guarantee). All pass.
  - [x] Dev env: `.venv` (Python 3.12) with deps installed; package editable-installed.
  - Note: system `pip` is Python 3.10 and PEP-668 externally-managed — use
    `./.venv/bin/python -m pytest` to run the suite.
- [x] **Stage 1 / Task 2 — revision reconstruction (`reconstruct.py`)** (2026-06-11)
  - [x] `reconstruct(raw, detection)`: for each VALID boundary, truncate
    `raw[0:truncate_len]` and load with pikepdf; returns ordered `Revision` list
    (re-indexed from 0, with page_count + is_encrypted), earliest first.
  - [x] Authoritative load gate: a boundary that won't open becomes a
    `ReconstructionFailure` (reported + skipped, never crashes, never dropped) —
    feeds the MEDIUM "detected but not reconstructed" rule. Cases handled:
    corrupt/unloadable (`pikepdf.PdfError`), encrypted-password-required
    (`pikepdf.PasswordError`), and any unexpected error (broad catch).
    Empty-user-password encryption opens and is flagged `is_encrypted=True`.
  - [x] Models: `Revision`, `ReconstructionFailure`, `ReconstructionResult`
    (with `revision_count`/`has_failures`/`is_multi_revision`).
  - [x] Read-only `reconstruct_from_path`; missing/dir/unreadable reported via notes.
  - [x] `tests/test_reconstruct.py` — 12 tests using REAL pikepdf-built PDFs incl.
    a genuine incremental-update builder (`_append_incremental`: appends obj +
    xref + `/Prev` trailer + startxref + `%%EOF`). Covers 1- & 2-revision load,
    exact-truncation + independent loadability, unloadable/encrypted failures,
    invalid-boundary skip, read-only guarantee. Suite: 30 pass.
- [x] **Stage 1 / Task 3 — `extract/text.py` + `extract/normalize.py` + `extract/words.py`** (2026-06-11)
  - [x] `normalize(text)`: NFC, strip U+200B/C/D + U+00AD (soft hyphen), collapse
    whitespace, trim. `tokenize(text)`: whitespace split. Both pure functions;
    shared by textdiff and objectdiff so they can never drift apart.
  - [x] `extract_text_per_page(data: bytes) -> list[str]`: pdfminer.six via
    `extract_pages`; collects `LTTextContainer` text per page; one raw string per
    page (0-based). Never crashes — total failure returns `[]`.
  - [x] `extract_words_per_page(data: bytes) -> list[list[WordBox]]`: pdfplumber,
    OVERLAY check only. `WordBox` frozen dataclass (`text`, `x0`, `top`, `x1`,
    `bottom`; pdfplumber coordinate system). Never crashes — failure returns `[]`.
  - [x] `tests/test_extract.py` — 47 tests: 16 normalize, 7 tokenize, 10 text
    extraction (blank/text/multi-page/malformed), 2 WordBox, 12 words extraction.
    `_text_pdf` fixture builds Type1/Helvetica pages with pikepdf content streams.
    Suite: 77 pass.
- [x] **Stage 1 / Task 4a — `diff/textdiff.py` + `highvalue.py`** (2026-06-11)
  - [x] `highvalue.py`: `classify_token(token) -> HighValueKind | None` and
    `classify_change(before, after) -> HighValueKind | None`. Three tiers:
    AMOUNT (strong) — optional `₹/$`/`Rs`/`INR` prefix + number with optional
    Indian/Western comma grouping and decimal; also standalone currency symbols.
    DATE (strong) — `dd/mm/yyyy`, `dd-mm-yyyy`, ISO 8601 `yyyy-mm-dd`.
    ID_LIKE (weak/noisy) — any token with a 6+-char alphanumeric run.
    Priority AMOUNT > DATE > ID_LIKE. All patterns are module-level compiled
    constants (Task 5/config.py will expose them as configurable overrides).
    Month-name dates span multiple tokens; noted in comment, not handled.
  - [x] `models.py` additions: `HighValueKind(str, Enum)`, `CharSpan`,
    `TokenDiff`, `PageTextDiff`, `TextChange` (pure data, frozen dataclasses).
  - [x] `diff/__init__.py` + `diff/textdiff.py`:
    `diff_normalized_pages(pages_a, pages_b, from_revision, to_revision)`
    (public, independently testable) and `diff_text(rev_a, rev_b)` (full
    pipeline). Token-level diff via `SequenceMatcher(autojunk=False)`;
    `replace` opcodes paired with `zip_longest`; char-level diff via a second
    `SequenceMatcher` on the token strings. Substantive iff >= 1 TokenDiff
    produced. High-value flag propagated from TokenDiff → PageTextDiff →
    TextChange. Page count mismatch noted. Extraction failures noted.
  - [x] `tests/test_textdiff.py` — 61 tests: 25 classify_token/classify_change,
    36 diff_normalized_pages (identical/whitespace/substantive/char-diff/
    high-value/page-alignment/indices/types) + 4 diff_text with real revisions.
    Suite: 138 pass.
- [x] **Stage 1 / Task 4b — `diff/objectdiff.py`** (2026-06-11)
  - [x] `ObjectChangeClass`, `ObjectChange`, and `ObjectDiff` models added for
    the exact rubric categories: CONTENT, SIGNATURE, MARKUP, OVERLAY, FORM_FILL,
    FIELD_EDIT, META.
  - [x] `diff_objects(rev_a, rev_b)`: opens consecutive reconstructed revisions
    with pikepdf, builds indirect-object signatures, reports every changed or
    newly added object in the later revision, and keeps errors in notes.
  - [x] `classify_changed_object(...)`: isolated/testable classifier. Handles
    page `/Contents` streams and changed page `/Contents` references as CONTENT,
    `/FT /Sig` and signature dictionaries as SIGNATURE, annotations as MARKUP or
    OVERLAY, form `/V` empty->filled as FORM_FILL, form `/V` prior-value changes
    as FIELD_EDIT, and `/Metadata`/XMP/`/Info`/catalog structural objects as META.
  - [x] OVERLAY geometry uses `extract/words.py` word boxes and converts PDF
    bottom-left coordinates to pdfplumber top-left coordinates before overlap
    checks.
  - [x] `tests/test_objectdiff.py` — 37 tests for changed-object detection,
    isolated geometry, CONTENT/SIGNATURE/MARKUP/OVERLAY/FORM_FILL/FIELD_EDIT/META
    classification, mixed changes, unloadable inputs, and grouping. Suite:
    175 pass.
- [x] **Stage 1 / Task 5 — `config.py` + `scoring.py`** (2026-06-11)
  - [x] `config.py`: `Config` dataclass — all thresholds, score bands, high-value
    regex pattern strings (`DEFAULT_AMOUNT/DATE/ID_LIKE_PATTERN`), normalisation
    toggles (`nfc_normalize`, `strip_zero_width`, `collapse_whitespace`),
    high-value enable flags (`enable_amount_pattern`, `enable_date_pattern`,
    `enable_id_like_boost`), behaviour toggle (`form_fill_triggers_medium`).
    Nothing hard-coded outside this file.
  - [x] `models.py` additions: `ConfidenceTier(str, Enum)` (INCONCLUSIVE / LOW /
    MEDIUM / HIGH) and `ScoringResult` frozen dataclass (tier, score, reasons,
    object_classes_seen, has_substantive_text_change, has_high_value_change,
    high_value_kind, revision_count, has_reconstruction_failures, notes).
  - [x] `extract/normalize.py`: accepts optional `Config`; applies NFC / strip-ZW /
    collapse-whitespace per toggles. Default (None) = all on (spec default).
  - [x] `diff/textdiff.py`: `diff_text` accepts optional `Config`; passes it to
    `normalize()` so normalisation toggles propagate end-to-end.
  - [x] `scoring.py`: explicit rule tree (not a weighted sum):
    INCONCLUSIVE (total_detected ≤ 1) → HIGH (substantive text diff + CONTENT
    object change; score driven by effective_hv_kind after toggles) → MEDIUM (any
    of: CONTENT no-text-diff / OVERLAY / FIELD_EDIT / form_fill_triggers_medium +
    FORM_FILL / recon failure; score = max of triggered conditions) → LOW (default
    benign). All score values and toggles sourced from Config. HIGH wins over MEDIUM
    when both conditions exist.
  - [x] `tests/test_scoring.py` — 48 tests: INCONCLUSIVE (4), HIGH (15), MEDIUM
    (13), LOW (9), ResultFields (7). Covers every tier boundary, custom Config
    overrides, toggle combinations, multi-pair aggregation, priority ordering.
    Suite: 223 pass.
- [x] **Stage 1 / Task 6 — `report.py` + `cli.py` (+ `analyze.py`)** (2026-06-11)
  - [x] `models.py` additions: `Finding` (revision indices, changed object id(s)
    `"<obj> <gen>"` + class, page index, token-level before/after, high-value
    flag/kind, one-line summary; `before_text`/`after_text` props) and
    `AnalysisReport` (per-file: path, `ok`/`error` for RUN success, raw_size,
    candidate/revision/failure counts, `ScoringResult`, findings, full
    text_changes + object_diffs, aggregated notes).
  - [x] **`analyze.py`** (new orchestration module, not in the original layout —
    keeps `report.py` pure-render and `cli.py` thin): `analyze_bytes(raw, path,
    config)` and read-only `analyze_path(path, config)` run detect → reconstruct
    → per-pair diff_text + diff_objects → score → `_build_findings` →
    `AnalysisReport`. Never raises; unreadable/dir/missing path → `ok=False`
    report. Findings come from substantive page text diffs (enriched with CONTENT
    object ids for that page) plus object-only flags (OVERLAY/FIELD_EDIT/
    FORM_FILL) and CONTENT-changed-but-no-text (possible overlay) so nothing is
    silently dropped.
  - [x] `report.py`: `report_to_dict` / `render_json` (single → JSON object,
    batch sequence → JSON array; no file bytes; deterministic, `ensure_ascii=
    False`) and `render_summary` (human before→after per finding: revision
    indices, object id(s)+class, page number, before/after text). Every summary
    states confidence is ADVISORY.
  - [x] `cli.py` (`pdf-forgery <path>`): no flags → summary to stdout; `--json
    <out>` (`-`=stdout) writes machine JSON and suppresses the summary unless
    `--summary` forces it; directory arg → batch over top-level `*.pdf` only (no
    recursion) as ONE combined JSON array. Exit code: 0 = run produced output
    (verdict irrelevant — HIGH still exits 0); 2 = usage/path error (missing
    path, empty dir, unwritable `--json`).
  - [x] Facade `revision_recovery/__init__.py` re-exports `analyze_path`,
    `analyze_bytes`, `render_json`, `render_summary`, `report_to_dict`,
    `AnalysisReport`, `Finding`.
  - [x] `tests/test_report.py` (10) + `tests/test_cli.py` (8): inline
    pikepdf-built incremental content-edit fixture (known-positive → HIGH) and
    single-rev fixture (known-negative → INCONCLUSIVE); cover JSON object vs
    array, summary before→after + advisory, output-rule matrix, no-recursion,
    exit codes. Suite: 241 pass. (Dedicated fixture generator + canonical
    end-to-end assertions remain Tasks 7–8.)
- [x] **Stage 1 / Task 7 — `scripts/make_fixtures.py` (known +/- cases)** (2026-06-11)
  - [x] `build_clean()` → single-revision PDF (Type1/Helvetica content stream:
    prose + `Approved claim amount: Rs 5,000`). Saved with
    `deterministic_id=True`; `Pdf.new()` writes no dates → byte-stable output.
    This is the **known-negative** (one revision → INCONCLUSIVE).
  - [x] `build_forged(clean)` → appends a genuine **incremental update**:
    new `/Contents` object (amount → `Rs 50,000`) + classic xref subsection +
    trailer chained via `/Prev` (to the original `startxref`) + `startxref` +
    `%%EOF`, all appended to the original bytes. Verified `clean` is an exact
    byte-prefix of the forged file (only 338 bytes appended) — original objects
    preserved, the "Save not Save-As" hallmark. **Known-positive** (HIGH 95,
    amount altered).
  - [x] `write_fixtures(dest=tests/fixtures/)` writes `clean.pdf` +
    `edited_incremental.pdf`; `main()` is the CLI. `ORIGINAL_AMOUNT` /
    `FORGED_AMOUNT` exported as constants for Task-8 assertions. Determinism
    confirmed (identical sha256 across runs).
  - [x] Detector run against the fixtures: `clean.pdf` → INCONCLUSIVE,
    `edited_incremental.pdf` → HIGH (`5,000 → 50,000`, object `4 0 [content]`,
    page 1).
  - [x] `.gitignore`: fixed the stale `tests/_fixtures/` pattern to the real
    `tests/fixtures/` path so the generated PDFs stay out of git (repo policy:
    keep the generator, not its output; fixtures are regenerated on demand).
- [x] **Stage 1 / Task 8 — end-to-end tests + README finalize** (2026-06-11)
  - [x] `tests/conftest.py`: session-scoped `fixtures` (+ `clean_pdf` /
    `forged_pdf`) built once into a tmp dir via `scripts/make_fixtures.py`
    (`scripts/` added to `sys.path`), so e2e tests run on the exact shipped
    artifacts without depending on the git-ignored `tests/fixtures/` checkout.
  - [x] `tests/test_end_to_end.py` (5): forged → HIGH, score 95, AMOUNT kind,
    reasons contain "high-value field altered", single finding on page 1 mapped
    to a CONTENT object with exact before/after `5,000` → `50,000`; clean →
    INCONCLUSIVE, score None, no findings; CLI summary shows before→after and
    JSON tiers match (verdict never affects exit code).
  - [x] `README.md` finalized: status now "Stage 1 complete, 246 tests";
    `Usage` section with the real CLI flags + output rules + confidence-tier
    table + advisory note; `Develop` documents the fixture generator.
  - [x] Full suite: **246 passed.**

### Stage 1 — DONE. All deliverables met:
package with clear modules; CLI (JSON + human summary, batch, advisory exit
codes); deterministic fixture generator (known +/-); end-to-end tests asserting
HIGH-on-positive / INCONCLUSIVE-on-negative; README. Later stages
(`font_fingerprint/`, `ocr_crosscheck/`) plug in as sibling subpackages.
