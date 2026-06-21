# image_forensics — working notes (Stage 6)

Stage 6 (raster / pixel forensics) — the destination of Stage 3's
`routed_to="image_forensics"` hand-off. Canonical design contract:
`docs/STAGE6_DESIGN.md`; cross-cutting spec / layout / status: repo-root
`CLAUDE.md`. Detailed task history below.

- [x] **Stage 6 / Session 6.0 — design contract** (2026-06-19)
  - `docs/STAGE6_DESIGN.md` written (DESIGN ONLY, no code). Locked decisions:
    stage name `image_forensics` (matches Stage 3's route); per-page conservative
    activation; forensics on ORIGINAL embedded raster bytes (never the re-render);
    classical skimage/OpenCV provider is the DEFAULT, PhotoHolmes opt-in only
    (torch weight + TruFor non-profit license); two-co-located-signals → HIGH.

- [x] **Stage 6 / Session 6.1 — extraction + activation + engine scaffolding**
  (2026-06-19)
  - Scope: scaffolding only — **no detectors, no scoring**, not yet wired into
    the live `STAGES` list. Package + `STAGE_NAME = "image_forensics"` (confirmed
    with the owner over the prompt's working name "raster_forensics", because the
    name must equal Stage 3's `routed_to` string). Engine vocabulary follows the
    design doc (`ForensicProvider`/`ForensicMethod`/`ForensicMap`) over the
    prompt's flatter `ForensicEngine`/`MethodResult`, owner-confirmed.
  - `config.py` — `ImageForensicsConfig`: every threshold named in design §7/§10
    (`min_embedded_words`, `image_area_dominance_frac`, per-method heatmap
    thresholds, `min_blob_area_frac`, `colocate_iou`, `enable_dl_methods`,
    `dl_min_free_vram_mb`, score bands, `image_hash_salt`). Frozen so it can be
    snapshotted into the provenance manifest. Activation + extraction knobs are
    consumed now; the rest are declared for 6.2/6.3.
  - `images.py` — `DecodedImage` record + `extract_images(bytes)` /
    `decoded_images(ctx)` (the latter memoised on the new `ctx.stage_cache`).
    Locates `/Subtype /Image` XObjects via pikepdf, **recursing one level into
    form XObjects** (nested images recorded with `placement=None`, never a wrong
    box). Decodes via `pikepdf.PdfImage` across DeviceGray/RGB/**CMYK→RGB**/
    **Indexed→RGB**/ICCBased + SMask. For **DCTDecode** keeps the raw JPEG bytes
    **verbatim** (`read_raw_bytes`) so double-JPEG sees no re-encode — verified
    `jpeg_bytes == original`. Placement rect via a content-stream CTM walk
    (`q`/`Q`/`cm`/`Do`), mapped to **pdfplumber top-left points** + page point
    dims (for the aggregate `_finding_bbox` branch in 6.3). PHI-safe **salted
    content hash**; never logs pixels. Never raises (malformed → `[]`).
  - `activation.py` — per-page predicate (design §1): image-dominant when
    embedded words `< min_embedded_words` (**text floor**, reusing the Stage 3
    constant value) **OR** a single image covers `>= image_area_dominance_frac`
    of the page. Embedded-word signal read from `ctx.page_layouts` via the shared
    `core.glyphs` extractor — **no second text-extraction path**. A missing
    placement never inflates coverage. Document rollup → `any_image_dominant` /
    `image_dominant_pages`.
  - `engine.py` — `ForensicMethod` / `ForensicProvider` Protocols (downstream
    depends only on these), `ForensicMap` (heatmap+scalar+params), and
    `ForensicProvenance` (manifest, §9). Providers:
    - `ClassicalProvider` (default, CPU): always-available (numpy+cv2+PIL); method
      set declared (`ela`, `double_jpeg`, `jpeg_grid`, `noise_inconsistency`,
      `copy_move`) with real `applicable` gating (DQ/grid require a JPEG source);
      `analyze` raises `NotImplementedError` (real math is 6.2/6.3) — the same
      "stub raises, scaffolding compiles" shape Stage 3.0 shipped.
    - `PhotoHolmesProvider` (optional, opt-in): lazy `is_available()` (False here
      — torch/photoholmes absent), `methods()` → `[]`, `dl_vram_ok()` VRAM guard.
      Degrades exactly like `PaddleOCREngine`.
    - `StubForensicProvider` (tests): deterministic canned heatmap+scalar seeded
      by the image content hash; always available, no heavy deps.
    - `default_provider()` = classical-when-available else stub (PhotoHolmes never
      the default).
  - `core/context.py` — added a generic `stage_cache: dict[str, Any]` (namespaced
    keys; derived data only, never the input bytes) so `decoded_images` memoises
    once per file. The only core change.
  - Fixtures: `scripts/make_image_forensics_fixtures.py` (Pillow/numpy/pikepdf,
    deterministic, no network) — `build_jpeg_image_pdf` (returns the embedded JPEG
    bytes for the round-trip assertion; full-page ⇒ also the scanned activation
    case), `build_cmyk_image_pdf`, `build_indexed_image_pdf`.
  - Tests: `tests/test_image_forensics.py` (21) — JPEG round-trip + raw-byte
    preservation, pixel decode, CMYK→RGB, Indexed decode, full-page placement,
    salted hash, malformed→`[]`, context caching; activation analyse/skip + every
    predicate branch; engine availability fallback (PhotoHolmes absent → clean
    degrade), stub determinism, classical JPEG gating + deferred `analyze`,
    Protocol conformance, provenance. Full suite: **843 passed, 1 skipped**
    (skip = pre-existing pristine-invoice precision baseline, unrelated).

## Next — 6.2 / 6.3
6.2: implement the classical methods (ELA, DQ/double-JPEG, JPEG-grid, noise
residual, copy-move) in `ClassicalProvider`, + the heatmap→`BBox` localizer
(design §6). 6.3: scoring rule tree (§7), `ImageForensicsStage` (`core.Stage`),
adapter ↔ `StageResult`, wire into the live `STAGES` (substantive, NOT a
corroborator), `aggregate._finding_bbox` `image_forensics` branch, gated
heatmap-overlay endpoint, and the spliced/copy-move/double-compressed precision
fixtures.
