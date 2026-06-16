# Build Plan — work one phase at a time

Each phase ends with a runnable demo in stub mode and a green `pytest`. Do not scaffold a
later phase before the current one is demonstrated. Section refs (§) point to
@docs/ARCHITECTURE.md.

## Phase 0 — Repo skeleton
- [ ] Project layout: `api/`, `pipelines/`, `models/`, `schemas/`, `synthesis/`,
      `worker/`, `tests/`, `docs/`.
- [ ] `pyproject.toml` / deps pinned to the §3 stack. Python 3.11+.
- [ ] `docker-compose.yml`: api, worker-cpu, worker-gpu (CPU-only in stub), redis,
      postgres, minio.
- [ ] `.env.example` incl. `FDP_MODEL_BACKEND=stub`, `FDP_MAX_BYTES`, thresholds.
- [ ] `docker compose up` boots all services; `/healthz` and `/readyz` (§6) pass.

## Phase 1 — Data contract (the spine, §5)
- [ ] `schemas/results.py`: `Evidence`, `ModuleResult`, `PageResult`, `DocumentResult`
      exactly per §5. Validate the §5 example JSON round-trips.
- [ ] Postgres models + Alembic migration for job, result, audit, review queue.
- [ ] Hash-chained audit record (chain of custody) writer.
- [ ] Tests: envelope serializes/deserializes; audit chain verifies.

## Phase 2 — Model boundary (§7)
- [ ] `models/base.py`: `ForensicModel` Protocol, `NeuralOutput`.
- [ ] `models/registry.py`: `FDP_MODEL_BACKEND` switch.
- [ ] `StubForensicModel`: deterministic, seeded by content hash; synthetic mask
      (box over densest text region via OpenCV) + templated rationale.
- [ ] `ForensicHubModel`: wired but inert in sandbox; `models/WEIGHTS.md` documents the
      download step. **Do not download weights.**
- [ ] Tests: same input → same stub output; backend switch changes no pipeline code.

## Phase 3 — Ingestion & Triage (PRIORITY, §8)
- [ ] `POST /v1/documents`: validate, encrypt-at-rest blob, create job + audit root,
      enqueue triage, return `202`. Idempotency-Key + SHA-256 content dedupe (§6).
- [ ] True-format detection by magic bytes; extension mismatch → `METADATA_FLAG` (§8.1).
- [ ] PDF classification: native / scanned (SCANNED-PDF LOOPHOLE) / mixed-media split
      (§8.2). Tag `source_kind` correctly.
- [ ] Hard guards (§8.4): zero-byte, encrypted, too-large, decompression bomb,
      JS/`/Launch`/embedded (never execute), multi-frame.
- [ ] Emit `RoutingPlan`; enqueue one task per (page, pipeline) on the right queue (§8.5).
- [ ] Tests: every §8 routing branch + every §11 terminal REJECT state.

## Phase 4 — Deterministic pipelines (CPU)
- [ ] PDF structural (§9.1): `object_tree`, `xref_revisions`, `metadata_regex`
      (starter subset of the 192-regex ruleset as data, TODO to expand), `signature`,
      `embedded_images` REQUIRED HANDOFF to image pipelines by codec.
- [ ] JPEG CPU parts (§9.2): `ela`, recompression-level estimate + confidence downweight.
- [ ] PNG CPU parts (§9.3): chunk/metadata parse; screenshot/born-digital no-sensor guard
      (PRNU/CFA confidence ≈ 0).
- [ ] `pipelines/common/content_extract.py`: text layer + PaddleOCR for amounts/dates/
      provider IDs (feeds §10).
- [ ] Neural module stubs route through the §7 adapter on the `gpu` queue.
- [ ] Tests: each module emits the correct verbatim `forgery_method` + tag
      (@docs/FORGERY_METHODS.md); handoff fires on a native PDF with an embedded image.

## Phase 5 — Synthesis & output (PRIORITY, §10)
- [ ] Cross-document perceptual hashing (pHash + dHash) reuse check (§10.1).
- [ ] Fully-synthetic verdict: SDiFL signal + NPI/registry + arithmetic (§10.2).
- [ ] Worst-case per-page → document rollup (§10.3).
- [ ] `synthesis/fusion.py` hard rules → confidence-weighted soft agg → `weights.yaml`.
- [ ] `synthesis/calibration.py`: monotone curve; scores as ranking, not probability.
- [ ] Decision bands + forced-review (AI-inpainting/low-confidence) with reason (§10.5).
- [ ] Populate every tag in `evidence_index` (§10.6).

## Phase 6 — Review, callbacks, edge-case matrix
- [ ] `GET /v1/documents/{job_id}` (+ `/evidence/{tag}`), `POST .../review`,
      `GET /v1/review/queue` (§6).
- [ ] Signed `callback_url` webhook on terminal state, retries w/ backoff, never blocks.
- [ ] Full §11 non-terminal edge-case handling (per-module timeout/OOM → error + continue,
      never fail the whole job for one module).

## Acceptance (§0)
- [ ] `FDP_MODEL_BACKEND=stub`: POST a real PDF, JPEG, and PNG → poll → full §5 envelope,
      every tag populated, every §11 case handled, hash-chained audit trail. No GPU.

---

## Stage 3 — OCR ↔ embedded-text cross-check (`pdf_forgery/ocr_crosscheck/`)

Cross-check layer in the implemented `pdf_forgery` pipeline (sibling of
revision_recovery / font_forensics / invoice_arithmetic / provenance_metadata).
Compares the embedded text layer (pdfminer) against OCR of the rendered raster
(pypdfium2 → PaddleOCR, GPU) to catch image-layer overlays, hybrid layer mixing,
and hidden/invisible text. **Substantive** fusion stage. Full design contract:
@docs/STAGE3_DESIGN.md.

### 3.0 — Design contract + stubs ✅ (2026-06-15)
- [x] `docs/STAGE3_DESIGN.md` (items 1–6 + interfaces/queue split/PHI-safety).
- [x] Stub subpackage `src/pdf_forgery/ocr_crosscheck/`: data models + config
      real; all logic functions raise `NotImplementedError`; pluggable
      `OCREngine` (default `PaddleOCREngine`); `STAGE_NAME = "ocr_crosscheck"`.
      Stubs compile; stage NOT yet wired into the live `STAGES` list.

### 3.1 — Primitives (CPU + GPU), unit-tested in isolation ✅ (2026-06-15)
- [x] `align.embedded_to_pixel` — DPI transform + `/Rotate` handling (pure math).
- [x] `align.center_inside` / `iou` / `match_words` — one-to-many matching.
- [x] `normalize.fold` (NFC + project normalizer + case/space + OCR-confusion
      classes); `classify` (maps `highvalue.classify_token` → `TokenClass`);
      `levenshtein`; `allowed_edits` + `is_within_tolerance` (high-value inversion).
- [x] `guards.filter_offpage_embedded` (clipping) + `filter_low_confidence_ocr`.
- [x] `routing.is_text_sparse` (scanned short-circuit).
- [x] `divergence.*` (taxonomy classification + weighting).
- [x] `scoring.score` (rule tree → tier/score; high-value MISMATCH → 90–100).
- [x] `ocr_engine.PaddleOCREngine.recognize`/`provenance` (GPU; optional dep,
      graceful absence — `is_available()` gate degrades to INCONCLUSIVE).
- [x] Unit tests per primitive (transform numerics, matching, fold/tolerance
      both directions, guards, routing, scoring boundaries).
      Suite: 46 align + 21 guards + 11 routing + 48 normalize + 38 divergence + 25 scoring = 189 new tests.

### 3.2 — Orchestration, adapter, wiring, end-to-end ✅ (2026-06-15)
- [x] `analyze.analyze_bytes`/`analyze_path` (GPU render+OCR → CPU
      extract+align+guard+route+classify+score → `OCRCrossCheckReport`;
      read-only, never raises; degrades to INCONCLUSIVE when backends absent).
- [x] `adapter.report_to_stage_result` + renderers (PHI-safe JSON + advisory
      human summary), `stage_result_to_report`.
- [x] Wire `OCRCrossCheckStage` into `STAGES` (test.py / pipeline) as a
      substantive stage; fusion treats it as a SUBSTANTIVE originator (not a
      corroborator — not in `FusionConfig.corroborator_stages`).
- [x] Acceptance tests (StubOCREngine):
      overlay simulation (5,000→50,000 in raster) → MISMATCH AMOUNT → HIGH 95;
      hidden-text simulation (amount word missing from OCR) → EMBEDDED_ONLY AMOUNT → HIGH 75;
      PaddleOCR unavailable → INCONCLUSIVE with note;
      all-agree (perfect OCR mirror) → LOW score=0.
      Suite total: 662 passed, 1 skipped (up from 526).

### Stage 4 — Raster / pixel forensics (next planned stage)
Pixel-level detection: Error Level Analysis (ELA), copy-move, AI-inpainting detection,
double-JPEG quantisation analysis. Expected to consume image bytes from
`AnalysisContext.rasterized_pages()` and produce a `StageResult` like every other stage.
Stage 3 already hands off scanned/image-only PDFs to this stage via
`routed_to="image_forensics"` in the INCONCLUSIVE result. See `docs/FORGERY_METHODS.md`
for the full taxonomy of raster-level forgery signals.
