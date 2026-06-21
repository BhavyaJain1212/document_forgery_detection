# aggregate — working notes (Stage 7)

Stage 7 = aggregation + PHI-scrub boundary + advisory + UI. It is NOT a detector
and NOT a `core.Stage`; it runs AFTER the pipeline, over the `list[StageResult]`
that `run_pipeline()` returns. Canonical design contract: `docs/STAGE7_DESIGN.md`.
Cross-cutting spec / layout / status: repo-root `CLAUDE.md`.

- [x] **Stage 7 / Session 7.0 — design contract + stubs** (2026-06-16)
  - Goal: prove the pipe end-to-end (aggregate → PHI-scrub → advisory → UI) on
    the detectors that exist today, THIN. Full fusion + full Stage 7 later.
  - [x] `docs/STAGE7_DESIGN.md` — items 1–4 fully specified:
    (1) `AggregateResult` roll-up — headline via existing `fusion.fuse()`
    (minimal fusion, full deferred + config-exposed), flat `AggregateFinding`
    list with stable `finding_id` + descriptor set + **`bbox` carried NOW**
    (canonical normalized [0,1] top-left page-relative space) so the future
    overlay is a pure render job;
    (2) PHI-scrub trust boundary as an explicit ALLOW-LIST
    (`ADVISORY_FINDING_ALLOWLIST`) — `AdvisoryInput` is the ONLY thing crossing
    toward LLM/frontend; raw before→after relocated to a gated, audit-logged
    evidence endpoint (out of scope this slice); forbidden set enumerated;
    (3) advisory system+user prompt — grounded only in descriptors, cite
    `finding_id`s, never re-judge the verdict, INCONCLUSIVE honesty +
    `AdvisoryOutput` shape;
    (4) UI states (upload / processing / result hero+advisory+drill-down /
    empty / error) + non-blocking API contract (`POST /v1/documents` →202,
    `GET /v1/jobs/{id}`, advisory SSE).
  - [x] Stub subpackage `src/pdf_forgery/aggregate/` — DATA MODELS + CONFIG +
    PROMPT TEXT real; ALL logic functions raise `NotImplementedError`;
    `safe_log` real (small PHI-safe primitive):
    - `models.py` — `BBox`, `AggregateFinding`, `AggregateResult`,
      `AdvisoryFinding`, `AdvisoryStage`, `AdvisoryInput`, `FindingRationale`,
      `AdvisoryOutput`, `ADVISORY_FINDING_ALLOWLIST`.
    - `config.py` — `AggregateConfig` (wraps `FusionConfig`; advisory toggle /
      engine name; allow-list; log salt).
    - `aggregate.py` — `aggregate()` (delegates headline to `fusion.fuse()`) +
      `_flatten_findings` / `_finding_type` / `_finding_bbox` stubs.
    - `phi_scrub.py` — `to_advisory_input()` + `assert_advisory_safe()` (the
      boundary). `prompts.py` — real `SYSTEM_PROMPT` / `USER_PROMPT_TEMPLATE`
      + `build_advisory_messages()` stub.
    - `advisory.py` — `Message`, `AdvisoryEngine` Protocol, `StubAdvisoryEngine`
      (deterministic, NO GPU — the 7.1 default), `LocalLLMAdvisoryEngine` (GPU,
      swappable, graceful absence via `is_available()`), `generate_advisory()`.
    - `api.py` — HTTP contract dataclasses + stub handlers (no web framework
      imported this slice). `safe_log.py` — `finding_log_record` / `salted_hash`.
  - [x] GPU note: advisory model contends with PaddleOCR ONLY under concurrent
    load; sits behind the swappable `AdvisoryEngine` like Stage 3's `OCREngine`.
    Never download weights in the sandbox.
  - [x] Stubs compile; **no logic implemented**; layer NOT yet wired to run after
    the pipeline (wiring = 7.2). `docs/TODO.md` updated with 7.1 / 7.2.

## Next — 7.1 (logic) then 7.2 (engine + wiring + UI)
7.1: implement `aggregate()` + flatten/bbox extractors + `to_advisory_input` +
`assert_advisory_safe` + `build_advisory_messages` + `StubAdvisoryEngine` +
`generate_advisory`; unit tests incl. a PHI-leak assertion. 7.2: wire after the
pipeline (e.g. in `test.py`), `LocalLLMAdvisoryEngine`, API handlers, gated
evidence endpoint. See `docs/STAGE7_DESIGN.md` §6.

- [x] **Stage 7 / Session 7.1 — CPU logic implemented + unit-tested** (2026-06-16)
  - Scope explicitly confirmed with the owner: 7.1 only (no FastAPI/Celery/real
    LLM/SSE this session — that's 7.2). Owner also named **Ollama** (already
    installed) as the chosen 7.2 serving backend for `LocalLLMAdvisoryEngine`,
    over vLLM — documented in `advisory.py`'s docstring, not implemented yet.
  - [x] `aggregate.aggregate()` — delegates the headline (tier/score/reasons/
    contributing_stages/notes) to `fusion.fuse()`; flattens every stage's
    `Finding`s into `AggregateFinding`s with `finding_id = "{stage}-{index}"`.
  - [x] `_finding_type` — derived per-stage from the rich `payload` object,
    relying on the **positional correlation invariant**: 4/5 stage adapters
    (`revision_recovery`, `font_forensics`, `invoice_arithmetic`,
    `provenance_metadata`) build core `Finding`s via an unfiltered 1:1 map, so
    `payload.findings[index]` lines up directly; `ocr_crosscheck` filters out
    `DivergenceType.AGREE` before building `Finding`s, so `_finding_type`
    re-applies the identical filter before indexing. Falls back to
    `finding.high_value` / a generic label when no payload is present.
  - [x] `_finding_bbox` — **always returns `None` this slice.** Researched (no
    stage payload stores per-page pixel/point dimensions needed to normalize
    native geometry — `ocr_crosscheck` pixel+`render_dpi`, `font_forensics`/
    `invoice_arithmetic` PDF points bottom-left-origin — into the canonical
    `[0,1]` top-left space), and the function has no access to the source PDF
    to compute it. Documented gap, not silently papered over; real fix needs
    either a wider function signature or per-stage page-dimension metadata.
  - [x] `phi_scrub.to_advisory_input()` / `assert_advisory_safe()` — allow-list
    projection plus a defensive egress check that (a) rejects attributes
    smuggled onto a frozen dataclass via `object.__setattr__` (frozen
    dataclasses have no `__slots__`, so this is a real runtime possibility, not
    just type-checker theory), and (b) rejects token fields that look like
    leaked free text (over 64 chars or containing a space).
  - [x] `prompts.build_advisory_messages()` — renders the frozen system/user
    prompt text against a scrubbed `AdvisoryInput`. Avoided a circular import
    by keeping `advisory.py`'s import of `build_advisory_messages` at module
    level while making `prompts.py`'s import of `Message` from `.advisory`
    **lazy** (inside the function body).
  - [x] `advisory.py` — `StubAdvisoryEngine.generate()` is real (parses its own
    rendered prompt text back into tier/score/findings, then templates a
    grounded summary). `generate_advisory()` validates that every cited
    `finding_id` actually exists in the input and degrades to the same
    templated fallback (never raises) when disabled, unavailable, raising, or
    citing unknown ids. `LocalLLMAdvisoryEngine` is still a 7.2
    `NotImplementedError` stub; its docstring now names the Ollama HTTP API
    (`GET /api/tags`, `POST /api/chat`) as the planned wrap.
  - [x] `tests/test_aggregate.py` (25 cases, all green): roll-up parity with
    `fusion.fuse()`; per-stage `type`/`token_class` derivation incl. the
    `ocr_crosscheck` AGREE-filter realignment; PHI-leak assertion (planted
    `raw_text` attribute, and free text leaking into a token field) both
    raise; bbox-is-`None` documented in test, not silently asserted as a bug;
    advisory cites only supplied ids; INCONCLUSIVE rendered honestly (asserts
    the "not the same as clean" disclaimer, never a bare clean-sounding
    statement); graceful degradation across disabled / unavailable / raising /
    malformed-citation paths.
  - Full suite after this change: **717 passed, 1 skipped** (the skip is the
    pristine-invoice precision baseline, unrelated and pre-existing).
  - **Known gap to revisit in 7.2 or sooner:** `bbox` is part of the
    `AggregateFinding`/`AdvisoryFinding` contract and the frontend overlay
    design assumes it's populated, but nothing currently produces it. Needs a
    decision: extend stage payloads with page pixel/point dimensions, or widen
    `_finding_bbox`'s signature to accept the source PDF/page sizes.

- [x] **Stage 7 / Session 7.2 — reviewer UI thin slice** (2026-06-17)
  - Scope: the item-4 UI as a THIN vertical slice on the real five-stage
    pipeline — upload → live per-stage progress → verdict hero → streamed
    advisory → expandable per-stage drill-down. Full fusion + the document
    overlay (bbox highlight) view stay deferred to the real Stage 7; Stage 4
    (raster/pixel forensics) is the next stage.
  - **No Celery/Redis/Postgres this slice.** `jobs.JobManager` is an in-memory,
    thread-backed stand-in: `submit()` returns immediately (invariant #8), a
    daemon thread runs the 5 stages, and the runner rolls up via `aggregate()` +
    scrubs via `to_advisory_input()`. Only the scrubbed `AdvisoryInput` and the
    advisory prose are ever served; the rich `AggregateResult` stays server-side
    for the future gated evidence endpoint.
  - `pipeline.run_pipeline` gained an optional `on_progress(stage, state)`
    callback (`running`/`done`/`error`) so progress is reported without polling;
    a raising callback is swallowed (reporting must never break the run).
  - `api.py` handlers are now real (`submit_document` / `get_job_status` /
    `stream_advisory`) over a process-wide `JobManager`, still framework-free.
    Advisory is generated lazily + cached, then `stream_advisory` chunks it
    word-by-word into `AdvisoryEvent`s so the "verdict now, prose after" feel of
    design §4 is real, not faked client-side.
  - `server.py` is the only FastAPI-aware module: route mapping + dataclass→JSON/
    SSE serialization + static serving. `POST /v1/documents` routes by **magic
    bytes** (`%PDF`, invariant #4), caps at 25 MB. Run:
    `./.venv/bin/python -m pdf_forgery.aggregate.server`.
  - `webapp/` — design-system-first hand-crafted CSS (tokens for type/spacing/
    color/radius; **tier is the single semantic color** — HIGH crimson / MEDIUM
    amber / LOW blue / INCONCLUSIVE gray, each paired with an icon AND a text
    label, AA contrast; tabular-mono figures for score/ids) + a dependency-free
    vanilla-JS SPA. System font stack (no web-font fetch — fully local). No node
    build step.
  - `LocalLLMAdvisoryEngine` is now a real Ollama wrap (`GET /api/tags` gates
    `is_available()`; `POST /api/chat` with `format:json` → `_parse_model_json`).
    Default engine stays `stub` (no model is pulled here; **weights are never
    downloaded in the sandbox**); absence degrades to the templated fallback.
    Config gained `advisory_model` / `advisory_base_url`.
  - Tests: `tests/test_web.py` (9) — stubbed-stage submit→poll→done, the **PHI
    boundary** (served result has no before/after, only `token_class`), SSE
    chunk+done, cited-id subset, 404/415/400. Verified end-to-end via FastAPI
    `TestClient` on the real PDFs and visually (headless-Chrome screenshot of all
    four tiers). Full suite: **726 passed, 1 skipped**.
  - **Still open:** `bbox` still `None` (overlay view blocked on it); full fusion;
    durable jobs; the gated raw-evidence endpoint.

- [x] **bbox wired for revision_recovery + baked overlay PNG** (2026-06-17)
  - Closes the long-standing `_finding_bbox`-returns-`None` gap for the
    `revision_recovery` stage (others still `None` — their payloads still lack
    per-page dims). The rich `revision_recovery.Finding` now carries a
    `location` (per-word `BoxPt`es in pdfplumber points + page visual dims); see
    that stage's CLAUDE.md for the localizer.
  - **`aggregate.py:_finding_bbox`**: for `stage == "revision_recovery"`, unions
    the located boxes and normalizes by page dims → canonical `[0,1]` `BBox`.
    Includes a **positional-integrity guard**: verifies the rich finding's
    `page_index`/`object_ids` match the core finding before trusting the
    positional index (the rich↔core map is a strict 1:1 in `adapter.py`, but the
    guard makes a future filter/reorder fail safe → `None`, never a wrong box).
  - **`overlay.py`** (new): `render_page_overlay(pdf_bytes, page, boxes_pt, cfg)`
    bakes an annotated PNG (pypdfium2 raster + Pillow boxes; scale = dpi/72,
    visual orientation). **This PNG is PHI** (real document pixels) → served ONLY
    via the gated evidence endpoint, never the scrubbed advisory channel. Returns
    `None` (never raises) when pypdfium2/Pillow absent or render fails.
  - **config.py**: `overlay_dpi` (150), `overlay_box_rgb`, `overlay_box_width`,
    `overlay_fill_alpha`.
  - **Endpoint**: `GET /v1/jobs/{job_id}/findings/{finding_id}/overlay.png`
    (gated evidence). `jobs.Job` now retains `pdf_bytes` (server-side, PHI);
    `JobManager.render_overlay` + `api.get_finding_overlay` back it. Only
    `revision_recovery-*` findings localize today.
  - PHI invariant tested (`test_web.py`): the served descriptor carries the
    `bbox` (coordinates) but NO document text; the pixels come only from the
    gated PNG endpoint. Tests: `tests/test_overlay.py` (4),
    `tests/test_aggregate.py` (+6 bbox), `tests/test_web.py` (+2 gated). Full
    suite **757 passed, 1 skipped**.

- [x] **Reviewer UI: two-column result + document overlay view** (2026-06-17)
  - The result view is now a **two-column split** (`webapp/`): analysis on the
    LEFT (existing hero + advisory + detector breakdown), the **document on the
    RIGHT** with CSS bounding boxes drawn over the flagged text. Boxes use the
    normalized `bbox` already in the scrubbed result; page pixels come from a new
    plain page-image endpoint `GET /v1/jobs/{job_id}/pages/{page}/image.png`
    (`JobManager.render_page` → `render_page_overlay(..., boxes=[])`; gated/PHI
    like the overlay PNG). Chose CSS overlay over the baked PNG so one page image
    carries all boxes, tier-coloured to match the theme, and interactive.
  - `app.js`: `renderDocPane` / `docPageFigure` / `bindDocPane` (hover a finding
    row → its box pulses; click → scroll-into-view). Empty state when no finding
    localises. `showView` adds `body.result-wide` so only the result view widens
    to 1160px; upload/processing/error stay narrow. `styles.css` adds
    `.result__split` (stacks < 980px), `.docpane` (sticky), `.docbox` (tier
    color via `color-mix`). Verified visually (headless-Chrome screenshot: box
    lands exactly on the edited `50,000`). Test: `test_web.py` page-image (+1).
    Full suite **758 passed, 1 skipped**. Still only `revision_recovery` findings
    localise; others show the empty state.

- [x] **Multipage UI fix: blank page images + truncated advisory** (2026-06-18)
  - Symptom: on the 13-page `Microsoft-Sample-Invoice_clear.pdf` the document
    pane showed empty page rows and the streamed advisory stopped after ~3 words
    ("Across **font_forensics** and"). Single-page docs were fine — this is the
    only multipage test file.
  - **Root cause: `pypdfium2` is NOT thread-safe.** The result screen opens the
    advisory SSE stream AND the browser fires ~6+ parallel `pages/{N}/image.png`
    requests (13 pages here vs 1 for single-page docs). FastAPI runs the sync
    handlers on its threadpool, so `render_page_overlay`'s concurrent
    `PdfDocument` renders corrupt each other — reproduced standalone: 13 parallel
    renders → **9 None + corrupt 9786-byte blanks**. The blanks 404 → empty page
    rows. The same instability drops the in-flight advisory SSE connection
    mid-stream (the advisory text is generated COMPLETE server-side — verified via
    an in-process job run — so the truncation was purely the dropped connection,
    not the generator). Both symptoms, one cause. Not a regression from the
    arithmetic/OCR fixes; the race was always there (hence "4–5 pages" before, "0"
    now — nondeterministic).
  - **Fix:** a module-level `threading.Lock` (`overlay._RENDER_LOCK`) serializes
    the `pypdfium2` open→render→close block in `render_page_overlay` (covers both
    the page-image and gated-overlay endpoints). Pillow box-drawing/compositing
    stays OUTSIDE the lock (thread-safe; keeps hold-time minimal). Renders are
    <0.1s so serializing the burst is cheap. With the lock, 13 concurrent renders
    → **0 failures**, verified both standalone and through the real FastAPI
    `TestClient` (13/13 → 200, full-size PNGs).
  - Test: `tests/test_overlay.py::test_concurrent_renders_all_succeed` (12
    threads, every render a valid PNG). Full suite: **822 passed, 1 skipped**.
