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

### Stage 4 — Raster / pixel forensics → RENUMBERED to Stage 6 (`image_forensics`)
This planned raster/pixel-forensics stage was renumbered to **Stage 6** in the
2026-06-19 stage reconciliation (substantive detectors are now 1–4,
`provenance_metadata` is the Stage 5 corroborator, raster forensics is Stage 6).
Full design contract: `docs/STAGE6_DESIGN.md`; build breakdown in the
**Stage 6 — Raster / pixel forensics** section below. NOTE the design corrects
the earlier assumption here: forensics run on the **original embedded image
XObject bytes**, NOT `AnalysisContext.rasterized_pages()` (the re-render
re-compresses away the JPEG/quantisation evidence — the re-raster is for UI
overlay only).

---

## Stage 7 — Aggregate + PHI-scrub + Advisory + UI (`pdf_forgery/aggregate/`)

> **Renumbered 2026-06-19:** this post-pipeline assembly/advisory/UI layer was
> historically labelled "Stage 6"; it is now **Stage 7**. It is **not a detector /
> not a `core.Stage`** — it runs *after* the pipeline. "Stage 6" now denotes the
> raster/pixel-forensics **detector** (`image_forensics`, see the section at the
> end of this file and `docs/STAGE6_DESIGN.md`). Its design contract moved to
> `docs/STAGE7_DESIGN.md`. The `7.0/7.1/7.2/7.x` session labels below are this
> layer's; the detector uses `image_forensics / 6.1–6.3`.

Assembly / presentation layer that runs AFTER the detection pipeline, over the
`list[StageResult]` it returns. Rolls them up into one `AggregateResult`
(headline via the existing `fusion.fuse()`, plus a flat overlay-ready finding
list with `bbox`), scrubs that to a descriptor-only `AdvisoryInput` at the single
PHI boundary, and runs a swappable advisory LLM to explain it as decision
support. Thin vertical slice; full fusion + full Stage 7 later. Full design
contract: @docs/STAGE7_DESIGN.md.

### 7.0 — Design contract + stubs ✅ (2026-06-16)
- [x] `docs/STAGE7_DESIGN.md` (items 1–4 + interfaces/stubs/PHI-safety/GPU note).
- [x] Stub subpackage `src/pdf_forgery/aggregate/`: data models + config +
      advisory prompt text real; all logic functions raise `NotImplementedError`;
      `safe_log` real; pluggable `AdvisoryEngine` (default `StubAdvisoryEngine`,
      no GPU). Stubs compile; layer NOT yet wired to run after the pipeline.

### 7.1 — Logic (CPU; no GPU), unit-tested ✅ (2026-06-16)
- [x] `aggregate.aggregate()` — delegates headline to `fusion.fuse()`; flattens
      findings into `AggregateFinding` descriptors with stable `finding_id`s
      (`"{stage}-{index}"`).
- [x] `_flatten_findings` / `_finding_type` / `_finding_bbox` — `type` derived
      per-stage from the rich payload (`object_classes`/`kind`/
      `relationship_kind`, with `ocr_crosscheck` re-applying its AGREE filter to
      stay positionally aligned); `token_class` from `Finding.high_value` /
      before-after presence. **`bbox` always returns `None` this slice** — no
      stage payload stores per-page pixel/point dimensions, so pixel/point →
      canonical `[0,1]` normalization isn't possible without re-opening the
      source PDF (out of the function's signature). Gap carried forward to 7.2;
      `docs/FORGERY_METHODS.md`-literal convergence for `type` also deferred.
- [x] `phi_scrub.to_advisory_input()` — allow-list projection to `AdvisoryInput`;
      `assert_advisory_safe()` — defensive egress check (rejects smuggled
      attributes via `object.__setattr__` and over-long/space-containing token
      fields).
- [x] `prompts.build_advisory_messages()` + `StubAdvisoryEngine.generate()`
      (deterministic, no GPU) + `generate_advisory()` (validate cited ids;
      templated fallback on disabled/unavailable/error/malformed; never raises).
- [x] Tests (`tests/test_aggregate.py`, 25 cases): roll-up matches
      `fusion.fuse()`; per-stage `type` derivation; `token_class` derivation;
      `bbox` is `None` (documented, not yet wired); **PHI-leak assertion** (a
      planted raw-text field via `object.__setattr__` raises, and so does
      free-text leaking into a token field); advisory cites only supplied ids;
      INCONCLUSIVE rendered honestly (never phrased as clean); graceful
      degradation when disabled / unavailable / raising / malformed. Full suite:
      717 passed, 1 skipped.

### 7.2 — Reviewer UI thin slice ✅ (2026-06-17)
Thin vertical slice of Stage 7's item-4 UI: upload → live per-stage progress →
verdict hero → streamed advisory → expandable per-stage drill-down, on the real
five-stage pipeline. **Full fusion and the document-overlay (bbox highlight)
view remain for the real Stage 7; Stage 6 (raster/pixel forensics) is next.**
- [x] `pipeline.run_pipeline(..., on_progress=cb)` — optional per-stage progress
      callback (`(stage, "running"|"done"|"error")`); a raising callback is
      suppressed so reporting never breaks the run.
- [x] `aggregate/jobs.py` — in-memory `JobManager` + background thread runner
      (the thin-slice stand-in for Celery/Redis): submit bytes → run the 5
      stages with live progress → `aggregate()` + `to_advisory_input()`; serves
      only the scrubbed descriptor view. Advisory generated lazily + cached.
- [x] `aggregate/api.py` — implemented `submit_document` / `get_job_status` /
      `stream_advisory` (chunked `AdvisoryEvent`s) over a shared `JobManager`;
      still framework-free (no web import).
- [x] `aggregate/server.py` — FastAPI transport: `POST /v1/documents` (202,
      magic-byte PDF check per invariant #4, 25 MB cap), `GET /v1/jobs/{id}`
      (scrubbed result), `GET /v1/jobs/{id}/advisory` (SSE); serves the static
      frontend. Serialization of descriptor dataclasses lives here.
- [x] `aggregate/webapp/` — hand-crafted CSS **design system** (type/spacing/
      color tokens, tier = the one semantic color: HIGH crimson / MEDIUM amber /
      LOW blue / INCONCLUSIVE gray, each paired with icon + label, AA contrast) +
      vanilla-JS SPA (upload/drag-drop, per-stage progress, verdict hero,
      streamed advisory via `EventSource`, accordion drill-down, empty + error
      states). No build toolchain; system font stack (fully local).
- [x] `LocalLLMAdvisoryEngine` — real Ollama wrap (`GET /api/tags` →
      `is_available()`, `POST /api/chat` `format:json` → `generate()`); default
      stays `stub` (no model pulled here; **never download weights**). Graceful
      absence → templated fallback.
- [x] Tests `tests/test_web.py` (9 cases): submit→poll→done with stubbed stages,
      **PHI boundary** (served result carries no before/after, only
      `token_class`), 404/415/400 transport, SSE chunk+done, cited-id subset.
      Verified end-to-end through `TestClient` on the real PDFs (tampered→HIGH 85,
      Acrobat_Demo→HIGH 95, clear invoice→MEDIUM 65). Full suite: 726 passed,
      1 skipped. Run the app: `./.venv/bin/python -m pdf_forgery.aggregate.server`.

### 7.x — Remaining for the real Stage 7 (deferred)
- [ ] Document-overlay view: render the page + draw `finding.bbox` boxes. Blocked
      on populating `bbox` (a stage payload must carry per-page pixel/point dims,
      or `_finding_bbox` must widen to re-open the source PDF). Coords already
      flow through the data model + API unchanged.
- [ ] Full fusion (cross-stage geometric correlation / dedup / calibration curve).
- [ ] Durable jobs (Celery/Redis/Postgres) replacing the in-memory `JobManager`.
- [ ] Gated, audit-logged evidence endpoint for raw before→after (the one path
      raw text is allowed, authenticated only).

---

## Stage 6 — Raster / pixel forensics (`pdf_forgery/image_forensics/`)

Pixel-level tamper detection for scanned / photographed bills — the destination
of Stage 3 (`ocr_crosscheck`)'s `routed_to="image_forensics"` hand-off. A
`core.Stage` (`run(bytes, ctx) -> StageResult`, read-only, never raises);
**SUBSTANTIVE** on image-dominant pages, INCONCLUSIVE on digital-native pages.
Full design contract: `docs/STAGE6_DESIGN.md`. Stage name / package =
`image_forensics` (matches the route string).

### image_forensics / 6.0 — design contract ✅ (2026-06-19)
- [x] `docs/STAGE6_DESIGN.md` — all 9 locked decisions + method table + scoring
      rule tree + activation predicate + test fixtures. **Design only — no code**
      this session (per owner). PhotoHolmes verified (Apache-2.0 base, but pulls
      torch + TruFor non-profit-only → optional provider, classical default).

### image_forensics / 6.1 — extraction + activation + engine scaffolding ✅ (2026-06-19)
Scaffolding only — the classical method *math* and all scoring were deliberately
deferred to 6.2/6.3 (the `analyze` bodies raise `NotImplementedError`).
- [x] `config.py` (`ImageForensicsConfig`: all thresholds/bands/toggles, frozen;
      nothing magic outside it). `DecodedImage` lives in `images.py`; `ForensicMap`
      + `ForensicProvenance` in `engine.py` (`ImageForensicsReport` is a 6.2 item).
- [x] **Pixel source** (`images.py`, not `source.py`): locate image XObjects via
      pikepdf (recurse one level into form XObjects); decode DCTDecode (**raw JPEG
      bytes kept verbatim, no re-encode**) / FlateDecode / Indexed / CMYK→RGB +
      `/SMask`; map XObject → page rectangle via the content-stream CTM (top-left
      points). Salted content hash. Cached on `ctx.stage_cache`. Never raises.
      (JPXDecode decode path lands when a J2K fixture exists.)
- [x] **Engine abstraction** (`engine.py`): `ForensicMethod` + `ForensicProvider`
      protocols + `StubForensicProvider` (deterministic, CPU) + `ClassicalProvider`
      (default) + `PhotoHolmesProvider` (scaffolded early; full wrap stays a 6.3
      item). Mirrors `OCREngine`.
- [~] **Classical methods**: ELA / DQ / JPEG-grid / noise-residual / copy-move are
      DECLARED on `ClassicalProvider` with real `applicable()` gating; `analyze()`
      math deferred to 6.2/6.3.
- [x] **Activation** (`activation.py`): per-page image-dominant predicate (text
      floor reusing `min_embedded_words`, OR image-area dominance).
- [x] Unit tests (`tests/test_image_forensics.py`, 21): decode paths (JPEG
      round-trip + raw bytes, CMYK, Indexed), CTM placement, activation boundaries,
      stub determinism, engine availability fallback. Suite **843 passed, 1 skip**.

### image_forensics / 6.2 — detect + localization ✅ (2026-06-22)
> Owner-scoped to detect/localize ONLY (consume the `ForensicProvider` Protocol);
> the real classical pixel DSP was deferred to its own follow-up (see below).
- [x] **Localization** (`localize.py`): heatmap → threshold → cv2 connected-component
      blobs (fractional, speckle-dropped) → page points (pdfplumber top-left) via the
      6.1 placement rect; `hot_fraction` / `iou` / `overlaps_high_value` (positional
      amount band). Hand-checked coordinate transform.
- [x] **Detect** (`detect.py`): run applicable methods over image-dominant pages;
      suppress whole-image / uniform lift as `GlobalSignal`; combine co-located
      fires (IoU; ≥2 distinct methods ⇒ `co_located` HIGH gate) → provisional
      `TamperRegion`s; `MethodError` / `CapabilityGap` never dropped.
- [x] Tests `tests/test_image_forensics_detect.py` (25).
- [ ] **Copy-move** (ORB + RANSAC) — folded into the deferred DSP work below.

### image_forensics / 6.3 — scoring + StageResult + fusion + wiring ✅ (2026-06-22)
- [x] **Scoring** (`scoring.py`): the §7 rule tree (single method never HIGH;
      HIGH needs two co-located independent signals; lone region / method-error →
      MEDIUM; capability-gap-only / no-image-dominant → INCONCLUSIVE).
- [x] **`stage.py`** (`ImageForensicsStage`, `core.Stage`) + adapter (rich
      `ImageForensicsReport` ↔ `StageResult`, payload-carried bbox region).
- [x] Wired into the live pipeline (`aggregate/jobs.py` + `test.py`) as
      **substantive** (NOT in `FusionConfig.corroborator_stages`). Fusion needed
      no change: HIGH originates; INCONCLUSIVE never drags down; MEDIUM +
      provenance → HIGH.
- [x] Tests: `tests/test_image_forensics_scoring.py` (15),
      `tests/test_image_forensics_stage.py` (9, incl. fusion). Suite **887 / 1 skip**.

> **Pipeline status:** Stage 6 is wired end-to-end, live, and **DETECTING**
> (Session 6.4). The classical CPU DSP is implemented and flags real
> spliced/copy-moved/locally-recompressed scanned bills as HIGH while clean scans
> and innocent whole-page recompression stay LOW.

### image_forensics / 6.4 — classical DSP + real-pixel fixtures ✅ (2026-06-22) — FULLY COMPLETE
- [x] Real method math in `ClassicalProvider` (new `classical.py`): ELA,
      DQ/double-JPEG (quant-lattice misfit, scipy DCT on the ORIGINAL JPEG + PIL
      quant tables — no page re-render), JPEG-grid, noise-residual (flat-block
      gated), **copy-move** (ORB+RANSAC) → genuine heatmaps through detect→score.
      Methods no longer raise `NotImplementedError`; `detect.executions` makes a
      clean scan LOW (analysed, no fire) while gaps still → INCONCLUSIVE.
- [x] Real-pixel acceptance fixtures (`make_image_forensics_fixtures.py`): clean
      scan → LOW, spliced amount → **HIGH with bbox**, local double-compress →
      localized, whole-page recompress → **no local region**, copy-move → detected.
- [x] Tests: `tests/test_image_forensics_classical.py` (7); the former deferred-DSP
      INCONCLUSIVE stage assertion flipped to live spliced→HIGH + clean→LOW. Suite
      **895 / 1 skip**; digital-native regression guard holds.
- [ ] (Optional) `aggregate._finding_bbox` `image_forensics` branch (payload boxes
      already in `FindingLocation` shape) + gated heatmap-overlay endpoint.
- Owner-calibration items surfaced: recomputed (not entropy-decoded) DCT
  coefficients; ELA edge sensitivity on text-heavy real scans; robust-z thresholds
  tuned to synthetic fixtures — all pending the still-owed real scanned-bill drop.

### image_forensics / OPTIONAL — PhotoHolmes / opt-in DL + reproducibility + UI overlay
- [ ] `PhotoHolmesProvider` behind the `ForensicProvider` interface (Apache-2.0
      classical methods freely; DL methods gated by `enable_dl_methods` + the VRAM
      guard; **TruFor excluded** — non-profit license). Lazy import; graceful absence.
- [ ] VRAM guard (`torch.cuda.mem_get_info` / `pynvml` ≥ `dl_min_free_vram_mb`)
      so DL never contends with PaddleOCR/Ollama; degrade to classical + note.
- [ ] `ForensicProvenance` reproducibility manifest (provider/versions/device +
      per-method params + per-image decode-path/colorspace/dims/salted hash).
- [ ] Gated heatmap-overlay PNG endpoint (PHI) reusing `aggregate/overlay.py`'s
      render-lock pattern; never on the scrubbed advisory channel.
- [ ] Drop a real flatbed-scanned untouched bill at `test_pdf's/` for a real-data
      precision test (synthetic clean fixture is not a substitute).
