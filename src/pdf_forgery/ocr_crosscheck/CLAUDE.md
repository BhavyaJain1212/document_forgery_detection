# ocr_crosscheck — working notes

Stage 3 (OCR ↔ embedded-text cross-check). The canonical design contract is
`docs/STAGE3_DESIGN.md`; the cross-cutting spec / layout / status live in the
repo-root `CLAUDE.md`. Detailed task history below.

- [x] **Stage 3 / Session 3.0 — design contract + stubs** (2026-06-15)
  - Goal: catch render-vs-text divergence (image-layer overlays, hybrid layer
    mixing, hidden/invisible text) that the STRUCTURAL stages cannot see, by
    comparing the embedded text layer (pdfminer) against OCR of the rendered
    raster (pypdfium2 → PaddleOCR, GPU).
  - [x] `docs/STAGE3_DESIGN.md` — full design: (1) coordinate alignment
    (embedded→pixel transform, render DPI 300, center-containment + IoU matching,
    one-to-many groups); (2) divergence taxonomy AGREE/MISMATCH/EMBEDDED_ONLY/
    OCR_ONLY with forgery-class mapping + base weights; (3) normalization +
    tolerance with the CRITICAL inversion (high-value tokens get STRICTER /
    zero tolerance, OCR-confusion fold first); (4) FP guards (clipping guard for
    off-page operators = the pdfminer-vs-fitz divergence; OCR-confidence floor);
    (5) scanned/text-sparse routing → INCONCLUSIVE + image-forensics hand-off;
    (6) scoring rule tree → INCONCLUSIVE/LOW/MEDIUM/HIGH, high-value MISMATCH →
    90-100; (7) interfaces/stubs + pluggable OCR engine + GPU/CPU queue split +
    PHI-safe logging.
  - [x] Stub subpackage `src/pdf_forgery/ocr_crosscheck/` — DATA MODELS + CONFIG
    real; ALL logic functions raise `NotImplementedError`:
    - `models.py` — `WordBox`, `WordSource`, `DivergenceType`, `TokenClass`,
      `Divergence`, `RenderProvenance`, `Stage3Result`, `OCRCrossCheckReport`.
    - `config.py` — `OCRCrossCheckConfig` (DPI, IoU/center, confusion classes,
      tolerances + high-value inversion, guards, routing floors, weights +
      multipliers, score values). Nothing magic hard-coded outside it.
    - `ocr_engine.py` — `OCREngine` Protocol + default `PaddleOCREngine` (GPU);
      records engine/model-version/language/device/DPI for reproducibility.
    - `align.py` (transform + matching), `normalize.py` (fold + tolerance, reuses
      `revision_recovery.highvalue.classify_token`), `guards.py`, `routing.py`,
      `divergence.py` (taxonomy classification), `scoring.py` (rule tree),
      `analyze.py` (orchestration), `adapter.py` (report ↔ `StageResult`),
      `stage.py` (`OCRCrossCheckStage`, conforms to `core.Stage`), `__init__.py`
      facade.
  - [x] `STAGE_NAME = "ocr_crosscheck"`. SUBSTANTIVE fusion stage (can originate
    a verdict) — NOT a corroborator; not added to `FusionConfig`.
  - [x] Stage intentionally NOT wired into the live `STAGES` list in `test.py`
    this session (stub `run` would raise) — wiring is a 3.2 task.
  - [x] Acceptance: design specifies items 1-6; stubs import/compile; no
    detection logic; `docs/TODO.md` updated with the 3.1 / 3.2 breakdown.

- [x] **Stage 3 / Session 3.1 — primitives, unit tests** (2026-06-15)
  - Implemented: `align.py` (embedded_to_pixel R=0/90/180/270, quad_to_bbox,
    center_inside, iou, match_words one-to-many), `guards.py`,  `routing.py`,
    `ocr_engine.py` (PaddleOCREngine lazy-import + is_available(); StubOCREngine).
  - Added `tests/test_ocr_align.py` (46), `test_ocr_guards.py` (21),
    `test_ocr_routing.py` (11). Suite: 526 passed.

- [x] **Stage 3 / Session 3.2 — orchestration, adapter, wiring** (2026-06-15)
  - `normalize.py`: fold() (NFC → confusion fold with casefolded classes → casefold
    → drop spaces), classify(), levenshtein(), allowed_edits(), is_within_tolerance().
    Key invariant: confusion classes are casefolded BEFORE building the char_map so
    that ("8","B") catches 'b' (post-casefold of 'B').
  - `divergence.py`: weight_for(), classify_group() (join embedded → most-sensitive
    token class → is_within_tolerance → AGREE/MISMATCH), classify_unmatched(),
    classify_page().
  - `scoring.py`: score() rule tree — INCONCLUSIVE (routed_to set / empty list) →
    HIGH (AMOUNT/DATE MISMATCH=95, ID MISMATCH=85, high-value orphan=75) →
    MEDIUM (mass≥2.0) → LOW. High fires before checking mass.
  - `analyze.py`: full orchestration — rasterize (ctx or direct pypdfium2) → OCR
    (with is_available() gate → INCONCLUSIVE) → pdfminer layouts → extract embedded
    → per-page guards + match + classify → routing check → score → report. Never
    raises.
  - `adapter.py`: report_to_stage_result() (divergences → Findings, tier/score
    carried through), stage_result_to_report(), render_stage_json() (PHI-safe: no
    raw text in JSON log, only counts/classes/weights), render_stage_summary().
  - Wired `OCRCrossCheckStage` into `test.py` STAGES as substantive stage.
  - Added `tests/test_ocr_normalize.py` (48), `test_ocr_divergence.py` (38),
    `test_ocr_scoring.py` (25), `test_ocr_integration.py` (25). Suite: 662 passed, 1 skipped.
  - Acceptance: overlay AMOUNT MISMATCH → HIGH 95; hidden AMOUNT EMBEDDED_ONLY → HIGH 75;
    PaddleOCR absent → INCONCLUSIVE; all-agree → LOW 0.

- [x] **PaddleOCR GPU verification + 3.x API fix** (2026-06-16)
  - `paddleocr` package was missing from `.venv` (only base `paddlepaddle-gpu`
    was present); installed `paddleocr==3.7.0`. GPU itself confirmed working
    (RTX 4060, CUDA) independent of this fix.
  - `ocr_engine.py`'s `PaddleOCREngine` was written against the PaddleOCR
    **2.x** API (`use_gpu=`, `gpu_id=`, `show_log=`, `.ocr(img, cls=True)`,
    `[[quad],[text,conf]]` results) which PaddleOCR 3.x no longer accepts
    (raises `ValueError: Unknown argument`). Fixed to 3.x: single `device=`
    kwarg, `use_textline_orientation=`, and result parsing reads the new
    `OCRResult` dict-like (`rec_texts`/`rec_scores`/`rec_polys` parallel
    arrays) instead of the old quad/tuple format.
  - Verified end-to-end on a real image: `is_available()` → True, 150 words
    recognized, GPU memory 15MiB→1883MiB during inference (CPU path actually
    errors in this paddle build with a oneDNN `NotImplementedError`, so GPU is
    the only working device here).
  - 3 tests in `test_ocr_integration.py` had asserted the INCONCLUSIVE-degrade
    path by relying on `paddleocr` being absent from the environment rather
    than mocking `is_available()`. Fixed via `monkeypatch.setattr(engine,
    "is_available", lambda: False)` since paddleocr is now genuinely installed
    in the same `.venv` pytest runs in. Suite: 662 passed, 1 skipped.

- [x] **False-positive fix: every clean PDF scored HIGH** (2026-06-16)
  - Diagnosed + fixed per `docs/STAGE3_OCR_FALSE_POSITIVE_FIX.md`. Three root
    causes, all fixed:
  - **#1 (primary) geometry.** PaddleOCR 3.x runs a document-preprocessing
    sub-pipeline (orientation classify + UVDoc unwarp + textline orientation)
    by default, which warps OCR boxes out of the rendered raster's coordinate
    space — every embedded↔OCR pair landed on unrelated text. Added
    `paddle_use_doc_orientation_classify` / `paddle_use_doc_unwarping` /
    `paddle_use_textline_orientation` config toggles (all default `False` —
    every page here is a digital-native PDF rasterised flat/upright by
    pypdfium2) and wired them into `ocr_engine.py`'s `_get_ocr()`. Alone, this
    took the clean page-4 invoice from 40 mismatches/~170 orphans down to
    191/192 agree, 1 orphan.
  - **#2 ID over-classification.** `classify_token_kind`'s `ID_LIKE` rule
    (alphanumeric run ≥ 6) matches almost every ordinary invoice word
    ("Standard", "Microsoft", "Southeast" → all "ID"), and `id_strict=True`
    gave it zero tolerance — turning routine OCR word-boundary noise into a
    zero-tolerance high-value divergence. Flipped defaults: `id_strict=False`,
    `id_rel_tol=0.15` (prose-grade tolerance, matching `prose_rel_tol`). ID
    removed from `scoring._HIGH_VALUE` entirely — an ID MISMATCH/orphan can no
    longer originate HIGH, only contribute its weight to the divergence mass
    (MEDIUM/LOW).
  - **#3 lone-orphan→HIGH.** A single high-value (AMOUNT/DATE) orphan is the
    expected steady-state OCR noise on a clean page (one orphan survived even
    after fix #1) — letting it alone originate HIGH meant the stage could never
    be quiet. Added `min_high_value_orphans` config (default 2): orphan→HIGH
    now needs either `>= min_high_value_orphans` of them or an accompanying
    high-value MISMATCH; an uncorroborated lone orphan scores MEDIUM instead
    (`score_medium_default`), not HIGH. Removed `score_high_id_mismatch`
    (dead after fix #2).
  - **Re-baselined** `tests/test_ocr_scoring.py` (lone-orphan tests →
    MEDIUM; ID-mismatch test → not-HIGH; added corroboration tests) and added
    a real-PaddleOCR-engine regression test,
    `tests/test_acceptance_samples.py::test_page4_microsoft_ocr_crosscheck_not_high`
    (skips gracefully if PaddleOCR unavailable).
  - **Verified on the real file:** `test_pdf's/page4_Microsoft-Sample-Invoice.pdf`
    went from **HIGH 95** (unwarp on) → **HIGH 75** (geometry fixed alone) →
    **LOW 15** (all three fixes; 191/192 agree, the lone surviving
    `ocr_only/id` orphan now correctly scores via mass, not HIGH). Full suite:
    **666 passed, 1 skipped** (skip is the pre-existing pristine-invoice
    precision baseline, unrelated).

- [x] **Long-PDF false-positive fix** (2026-06-16)
  - Diagnosed + fixed per `docs/STAGE3_LONG_PDF_FALSE_POSITIVE.md`. Two
    independent root causes on `Microsoft-Sample-Invoice_clear.pdf` (13-page,
    clean), both new and specific to long multi-page documents — distinct from
    the earlier single-page false-positive fix above.
  - **RC#1 (drove the HIGH 95): bare-digit AMOUNT misclassification +
    whole-line zero tolerance.** A standalone small integer with no currency/
    decimal/separator (e.g. "3", a reservation term in years) classified as
    AMOUNT, which elevated the entire joined prose+SKU line to AMOUNT's zero
    tolerance — so an unrelated underscore-vs-space OCR glyph drop elsewhere in
    the line tripped a false AMOUNT MISMATCH. Fixed with two layered changes:
    - **Fix B (narrowing):** `normalize.is_monetary_amount()` + config
      `amount_requires_monetary_context` (default `True`) — a bare 1-2 digit
      run with no currency symbol/word, decimal point, or thousands separator
      is demoted PROSE for the group/unmatched class decision
      (`divergence._classify_for_elevation`). Local to Stage 3; does NOT touch
      `revision_recovery.highvalue`.
    - **Fix A (structural):** `divergence.classify_group` no longer judges a
      MULTI-TOKEN AMOUNT/DATE group's whole joined line at zero tolerance.
      Instead it extracts the group's actual high-value sub-tokens
      (`_high_value_subtokens`), requires each to survive intact (folded,
      zero-tolerance substring containment) in the folded OCR text, and judges
      the rest of the line at PROSE tolerance — AGREE iff both hold. A
      single-token high-value group keeps the original strict whole-string
      check (a true `5,000`→`6,000` edit is unaffected — regression-tested).
  - **RC#2 (residual MEDIUM after RC#1): absolute divergence-mass threshold
    doesn't scale with document length.** A "Microsoft Azure" header/logo OCR
    recognizes but the embedded layer doesn't (or vice versa) repeats once per
    page; on a 13-page doc this is 16 orphans (mass 16.5) — an artifact rate of
    ~0.7% of 2204 compared words, but an absolute floor can't see that it's
    proportionally clean. Fixed with two layered changes:
    - **Fix E (relative floor):** config `divergence_mass_ratio` (default
      `0.02`) + `compared_words` threaded into `scoring.score()` (counted in
      `analyze._run` as matched groups + unmatched embedded + unmatched OCR
      across all pages). MEDIUM gate is now
      `mass >= max(medium_divergence_mass, divergence_mass_ratio * compared_words)`.
      `compared_words` defaults to `0` so existing direct `score()` unit-test
      calls keep the absolute-floor-only behaviour.
    - **Fix F (orphan collapse):** config `repeated_orphan_cap` (default `2`)
      + `scoring._divergence_mass` groups EMBEDDED_ONLY/OCR_ONLY divergences by
      folded text and caps how many instances of an identical repeated orphan
      contribute full weight — a header/logo repeated on every page is one
      systematic extraction artifact, not N independent anomalies. MISMATCHes
      are never capped.
  - **Tests:** `tests/test_ocr_normalize.py` (`is_monetary_amount`),
    `tests/test_ocr_divergence.py` (`TestRC1BareDigitAndLocalizedTolerance`:
    bare-digit line → AGREE; genuine amount/date edit inside a multi-token
    line → still MISMATCH — the regression guard), `tests/test_ocr_scoring.py`
    (`TestRelativeMassThreshold`, `TestRepeatedOrphanCollapse`: long-doc
    steady-state noise → LOW, short-doc real cluster → MEDIUM, repeated
    identical orphan collapses, distinct orphans don't). Re-baselined one
    pre-existing test (`test_ocr_only_prose_medium`) that had unintentionally
    relied on 3 *identical*-text OCR_ONLY divergences (now correctly capped) —
    switched to distinct text so it tests the mass threshold, not the cap.
  - **Acceptance:** `tests/test_acceptance_samples.py::
    test_microsoft_long_invoice_ocr_crosscheck_not_high` — real PaddleOCR
    engine on `Microsoft-Sample-Invoice_clear.pdf`, asserts LOW or
    INCONCLUSIVE (skips gracefully if PaddleOCR unavailable).
  - **Verified on the real file:** HIGH 95 → **LOW 15** (2204/2204 agree, 0
    mismatch; residual 16-orphan mass ≈3.9 capped vs relative floor ≈44.1).
    Full suite: **692 passed, 1 skipped**.

- [x] **GPU-OOM regression: long PDF scored HIGH again (every embedded word a
  false orphan)** (2026-06-18)
  - Symptom: `Microsoft-Sample-Invoice_clear.pdf` scored **OCR HIGH 75** with
    **4504 divergences** — nearly every embedded word flagged EMBEDDED_ONLY
    ("hidden text"). Diagnostics gave `matched=50` (vs the 2204 baseline above):
    OCR produced 52 words on the sparse page 0 and **0 on every dense table
    page**. The user also saw this as "only the first 4–5 pages load".
  - **Root cause: CUDA out-of-memory, silently swallowed.** PaddleOCR's
    auto-growth allocator holds freed-but-cached GPU blocks (≈7.6 GB held while
    only ≈2 GB used after one page on the 8 GB RTX 4060). The next dense page's
    detector conv can't get ~515 MB → `ResourceExhaustedError`, which
    `PaddleOCREngine.recognize`'s `except Exception: return []` turns into an
    empty page. Empty OCR ⇒ every embedded word on that page becomes a false
    hidden-text orphan ⇒ HIGH. NOT a page cap; all 13 pages rasterise/extract
    fine. This is environmental (tighter free VRAM than when the LOW-15 baseline
    above was recorded), so it had silently regressed that acceptance test.
  - **Fix (in `ocr_engine.py` + 2 config knobs), GPU-memory only — no logic
    change to alignment/scoring:**
    - `ocr_max_side_px` (default 2000): downscale the image fed to OCR so the
      working set stays bounded; **rescale the returned boxes back to the full
      `render_dpi` raster space** (`box_scale = 1/scale` in `_parse_result`) so
      the embedded↔pixel correspondence is unchanged. `empty_cache` alone only
      bought ~4 pages (matching the user's "4–5 pages") — the side cap is what
      gets all 13 through.
    - `ocr_empty_cache_between_pages` (default True): `paddle.device.cuda.empty_cache()`
      in `recognize`'s `finally` (best-effort, no-op without paddle/CUDA).
  - **Tests:** `tests/test_ocr_integration.py::TestOCRDownscaleAndRescale`
    (large page downscaled to the cap; boxes rescaled back to raster space;
    small page untouched; `_empty_cache` is a safe no-op). Unit tests use the
    deterministic `StubOCREngine`, untouched.
  - **Verified:** OCR HIGH 75 → **LOW 15** again (matched/agree 2203/2203,
    0 mismatch, all 13 pages). The real-engine acceptance test
  `test_microsoft_long_invoice_ocr_crosscheck_not_high` (which had been
  failing under OOM) now PASSES. Full suite: **821 passed, 1 skipped**.

- [x] **Clean W-2 false-positive fix: reading-order swaps + form-logo OCR**
  (2026-06-22)
  - Implemented `docs/plans/ocr_crosscheck_form_false_positive_plan.md`. The
    clean `test_files/W2_XL_input_clean_1000.pdf` previously produced two ID
    MISMATCHes (`Statutory 13` vs `13 Statutory`) and two ID OCR_ONLY findings
    (`IRSefile`), pushing divergence mass above the relative MEDIUM threshold.
  - Added config-gated, order-insensitive token-bag agreement for multi-token
    non-AMOUNT/DATE groups. Identical folded token multisets now absorb pure
    extraction/OCR reading-order swaps; amount/date ordering and every genuine
    character edit remain strict.
  - Added a config-gated OCR_ONLY premise check in real orchestration: an
    unmatched OCR box is surfaced as a possible over-content raster patch only
    when it overlaps a page embedded-text box (6px configurable margin). Small
    non-overlapping logo/seal/watermark clusters are omitted from findings and
    mass; pages with at least 8 such boxes retain them via the configurable
    safety valve for image-forensics review.
  - Real PaddleOCR/GPU acceptance: the W-2 OCR stage is **LOW** and neither
    `Statutory` nor `IRSefile` surfaces as a non-AGREE finding. Focused OCR
    suite: **104 passed**. Full suite: **906 passed, 25 skipped**.

## Next — Stage 6 (raster/pixel forensics)
Stage 3 hands off scanned/image-only PDFs via `routed_to="image_forensics"`.
Stage 6 would consume `AnalysisContext.rasterized_pages()` + run ELA /
copy-move / AI-inpainting / double-JPEG-quantisation → `StageResult`. See
`docs/FORGERY_METHODS.md` for the raster-level taxonomy.
