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

- [x] **Stage 6 / Session 6.2 — detectors (combination) + bbox localization**
  (2026-06-22)
  - **Scope decision (owner-approved):** detect/localize ONLY. The consumer layer
    against the `ForensicProvider` Protocol; the real classical pixel math (ELA /
    DQ / noise DSP) is deferred to its own follow-up — the 6.1 classical methods
    still raise `NotImplementedError`. (The 6.2 prompt's working names
    `raster_forensics` / `MethodResult` map to the locked `image_forensics` /
    `ForensicMap`, same as 6.1.)
  - `localize.py` — heatmap → `BBox`, no signal math. `heatmap_blobs` (threshold →
    `cv2.connectedComponentsWithStats`, 8-conn, → `scipy.ndimage.label` →
    all-hot-pixels fallback; drops `< min_blob_area_frac` speckle) yields
    **fractional** `Blob`s (resolution-independent). `frac_bbox_to_page_bbox` /
    `blob_to_page_bbox` map a fractional box LINEARLY into the image's top-left
    `placement` rect (upright-placement assumption documented; no retained CTM →
    conservative axis-aligned). `hot_fraction` (global-signal test), `iou`,
    `union_bbox`, `overlaps_high_value` (the §4 **positional** amount-band tag —
    NO fabricated token classes).
  - `detect.py` — orchestration, **provider-agnostic** (depends only on the
    Protocol). `detect(ctx, provider=, config=, activation=)` runs applicable
    methods over image-dominant pages → `_fires_from_map` (suppress whole-image
    lift / single dominant blob as `GlobalSignal`; scalar-only fire → global) →
    `combine_fires` (greedy IoU≥`colocate_iou` cluster; region with ≥2 **distinct**
    methods ⇒ `co_located`, the §7 HIGH gate). Emits provisional `TamperRegion`s
    (NOT a `StageResult`) + `MethodFire`/`GlobalSignal`/`MethodError` +
    `ForensicProvenance`. A method that raises → `MethodError` (never dropped) —
    so the real `ClassicalProvider` degrades to all-errors + no regions, never
    crashes. Read-only, never raises.
  - `config.py` — added `global_coverage_frac` (0.65, whole-image suppression),
    `high_value_band_top_frac` (0.50) / `high_value_band_bottom_frac` (1.0).
  - Tests: `tests/test_image_forensics_detect.py` (25) — hand-checked coordinate
    transform; blob extraction + speckle drop; IoU + high-value band; whole-image
    / scalar-only / sub-threshold suppression; co-located vs lone vs same-method
    corroboration; e2e over `build_jpeg_image_pdf` (spliced-amount → localized
    high-value co-located region; clean → none; whole-page recompression → no
    local region, recorded global; method-error capture; no-image-dominant →
    empty; real `ClassicalProvider` degrades to `NotImplementedError` errors).
    Full suite: **868 passed, 1 skipped**.

- [x] **Stage 6 / Session 6.3 — scoring + StageResult + fusion integration**
  (2026-06-22)
  - **Scope decision (owner-approved):** scoring/stage/fusion now; the real
    classical DSP stays DEFERRED. Key gate: a method raising `NotImplementedError`
    is a **capability gap** (`detect.CapabilityGap`), NOT a `MethodError` — the
    scorer treats gaps as *no signal* → INCONCLUSIVE, so registering the stage
    live while every classical method is a stub can never manufacture a false
    MEDIUM. `DetectionResult.analyzed` = "did any method actually execute".
  - `scoring.py` — `score(detection, activation, cfg)` → `ImageForensicsReport`
    (rich payload). §7 rule tree: no image-dominant page **or** only capability
    gaps → INCONCLUSIVE; co-located region (≥2 independent methods) → **HIGH**
    (`score_high` 80, +`score_high_value_bump` 8 in the amount band; the ONLY HIGH
    path); lone region ≥ `region_medium_min_strength` **or** a `MethodError` →
    MEDIUM (capped — cross-stage corroboration lifts in fusion); weak lone / global
    / nothing-fired → LOW. Worst-case rollup; per-region `RegionFinding` tiers.
  - `stage.py` — `ImageForensicsStage` (`core.Stage`): activate → detect → score →
    `report_to_stage_result`. Provider injectable (tests); default = classical
    (→ gaps → INCONCLUSIVE live). Findings carry NO text (only pixels); geometry
    rides the payload region (`TamperRegion.page_bbox`, already `FindingLocation`
    shape); high-value tag = positional `"amount"`. Never raises → `ok=False`.
  - `config.py` — `region_medium_min_strength` (0.60), `score_medium_method_error`
    (40), `score_high_value_bump` (8).
  - **Registered live** as SUBSTANTIVE in `aggregate/jobs.py` (`STAGE_ORDER` +
    `build_default_stages`) and `test.py` `STAGES`, after `ocr_crosscheck`. NOT in
    `FusionConfig.corroborator_stages` → fusion needed **no change**: a Stage 6
    HIGH originates a verdict; INCONCLUSIVE never drags a parse-side HIGH down; a
    Stage 6 MEDIUM + `provenance_metadata` corroboration escalates to HIGH (same
    mechanism as `invoice_arithmetic`).
  - Tests: `tests/test_image_forensics_scoring.py` (15) — every tier branch;
    `tests/test_image_forensics_stage.py` (9) — Stage protocol, e2e
    digital-native→INCONCLUSIVE / spliced→HIGH-with-bbox / real-classical→
    INCONCLUSIVE / internal-failure→`ok=False`, + the four fusion confirmations.
    Full suite: **887 passed, 1 skipped**.

- [x] **Stage 6 / Session 6.4 — classical pixel DSP + real-pixel fixtures**
  (2026-06-22) — **Stage 6 FULLY COMPLETE; engine implemented, live, detecting.**
  - `classical.py` — the real CPU signal processing behind `ClassicalProvider`,
    each method returning a `[0,1]` heatmap (or `None` = "ran, too little data"):
    - **`ela`** — recompress at `cfg.ela_quality` (75; below scan quality so the
      pass actually exercises high-freq, not ≈identity) + per-block robust-z of the
      local error; thresholds on **local contrast** not absolute (§5 edge FP note).
    - **`double_jpeg`** (JPEG only) — DCT **quantisation-lattice misfit**: read the
      quant table verbatim from the original stream (`PIL.Image.quantization`,
      un-zigzagged) + recompute the block DCT (`scipy.fftpack`) over the ORIGINAL
      JPEG luminance (NOT the page re-render); a block off the host lattice (splice
      / local re-compress) is the local outlier. Uniform single/whole-image double
      compression → flat field → no fire.
    - **`jpeg_grid`** (JPEG only) — on-grid 8×8 boundary-energy **deficit** per
      block (distinct signal from DQ → the two can co-locate as 2 independent fires).
    - **`noise_inconsistency`** — high-pass residual std per block, **flat-block
      gated** (Noisesniffer principle: edges/rules/glyphs are not noise) so a
      lined / text-bearing clean scan stays out of MEDIUM; bidirectional robust-z.
    - **`copy_move`** — ORB (seeded RNG) self-match + RANSAC affine; a cluster needs
      `≥ copy_move_min_matches` inliers AND a non-trivial offset; marks both source
      + duplicate. MEDIUM alone; HIGH only with a co-located boundary break.
    - Normalization is **relative** (robust z vs the image's own median) → a clean /
      uniformly recompressed page has no positive outlier (precision), only a genuine
      local anomaly lights up (recall). No `jpegio`/`torchjpeg` (neither installs in
      the restricted env); scipy-DCT-on-original + PIL quant tables is the reader.
  - `engine.py` — the five `_ClassicalMethod` subclasses now bind a `classical.*`
    function (versions `1.0.0`); `analyze` wraps the heatmap in a `ForensicMap` and
    **never raises NotImplementedError** (a `None` heatmap is no-fire → LOW, not a
    gap/error). `applicable` unchanged (DQ/grid still JPEG-only).
  - `detect.py` — added `DetectionResult.executions` (methods that ran to
    completion); `analyzed` now true when a method **executed** (even with no fire:
    "analysed, found nothing" = LOW), not only on fires/errors — so a clean scan is
    LOW, not INCONCLUSIVE. Capability-gap path retained (still hit by stub/absent
    providers) → INCONCLUSIVE, so the live-safe contract is intact.
  - `config.py` — DSP knobs: `ela_quality`, `anomaly_block`, `anomaly_z0`/`_slope`,
    `noise_z0`, `noise_flat_percentile`, `dq_z0`, `jpeg_grid_z0`,
    `copy_move_orb_features`/`_orb_max_dist`/`_min_offset_frac`.
  - Fixtures (`make_image_forensics_fixtures.py`) — smooth "scanned bill" base +
    `build_clean_scan_pdf` (→ LOW), `build_spliced_amount_pdf` (foreign-noise
    amount-band patch → co-located **HIGH**, returns GT bbox), `build_double_
    compressed_pdf` (local Q25 re-compress → DQ/grid localizes), `build_recompressed
    _pdf` (innocent whole-page Q72 rescan → **no local region**), `build_copy_move
    _pdf` (duplicated stamp → copy-move). All Pillow/numpy/pikepdf, deterministic.
  - Tests: new `tests/test_image_forensics_classical.py` (7 real-pixel acceptance,
    skip if cv2/scipy absent); the former deferred-DSP "INCONCLUSIVE live-safe"
    stage assertion **flipped** to a live spliced→HIGH + clean→LOW pair; the detect
    capability-gap test rewritten to assert real executions. Full suite: **895
    passed, 1 skipped**; the digital-native regression guard holds (image-dominant
    activation means digital-native docs never invoke the engine).

### Owner-calibration items surfaced (not blockers)
- **DCT source:** coefficients are *recomputed* (scipy DCT on the original JPEG's
  decoded luminance) rather than entropy-decoded, because `jpegio`/`torchjpeg` do
  not build in the restricted-network env. Adequate for localization; an
  entropy-coded-coefficient reader would sharpen DQ marginally.
- ~~**ELA edge sensitivity / real-document precision proof**~~ **CLOSED
  2026-06-22:** the real clean, text/rule-dense multi-copy W-2 baseline exposed
  the false HIGH and now stands as the regression proof for the document-aware
  gates recorded below. It is not HIGH and emits no HIGH/splice finding.
- **Thresholds** (`ela_quality`, the `*_z0` robust-z centres, `noise_flat_
  percentile`, copy-move gates) are tuned to the synthetic fixtures; expect a
  re-tune against a real scanned-bill population. The §7 scoring rule tree is
  untouched (locked).

- [x] **bbox wired into the reviewer UI** (2026-06-22) — `aggregate._finding_bbox`
  now has an `image_forensics` branch: it reads `TamperRegion.page_bbox`
  (pdfplumber top-left points) + `page_width_pt`/`page_height_pt` off the payload
  region and normalizes to the canonical `[0,1]` `BBox` (mirrors the
  `revision_recovery` branch — plain divide + `_clamp01`, no y-flip), behind the
  same positional-integrity page guard `_finding_type` already uses. So tamper
  regions now render as boxes in the two-column document pane for **both** PDF and
  raw image (JPEG/PNG) uploads (the server wraps an image into a full-page PDF, so
  placement resolves to the whole page = the simplest case). No new endpoints, no
  UI/JS change (the doc pane already renders any finding carrying a normalized
  bbox). Tests: `tests/test_aggregate.py` (+4: normalization / no-placement→None /
  page-guard / no-dims→None), `tests/test_image_forensics_classical.py` (+2: spliced
  fixture + wrapped-image upload localize through `aggregate()`). Full suite: **912
  passed, 25 skipped** (0 regressions).

- [x] **document-aware classical-DSP precision calibration** (2026-06-22) — the
  real clean multi-copy W-2 at `test_files/W2_XL_input_clean_1000.jpg` was a
  standing false-positive baseline: dense glyph/rule edges plus the intended
  Copy B / Copy C duplication produced 28 regions and a false **HIGH 88** splice.
  Three conservative, config-driven gates now keep structural document patterns
  from originating that verdict:
  - ELA uses the same low-structure-block principle as the noise detector
    (`ela_structure_gate=True`, `ela_flat_percentile=0.6`), with a shared
    `_flat_block_mask`; block-scale low-pass suppresses glyph/rule edges while
    preserving the flat foreign-noise patch in `build_spliced_amount_pdf`.
  - copy-move rejects a RANSAC source/destination cluster whose normalized span
    reaches `copy_move_max_cluster_span_frac=0.25`, suppressing multi-copy forms
    while retaining the compact duplicated-stamp fixture.
  - scoring only lets co-located regions within `splice_max_width_frac=0.90` and
    `splice_max_area_frac=0.12` originate HIGH. Page-spanning corroborated bands
    remain reported as MEDIUM review evidence; missing geometry remains eligible
    so localization failure cannot silently suppress evidence.
  - Standing result: the real W-2 is **not HIGH** and has no HIGH/splice finding
    (residual lone DQ/noise regions may conservatively produce MEDIUM, as expected
    for the classical engine). Synthetic splice remains HIGH; compact copy-move
    and local double-compression remain ≥MEDIUM; clean/recompression negatives
    remain LOW/INCONCLUSIVE. Focused tests cover every gate plus the real asset.

  This closes the ELA-edge / real-document precision item above. DQ structure
  normalization remains a possible later calibration if the desired policy is
  to demote the residual W-2 review signal from MEDIUM to LOW; it is not required
  for the firm no-false-HIGH acceptance bar.

- [x] **DQ/noise residual precision calibration** (2026-06-23) — closed the
  deferred lone-DQ MEDIUM item against the same real clean W-2. Before this pass,
  the prior no-false-HIGH gates still left 10 strong lone regions (8
  `double_jpeg`, 2 `noise_inconsistency`) and a false MEDIUM 50 review result.
  - `double_jpeg` now applies the shared low-structure mask on its native 8x8
    grid (`dq_structure_gate=True`, `dq_flat_percentile=0.6`). This removes the
    decoded-IDCT rounding/clipping response on ordinary glyph/rule blocks; the
    W-2's 8 DQ regions fall to zero.
  - Scoring treats repeated lone regions from one method as diffuse LOW evidence
    when their count reaches `diffuse_lone_min_count=4` or their aggregate page
    coverage reaches `diffuse_lone_coverage_frac=0.20`. The regions remain
    reportable; co-located evidence and method errors are never demoted.
  - Fix-C calibration selected `noise_flat_percentile=0.5`. The noise residual
    still uses the edge-preserving median pass, while its *structure* mask uses a
    block-scale Gaussian low-pass so it rejects printed layout without rejecting
    the foreign paper grain it is meant to detect. Measured result: W-2 residual
    noise regions 2 -> 0; the synthetic foreign-noise splice remains above the
    noise threshold and co-locates with ELA.
  - Standing result: real W-2 **LOW 15**, zero DQ/noise regions; synthetic splice
    **HIGH 88**; local recompression remains HIGH, compact copy-move MEDIUM, and
    clean / whole-page recompression LOW. The recomputed-DCT DQ approximation did
    not itself fire on the local-recompression fixture even before this gate;
    that positive remains supported by the independent grid/noise signals.

  Focused tests cover the DQ structure gate, diffuse count and coverage paths,
  method isolation, noise-patch recall, the real W-2 precision baseline, and all
  existing real-pixel positive/negative fixtures.

### Still optional (not required for completeness)
PhotoHolmes / opt-in DL provider; a gated **heatmap**-overlay endpoint for the UI
(the box overlay is wired — see above); config-snapshot in the provenance manifest.
