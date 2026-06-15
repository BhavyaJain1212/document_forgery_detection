# PDF Document-Forgery Detection — Stage 1 (Revision Recovery)

This file is the canonical spec + working memory for the project. The spec below
is saved VERBATIM from the project owner. Do not paraphrase or "improve" it.
Two maintained sections follow the spec: **Module layout** and **Progress log**.

---

## Spec (verbatim)

I'm building a PDF document-forgery detection system. This is STAGE 1 of a
larger pipeline; later stages will add font fingerprinting and OCR cross-checks.
For now, build ONLY the stage described below. Plan the architecture first and
show me the plan before writing code.

## Goal
Detect "direct text editing" forgery in PDFs via REVISION RECOVERY. When someone
edits a PDF and saves with an incremental update ("Save", not "Save As"), the
original objects remain in the file and the edit is appended as a new revision.
The detector must recover each historical revision and prove what text changed.

## Detection method (implement exactly this)
1. Read the raw bytes of the input PDF (read-only; never modify the input).
2. Find all %%EOF markers to count revisions. Also note /Prev in trailers and
   multiple xref/startxref sections. Validate candidate boundaries by attempting
   to load each truncation as a real PDF (some %%EOF may sit inside streams).
3. For each valid %%EOF boundary, truncate bytes[0:boundary] and treat that as a
   reconstructed historical revision (each is a complete, loadable PDF).
4. For each pair of consecutive revisions, compare them TWO ways:
   a. TEXT DIFF: extract the text layer per page (deterministic, local) and diff
      consecutive revisions. Report added/removed/changed spans, ignoring pure
      whitespace noise.
   b. OBJECT DIFF: find which PDF objects were overridden between revisions and
      classify each changed object by type: page content stream (/Contents) or
      text object = content change; /Sig = signature; /Annot(s) = annotation;
      /Metadata or XMP = metadata. This classification drives the confidence score.
5. SCORING (output confidence, NOT a binary verdict — a human reviewer makes the
   final call):
   - Multiple revisions + text-layer change in a content stream  -> HIGH confidence
     of text editing. Include the exact before/after text.
   - Multiple revisions but changes only in /Sig, /Annot, or metadata objects
     -> LOW confidence / likely benign (legitimate signing or markup).
   - Single revision -> INCONCLUSIVE for this method; note that later stages
     (font/OCR) are needed.

## Tech stack
- Python 3.11+
- pikepdf (qpdf bindings) for object inspection and per-revision loading
- pdfminer.six or pdfplumber for text-layer extraction
- difflib (stdlib) for diffing
- Keep dependencies minimal. Fully local — no network calls, no cloud APIs
  (the documents are sensitive insurance records).

## Deliverables
1. A Python package with clear modules: revision detection, revision
   reconstruction, text extraction, object-diff/classification, scoring, report.
2. A CLI entry point that takes a PDF path (and supports a directory for batch),
   and emits BOTH machine-readable JSON and a human-readable summary that shows
   the before/after text for any flagged change.
3. A test-fixture generator script: create a clean PDF, then produce a second
   copy that has been edited via an INCREMENTAL UPDATE (text changed, original
   preserved) so I have a known-positive case. Also generate a known-negative
   (single-revision) PDF.
4. Tests that run the detector against both fixtures and assert the expected
   confidence outcomes.

## Engineering constraints
- Treat input files as read-only.
- Handle malformed/encrypted/non-incremental PDFs gracefully — report and
  continue, never crash.
- Make modules independently testable so later stages can plug in.
- Add a short README explaining the method and how to run it.

Start by proposing the module layout and the scoring thresholds, wait for my
confirmation, then implement.

## Scoring rubric (use these exact rules; expose all thresholds as config)

### Text normalization (apply before every diff)
- Unicode NFC; collapse whitespace runs to single space; trim ends.
- Strip zero-width chars (U+200B/C/D) and soft hyphens (U+00AD).
- Case-sensitive. Compare per page, aligned by page index.
- Diff at token level (whitespace split). For changed tokens, also emit a
  character-level diff.

### Substantive change definition
- A diff is "substantive" if, after normalization, there is >=1 added/removed/
  changed token. Whitespace-only differences are NON-substantive.
- There is NO minimum token count. A single-character change counts.

### High-value token patterns (boost confidence when a CHANGED token matches)
- Currency/amount: digits with optional thousands separators/decimals, or
  adjacent to ₹, Rs, INR, $  (e.g. 5,000 / 50000 / ₹1,20,000.00)
- Date: dd/mm/yyyy, dd-mm-yyyy, ISO 8601, or month-name dates
- ID-like: alphanumeric run length >= 6 (policy/claim numbers) -- treat as a
  WEAK booster (noisier), not a strong one.

### Object-change classification (compare consecutive reconstructed revisions)
Classify every overridden object by type:
- page /Contents stream, or text-bearing form XObject -> CONTENT
- /Sig or signature appearance                         -> SIGNATURE
- /Annot(s): comment/highlight/note                    -> MARKUP
- /Annot(s): stamp/redaction OR any annot whose rect   -> OVERLAY
  geometrically overlaps a text region
- form field /V changed, field was empty before        -> FORM_FILL
- form field /V changed, field had a prior value       -> FIELD_EDIT
- /Metadata, XMP, /Info only                            -> META

### Confidence tiers (output tier + score band + the before/after evidence)
- INCONCLUSIVE (n/a): only one revision found. Route to later stages.
- LOW (0-30, likely benign): multiple revisions, but changes are confined to
  SIGNATURE and/or META and/or MARKUP and/or FORM_FILL, with NO substantive
  text change in any CONTENT object.
- MEDIUM (30-70, review): any one of --
    * a CONTENT stream changed but the normalized text diff is empty/whitespace
      only (possible overlay/inpainting the text layer doesn't reflect -> needs
      the later OCR cross-check), OR
    * an OVERLAY object changed, OR
    * a FIELD_EDIT occurred, OR
    * a revision was detected but could not be reconstructed/extracted
      (corruption or evasion -- never silently drop it).
- HIGH (70-100, strong evidence): substantive text diff that maps to a changed
  CONTENT object.
    * changed tokens match a high-value pattern (amount/date) -> 90-100,
      tag "high-value field altered"
    * prose-only changed tokens -> 70-85

### Output
For every flagged change, include: revision indices, the changed object id(s)
and class, the page number, and the exact before -> after text. Confidence is
advisory; a human reviewer decides.

---

## Module layout

> Status: **APPROVED (2026-06-11).** Stage 1 is a subpackage,
> `pdf_forgery/revision_recovery/`, so later stages (`font_fingerprint/`,
> `ocr_crosscheck/`) become sibling subpackages. Implementation underway —
> see the Progress log for what exists vs. proposed.

Package name: `pdf_forgery` (under `src/`). Python `>=3.11` (local interpreter
is 3.12; no 3.11-specific build available on this machine — 3.12 satisfies the
constraint).

```
document_forgery_detection/
├── CLAUDE.md                  # this file: spec + layout + progress log
├── README.md                  # short: method + how to run
├── pyproject.toml             # src layout, deps: pikepdf, pdfminer.six, pdfplumber
├── .gitignore
├── src/
│   └── pdf_forgery/
│       ├── __init__.py        # version
│       ├── cli.py             # shared entry point; for now dispatches to stage 1.
│       │                      #   Rules: no flags -> human summary to stdout;
│       │                      #   --json <path> writes machine JSON; --summary
│       │                      #   forces the summary even with --json; a directory
│       │                      #   arg -> batch, ONE combined JSON array (one entry
│       │                      #   per file, top-level only, no recursion); exit code
│       │                      #   reflects RUN success only, NEVER the verdict.
│       └── revision_recovery/         # STAGE 1 (this stage)
│           ├── __init__.py            # stage facade: analyze_path() re-export
│           ├── config.py              # Config dataclass: ALL thresholds, score
│           │                          #   bands, high-value regexes, normalization
│           │                          #   toggles. Nothing magic hard-coded outside.
│           ├── models.py              # dataclasses: EOFMarker, XrefSection,
│           │                          #   RevisionBoundary, DetectionResult,
│           │                          #   Revision, ObjectChange, TextChange,
│           │                          #   Finding, AnalysisReport. Pure data.
│           ├── detect.py              # scan raw bytes: %%EOF markers, /Prev chain,
│           │                          #   xref/startxref sections -> candidate
│           │                          #   RevisionBoundary list. Cheap STRUCTURAL
│           │                          #   validity flag (boundary must end with
│           │                          #   'startxref <n>'); in-stream %%EOF kept
│           │                          #   but valid=False. No PDF loading here —
│           │                          #   authoritative load-test is reconstruct.
│           ├── reconstruct.py         # truncate bytes[0:boundary]; validate each
│           │                          #   by loading with pikepdf; emit Revision
│           │                          #   objects. A detected-but-unloadable
│           │                          #   boundary is kept and flagged (never
│           │                          #   silently dropped).
│           ├── extract/
│           │   ├── __init__.py
│           │   ├── text.py            # per-page text-layer extraction via
│           │   │                      #   pdfminer.six (deterministic); the text
│           │   │                      #   diff uses ONLY this.
│           │   ├── words.py           # pdfplumber word-level bounding boxes,
│           │   │                      #   used ONLY by the OVERLAY check (annot
│           │   │                      #   rect overlapping a text region).
│           │   └── normalize.py       # NFC, collapse whitespace, strip
│           │                          #   ZW/soft-hyphen, trim; tokenizer. Shared
│           │                          #   so text + object diffs normalize alike.
│           ├── diff/
│           │   ├── __init__.py
│           │   ├── textdiff.py        # difflib token-level diff per page (aligned
│           │   │                      #   by page index) + char-level diff on
│           │   │                      #   changed tokens; substantive vs whitespace.
│           │   └── objectdiff.py      # overridden-object detection between
│           │                          #   consecutive revisions + classification:
│           │                          #   CONTENT / SIGNATURE / MARKUP / OVERLAY /
│           │                          #   FORM_FILL / FIELD_EDIT / META. OVERLAY
│           │                          #   geometry uses extract/words.py.
│           ├── highvalue.py           # high-value token matchers (amount/date
│           │                          #   strong; ID-like weak). Config-driven.
│           ├── scoring.py             # combine text + object diffs -> tier + score
│           │                          #   band + evidence per rubric. Config thresholds.
│           ├── analyze.py             # orchestration: detect->reconstruct->diff->
│           │                          #   score->findings -> AnalysisReport.
│           │                          #   analyze_path/analyze_bytes (read-only,
│           │                          #   never raises). Added in Task 6 to keep
│           │                          #   report.py pure-render and cli.py thin.
│           └── report.py              # render AnalysisReport -> JSON (machine) and
│                                      #   a human summary showing before -> after.
├── scripts/
│   └── make_fixtures.py       # generate (deterministic) into tests/fixtures/:
│                              #   clean.pdf = single-revision KNOWN-NEGATIVE
│                              #   (also serves the "single_rev" role); and
│                              #   edited_incremental.pdf = KNOWN-POSITIVE, amount
│                              #   changed via incremental update, original bytes
│                              #   preserved as a prefix.
└── tests/
    ├── conftest.py            # build fixtures into a tmp dir once per session
    ├── test_detect.py         # revision detection over synthetic raw bytes
    ├── test_reconstruct.py    # truncate + load-validate; unloadable kept & flagged
    ├── test_objectdiff.py     # classification of overridden objects
    ├── test_textdiff.py       # normalization + substantive-change detection
    ├── test_scoring.py        # tier/score boundaries from the rubric
    ├── test_report.py         # analyze + JSON/summary rendering (inline fixtures)
    ├── test_cli.py            # CLI output rules + exit codes
    └── test_end_to_end.py     # detector vs fixtures: positive -> HIGH,
                               #   negative -> INCONCLUSIVE
```

### Data flow (one analysis)
`cli` → read raw bytes → `revisions.detect` (boundaries) →
`revisions.reconstruct` (loadable Revision per boundary) → for each consecutive
pair: `extract.text` + `extract.normalize` → `diff.textdiff` and
`diff.objectdiff` (+`highvalue`) → `scoring` (tier + score) →
`report` (JSON + human summary).

### Why this split
- **Stage 1 is a subpackage** (`revision_recovery/`); later stages
  (`font_fingerprint/`, `ocr_crosscheck/`) are siblings under `pdf_forgery/`.
- **Each stage is a pure function over data models** (`models.py`), so later
  stages plug in at the diff/scoring layer without touching revision recovery.
- **All thresholds and regexes live in `config.py`** — the rubric demands every
  threshold be configurable.
- **`normalize.py` is shared** so text-diff and object-diff can't drift apart.
- **Reconstruction never drops a detected revision**: an unloadable boundary
  becomes a flagged Finding (feeds the MEDIUM "detected but not reconstructed"
  rule), satisfying "never silently drop it."

### Resolved decisions (owner-approved 2026-06-11)
1. **Package name** `pdf_forgery`; Stage 1 lives in `pdf_forgery/revision_recovery/`.
2. **Text extraction** is **pdfminer.six** only, for deterministic output — it
   drives the text diff. **pdfplumber** stays a dependency but is used *only* in
   `extract/words.py` for word-level bounding boxes feeding the OVERLAY check
   (annotation rect overlapping a text region). It is never used for the text diff.
3. **CLI** (`pdf-forgery <path>`):
   - No flags → human-readable summary to **stdout**.
   - `--json <path>` → write machine-readable JSON to that path.
   - `--summary` → force the human summary even when `--json` is set.
   - **Directory arg → batch**: ONE combined JSON array, one entry per file;
     top-level only, **no recursion**; not scattered per-file outputs.
   - **Exit code reflects RUN success only, never the verdict.** A HIGH finding
     does not change the exit code (confidence is advisory).

---

## Progress log

A running checklist, updated at the end of every task.

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

- [x] **Multi-stage architecture refactor — shared `core/` + orchestrator** (2026-06-12)
  - [x] New stage-agnostic `src/pdf_forgery/core/`:
    - `types.py`: `ConfidenceTier` (the canonical INCONCLUSIVE/LOW/MEDIUM/HIGH
      enum, **moved here** so every stage shares ONE definition), plus pure
      dataclasses `Evidence`, `Finding` (stage, per-finding `tier`, reason, page,
      object_ids, optional before/after, optional high_value tag, granular
      `evidence`), and `StageResult` (stage, tier, score, findings, summary,
      reasons, notes, ok/error, and a `payload` carrying the stage's rich result
      for adapters; excluded from `repr`).
    - `stage.py`: `Stage` `Protocol` (`runtime_checkable`) —
      `run(pdf_bytes, ctx) -> StageResult`, read-only, never raises.
    - `context.py`: `AnalysisContext` holds the raw bytes once and **lazily
      caches** shared artifacts — pikepdf doc, pdfminer `page_layouts`, and
      optional `rasterized_pages(dpi)` (PNG via `pypdfium2` if present, else `[]`).
      All accessors tolerate garbage input (`None`/`[]`, never raise);
      context-manager closes the pikepdf handle.
  - [x] `revision_recovery/models.py` now **re-exports** `ConfidenceTier` from
    `core.types` (identical object/values) — no test churn; scoring outcomes
    unchanged. Direction stays one-way: `revision_recovery → core`.
  - [x] `revision_recovery/adapter.py`: `report_to_stage_result` /
    `stage_result_to_report` map `AnalysisReport` ↔ `StageResult` WITHOUT touching
    detection or scoring; per-finding tier is a derived advisory annotation
    (CONTENT+text edit → HIGH, OVERLAY/FIELD_EDIT/content-no-text → MEDIUM, else
    LOW) that never feeds back into the score. `render_stage_json` /
    `render_stage_summary` unwrap the preserved `payload` and delegate to the
    **unchanged** `report.py` renderers (existing JSON/summary kept working).
  - [x] `revision_recovery/stage.py`: `RevisionRecoveryStage` (Stage 1 as a
    `Stage`). `analyze.py` gains `analyze_bytes_as_stage` / `analyze_path_as_stage`
    returning `StageResult` (same pipeline; `analyze_bytes`/`analyze_path` still
    return `AnalysisReport`). Facade `__init__` re-exports the new symbols.
  - [x] `src/pdf_forgery/pipeline.py`: `run_pipeline(bytes, stages, path=)` /
    `run_pipeline_on_path(path, stages)` build ONE shared `AnalysisContext`, run
    each stage, and **collect** the `StageResult` list in order (fusion is later).
    A misbehaving/raising stage or a bad path becomes an `ok=False` result, never
    aborts the run.
  - [x] Tests added: `test_core_types.py` (8: shared-enum identity, dataclass
    defaults, Protocol conformance, context caching/tolerance),
    `test_stage_adapter.py` (6: forged→HIGH & clean→INCONCLUSIVE on the fixtures
    with identical tiers/findings/before-after, render passthrough byte-identical
    to the original), `test_pipeline.py` (7: order, shared context, graceful
    degradation). Full suite: **264 passed** (246 original, 0 regressions).
  - Note: `revision_recovery` reads raw bytes + per-revision truncations itself,
    so its stage doesn't consume `ctx`'s caches yet — `AnalysisContext` exists for
    the upcoming pixel/text-sharing stages (`font_fingerprint`, `ocr_crosscheck`).

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
