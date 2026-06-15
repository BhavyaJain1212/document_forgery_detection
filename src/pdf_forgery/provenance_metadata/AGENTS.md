# provenance_metadata — working notes

Detailed history below. Canonical spec / layout / status live in the repo-root
`CLAUDE.md`.

- [x] **Stage — `provenance_metadata/` (lightweight corroborator)** (2026-06-15)
  - Goal: cheap corroboration — a "hospital bill" whose /Producer is a consumer
    web PDF editor (or a bare version string from a re-render tool) is suspicious.
  - [x] `detect.py` reads ONLY Info dict + XMP producer + trailer `/ID` (read-
    only, shallow). Checks: web-editor producer/creator (configurable list:
    Sejda/iLovePDF/Smallpdf/PDFescape/Foxit/…), **version-only Producer** (the
    bare `3.0.35 (5.1.21)` form is itself the fingerprint — NOT restricted to
    brand names, per owner), browser Creator, ModDate>CreationDate, XMP-vs-Info
    producer mismatch, `/ID` halves mismatch, and a **composite edited-footprint**
    (version/web Producer + browser Creator + ModDate>CreationDate). HARD
    BOUNDARY: never walks `/Prev`/xref — revision_recovery owns that.
  - [x] `config.py`/`scoring.py`/`models.py`/`dates.py`/`analyze.py`/`adapter.py`/
    `stage.py` `ProvenanceMetadataStage`. **Provenance NEVER reaches HIGH on its
    own** (capped at `medium_ceiling`); it is a corroborator. Both real Sejda
    files → **MEDIUM 55** composite footprint (truthfully — they share it).
  - [x] Tests: `test_provenance.py` (dates, each matcher, composite, never-HIGH
    cap, real samples), `test_new_stages_pipeline.py` (Stage-protocol conformance
    + adapter round-trip + both stages in `run_pipeline` together).
  - Full suite: **406 passed, 1 skipped** (331 prior + 75 new; 0 regressions;
    the skip is the pristine-invoice precision baseline awaiting a real file).
    Runs still use `--ignore=tests/test_microsoft_pdf.py`.
