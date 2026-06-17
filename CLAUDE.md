# PDF Document-Forgery Detection — Stage 1 (Revision Recovery)

This file is the canonical spec + working memory for the project. The spec below
is saved VERBATIM from the project owner. Do not paraphrase or "improve" it.
The spec is followed by the **Module layout** and a compact **Status &
architecture** section. Detailed per-stage task history lives in nested
`CLAUDE.md` files under each `src/pdf_forgery/<stage>/` directory.

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
> `pdf_forgery/revision_recovery/`, so later stages (`font_forensics/`,
> `provenance_metadata/`, `ocr_crosscheck/`) become sibling subpackages. See the
> **Status & architecture** section below and the nested per-stage `CLAUDE.md`
> files for what exists.

Package name: `pdf_forgery` (under `src/`). Python `>=3.11` (local interpreter
is 3.12; no 3.11-specific build available on this machine — 3.12 satisfies the
constraint).

```
document_forgery_detection/
├── CLAUDE.md                  # this file: spec + layout + status (per-stage history in nested CLAUDE.md)
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

## Status & architecture (working memory)

> **Detailed per-stage task history now lives in nested `CLAUDE.md` files**
> (Claude Code auto-loads a subdirectory's `CLAUDE.md` when working in that
> subtree), so this root stays focused on the spec + layout + the cross-cutting
> picture. See:
> - `src/pdf_forgery/revision_recovery/CLAUDE.md` — Stage 1 (Tasks 0–8)
> - `src/pdf_forgery/font_forensics/CLAUDE.md` — Stage 2 + mixed-font bug-fix
> - `src/pdf_forgery/invoice_arithmetic/CLAUDE.md` — invoice stage + calibration
> - `src/pdf_forgery/provenance_metadata/CLAUDE.md` — provenance stage
> - `src/pdf_forgery/ocr_crosscheck/CLAUDE.md` — Stage 3 OCR↔embedded cross-check
>   (implemented; contract in `docs/STAGE3_DESIGN.md`)
> - `src/pdf_forgery/aggregate/CLAUDE.md` — Stage 6 aggregate + PHI-scrub + advisory + UI
>   (DESIGN + STUBS only so far; contract in `docs/STAGE6_DESIGN.md`)

### Where things stand
All stages are implemented and green. Pipeline:
`revision_recovery` + `font_forensics` + `invoice_arithmetic` +
`provenance_metadata`, fused by `pdf_forgery/fusion.py` into one overall verdict.
Full suite: **417 passed, 1 skipped** (the skip is the real-pristine-invoice
precision baseline — see below). Later planned siblings: `ocr_crosscheck`.

- **Dev env:** `.venv` (Python 3.12). System `pip` is 3.10 + PEP-668, so run the
  suite via `./.venv/bin/python -m pytest --ignore=tests/test_microsoft_pdf.py`
  (`tests/test_microsoft_pdf.py` is a stray `fitz` scratch script that breaks
  collection — left pending an owner delete decision).
- **Fixtures** are git-ignored; the generators in `scripts/` (`make_fixtures.py`,
  `make_font_fixtures.py`, `make_invoice_fixtures.py`) regenerate them on demand,
  and `tests/conftest.py` builds them once per session into a tmp dir.

### Multi-stage architecture (2026-06-12 refactor)
Stage-agnostic `src/pdf_forgery/core/` holds the shared vocabulary every stage
speaks: `types.py` (`ConfidenceTier` + `Evidence`/`Finding`/`StageResult`),
`stage.py` (the `Stage` protocol), `context.py` (`AnalysisContext` — lazily
caches the pikepdf doc / pdfminer layouts / rasterised pages so the file is
parsed once across stages), and `glyphs.py` (the ONE shared per-character glyph
extractor + line/token grouping; do NOT add a second extraction path). Each
stage exposes a `Stage` (`run(bytes, ctx) -> StageResult`, read-only, never
raises) and an adapter mapping its rich report ↔ `StageResult` (rich report
preserved as `payload` for the original JSON/summary renderers). `pipeline.py`
builds one shared `AnalysisContext`, runs the stages, and collects their results
in order; a failing stage becomes an `ok=False` result, never aborts the run.
Direction is one-way: `stages → core`, never the reverse.

### Fusion layer (`pdf_forgery/fusion.py`)
Collapses the per-stage `StageResult` list into ONE advisory headline. NOT a
vote/average — evidence-weighted escalation on two ideas: (1) **stage roles** —
*substantive* stages (revision_recovery, font_forensics, invoice_arithmetic) can
originate a verdict; *corroborators* (provenance_metadata, configurable via
`FusionConfig.corroborator_stages`) only strengthen one. (2) **corroboration
lifts, never originates** — floor = strongest substantive tier (INCONCLUSIVE =
no signal, never drags down); a substantive MEDIUM escalates to HIGH only with
independent corroboration (a 2nd substantive stage ≥ MEDIUM, OR the corroborator
firing). Lone substantive MEDIUM → MEDIUM; only-LOW substantive → LOW even if a
corroborator fired; all-INCONCLUSIVE → INCONCLUSIVE. This is where
invoice_arithmetic's lone-gross-break MEDIUM cap is lifted to HIGH when
provenance corroborates. `fuse()` + `render_overall_summary()` +
`FusedAssessment`. `test.py` prints an **OVERALL** section using it.

### Critical calibration decisions (owner-approved 2026-06-15) — DO NOT relitigate
- **`test_pdf's/Microsoft-Sample-Invoice.pdf` is NOT a clean baseline.** It has a
  GENUINE line-item break (`9.00*41.61 = 374.49 != 37004.49`, contiguous uniform
  Calibri glyphs — not extraction noise, not a misclassified total), present in
  BOTH it and `tampered.pdf`, plus a Sejda producer + ModDate>CreationDate. Both
  Microsoft files are therefore positives. It is still clean w.r.t. *fonts* and
  *revisions* (so the font/revision tests asserting LOW/INCONCLUSIVE stay valid).
- **Convergence-gated arithmetic tiering:** a lone gross broken equation (no
  subtotal to corroborate) is capped at strong MEDIUM; HIGH needs convergence
  (one cell reconciles ≥2 equations) OR cross-stage corroboration (the latter
  applied in fusion). So `tampered.pdf`: arithmetic MEDIUM 65 → fused **HIGH 85**
  via the provenance footprint.
- **Real precision baseline still owed:** drop a genuine, untouched invoice at
  `test_pdf's/pristine-invoice.pdf` to activate the skipped
  `test_pristine_invoice_low_on_arithmetic`. A synthetic always-reconciling
  fixture is NOT an acceptable substitute for that precision proof.
- **Fusion provenance rule (already implemented):** provenance-MEDIUM with no
  substantive stage firing fuses to overall-clean (LOW).
