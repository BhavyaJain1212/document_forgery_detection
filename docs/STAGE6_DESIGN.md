# Stage 6 — Raster / Pixel Forensics (`pdf_forgery/image_forensics/`)

> **Status: DESIGN ONLY (Session 6.0, 2026-06-19).** No detector code written
> this session. This document is the canonical contract; the 6.1–6.3
> implementation breakdown lives in `docs/TODO.md`.

Pixel-level tamper detection for **scanned / photographed bills** — the pages
where there is no reliable embedded text or font structure for the digital-native
detectors (Stages 1–4) to analyse. Stage 3 (`ocr_crosscheck`) already detects the
text-sparse / scanned case and emits `routed_to="image_forensics"`; **Stage 6 is
the destination of that hand-off.** Its package name and `STAGE_NAME` are
therefore exactly `image_forensics`, so the route string and the stage identity
match.

Stage 6 is a `core.Stage` like every other detector: `run(pdf_bytes, ctx) ->
StageResult`, read-only, never raises, shares the one `AnalysisContext`. It is a
**SUBSTANTIVE** fusion stage on image-dominant pages (it can originate a verdict);
it returns INCONCLUSIVE — i.e. "no signal" to fusion — on digital-native pages.

---

## 0. Numbering note (read first)

This project's stage numbers were reconciled in this session:

| # | Stage | Role |
|---|---|---|
| 1 | `revision_recovery` | substantive |
| 2 | `font_forensics` | substantive |
| 3 | `ocr_crosscheck` | substantive (routes scanned pages → `image_forensics`) |
| 4 | `invoice_arithmetic` | substantive |
| 5 | `provenance_metadata` | corroborator |
| **6** | **`image_forensics`** (this doc) | **substantive (image-dominant pages only)** |
| 7 | `aggregate` (assembly / advisory / UI) | post-pipeline, **not** a `core.Stage` |

**Stage 7** (`pdf_forgery/aggregate/`) — the post-pipeline assembly / PHI-scrub /
advisory / UI layer — was historically labelled "Stage 6"; it was renumbered to
**Stage 7** (2026-06-19) so that "Stage 6" denotes this raster/pixel-forensics
detector. Its design contract moved to `docs/STAGE7_DESIGN.md` and all its
code/docstring references were repointed accordingly.

---

## Locked decisions (summary)

1. **Stage name = `image_forensics`** (matches Stage 3's `routed_to`).
2. **Activation is per-page and conservative** — SUBSTANTIVE only on
   image-dominant pages (text floor mirroring Stage 3, OR a single raster image
   dominating the page area). Digital-native pages → INCONCLUSIVE. **Never run
   pixel forensics on `ctx.rasterized_pages()` (the re-rendered page).**
3. **Forensics run on the ORIGINAL embedded raster bytes** — the DCTDecode /
   JPXDecode / FlateDecode image XObject streams — never on a re-rasterised page.
   Re-rasterisation re-compresses and destroys the JPEG-grid / quantisation
   evidence the JPEG methods depend on. `ctx.rasterized_pages()` is used for **UI
   overlay only**.
4. **Engine abstraction mirrors the `OCREngine` pattern** — a `ForensicMethod`
   interface and a `ForensicProvider` bundle. Default = a **classical
   skimage/OpenCV provider (CPU)**. PhotoHolmes is an **optional** provider behind
   the same interface.
5. **PhotoHolmes: depend optionally, not by default.** Base license is Apache-2.0
   (acceptable — *not* AGPL), but it drags in **PyTorch** (heavy / GPU / VRAM
   contention with PaddleOCR + Ollama) and its strongest DL method (**TruFor**) is
   **non-profit-only** → unusable in a corporate insurance tool. Use the classical
   skimage/OpenCV provider as the default; offer PhotoHolmes as an opt-in provider
   (its classical methods are Apache-2.0 and useful for cross-validation; its DL
   methods stay behind an opt-in flag + VRAM guard, **TruFor excluded** for
   corporate use).
6. **Classical-first method set:** ELA, double-quantisation / double-JPEG (DQ +
   JPEG-grid), and noise/residual inconsistency, all CPU. **Copy-move IS in
   scope** (OpenCV ORB + RANSAC — PhotoHolmes does not provide it) but conservative
   (never HIGH alone). DL methods are opt-in, VRAM-guarded, off by default.
7. **Localization:** every method produces a heatmap in the **embedded image's
   pixel grid**; it is thresholded → connected-component blobs → mapped through the
   image's placement matrix into **page points (pdfplumber top-left convention)**,
   then normalised to the canonical `[0,1]` `BBox` the overlay UI already consumes.
8. **Scoring is conservative — a single weak blob is never HIGH.** HIGH requires
   **two independent, co-located signals** (e.g. a localised double-JPEG ghost AND
   a co-located noise/residual discontinuity). A method that errors on an
   image-dominant page becomes MEDIUM (never silently dropped).
9. **PHI-safe:** logs carry counts / positions / hashes / method names+versions /
   device only — never raw pixels, never the decoded image, never the heatmap
   array. The heatmap/overlay PNG is PHI → gated evidence endpoint only. The
   existing `phi_scrub` allow-list needs **no new raw fields**.

---

## 1. Activation predicate

Stage 6 decides **per page** whether pixel forensics apply. It mirrors Stage 3's
text floor so the two stages partition the document the same way (Stage 3 owns
text-rich pages; Stage 6 owns image-dominant pages).

A page is **image-dominant** (→ SUBSTANTIVE for that page) when **either**:

- **Text floor:** the page's embedded word count `< cfg.min_embedded_words`
  (default `10`, reusing the Stage 3 constant value), **or**
- **Image dominance:** a single decoded raster image XObject covers
  `>= cfg.image_area_dominance_frac` (default `0.60`) of the page's area, after
  mapping the XObject to its placement rectangle (§2).

Otherwise the page is **digital-native** → Stage 6 contributes **nothing** for it
(no pixel forensics on the 300-DPI re-render).

Whole-document roll-up:

- **No image-dominant page** → `StageResult` tier **INCONCLUSIVE**, `score=None`,
  note `"no image-dominant page; pixel forensics not applicable"`. This is the
  "method does not apply" signal — fusion reads INCONCLUSIVE as *no signal*
  (never a drag-down), exactly like `revision_recovery`'s single-revision case.
- **≥ 1 image-dominant page** → analyse those pages; the stage tier is the
  worst-case across analysed pages (§7). Non-image pages still contribute nothing.

```
activate(page) :=
    embedded_word_count(page) < cfg.min_embedded_words
    OR max_image_coverage_frac(page) >= cfg.image_area_dominance_frac
```

`embedded_word_count` comes from `ctx.page_layouts` (already cached); the stage
must **not** re-parse the file. Activation is independent of whether Stage 3
actually ran — the predicate is self-contained — but uses the same threshold so
the hand-off is coherent.

---

## 2. Pixel source — original embedded raster bytes (critical)

**The forensic methods receive the original compressed/decoded image XObject
stream, never `ctx.rasterized_pages()`.** Re-rasterisation (pypdfium2 →
re-encode) is a second compression that erases the very quantisation / JPEG-grid
evidence double-JPEG and ELA rely on. `ctx.rasterized_pages()` is reserved for
drawing the UI overlay (§6 / gated endpoint).

### Locating image XObjects

Via `ctx.pikepdf_doc` (shared, already cached):

1. For each page, walk `page.Resources.XObject` for entries with
   `/Subtype == /Image`. Recurse into form XObjects (`/Subtype /Form`) one level
   to catch images nested in a form's own `/Resources`.
2. Record `(page_index, xobject_id "<obj> <gen>", filter chain, width, height,
   colorspace, bits, /SMask present)`.

### Decoding across colour spaces

Use `pikepdf.PdfImage` as the single decode path:

| Source `/Filter` | How the **original bytes** are obtained | JPEG history? |
|---|---|---|
| `DCTDecode` (JPEG) | `obj.read_raw_bytes()` returns the **JPEG file bytes verbatim** — fed straight to the JPEG/DQ/ELA analysers with **zero re-encode** | **yes** (DQ/ZERO/ELA all valid) |
| `JPXDecode` (JPEG 2000) | `PdfImage(obj)` → decoded pixel array | no (DQ/ZERO N/A; ELA-by-recompress + noise only) |
| `FlateDecode` / raw | `PdfImage(obj).as_pil_image()` → pixel array | no (lossless; DQ/ZERO N/A) |

Colour handling is delegated to `PdfImage`, which resolves `DeviceGray`,
`DeviceRGB`, `DeviceCMYK`, `ICCBased`, and `Indexed`, applies the `/Decode`
array, and composites `/SMask`. The decoder normalises everything to an 8-bit
**RGB or grayscale** ndarray for the methods; CMYK is converted to RGB. A
`DecodedImage` carries: `pixels` (ndarray), `jpeg_bytes` (the original JPEG when
DCTDecode, else `None`), `colorspace`, `page_index`, `xobject_id`, `placement`
(§ below), and a salted content hash.

**Never raises:** an undecodable / unsupported image is skipped with a note
(degrades the page, never the run).

### Mapping an XObject back to a page rectangle (for bbox overlay)

An image is always painted into the unit square `[0,1]²` transformed by the
current transformation matrix (CTM) in effect at its `Do` operator. To recover
the placement rectangle:

1. Tokenise the page content stream (pikepdf `Page.parse_contents` / a small
   operator walker), tracking the graphics-state CTM stack (`cm`, `q`, `Q`).
2. At each `/<Name> Do` whose `<Name>` resolves to our image XObject, snapshot the
   CTM `M`. The four unit-square corners `(0,0),(1,0),(1,1),(0,1)` map through `M`
   to page **user-space points** (bottom-left origin); their axis-aligned bbox is
   the placement rectangle.
3. Convert to **pdfplumber top-left** convention (`top = page_height_pt - y_top`)
   to match the coordinate space `revision_recovery`'s overlay and
   `aggregate._finding_bbox` already use.

The same `M` is the image-pixel → page-point transform used for localization
(§6). If an image is painted more than once (tiling), each placement is analysed
independently. If the content stream can't be parsed, the image is still analysed
but produces no bbox (note recorded) — never a wrong box.

---

## 3. Engine / method abstraction

Mirrors the `ocr_crosscheck.OCREngine` pattern: downstream code depends only on
the interface, the provider is swappable, optional deps import lazily and degrade
via `is_available()`.

```python
@runtime_checkable
class ForensicMethod(Protocol):
    name: str          # canonical, space-free token, e.g. "double_jpeg"
    version: str

    def applicable(self, image: DecodedImage, cfg) -> bool:
        """e.g. DQ/ZERO only when image.jpeg_bytes is not None."""
    def analyze(self, image: DecodedImage, cfg) -> ForensicMap:
        """Heatmap (+ optional scalar) in the image's pixel grid. Never raises."""

@runtime_checkable
class ForensicProvider(Protocol):
    name: str
    device: str                       # "cpu" | "cuda:0"
    def is_available(self) -> bool
    def methods(self, cfg) -> list[ForensicMethod]
    def provenance(self) -> ForensicProvenance   # §9
```

`ForensicMap` = `{ method, version, heatmap (ndarray float in [0,1]), scalar
(float|None), params (dict) }`. Logging only ever touches its scalar / shape /
hash, never the array (§8).

Providers shipped:

- **`ClassicalProvider` (default, CPU)** — skimage / OpenCV / numpy
  implementations of ELA, DQ/double-JPEG, JPEG-grid, noise-residual, copy-move.
  No torch. Always available (hard deps already in the stack: `Pillow`, `numpy`,
  `opencv-python`).
- **`PhotoHolmesProvider` (optional, opt-in)** — wraps PhotoHolmes. Lazy import;
  `is_available()` False when the package/torch is absent. Exposes its classical
  methods (DQ, ZERO, Splicebuster, Noisesniffer — Apache-2.0) freely; its DL
  methods (CAT-Net, PSCC-Net, FOCAL) only when `cfg.enable_dl_methods` **and** the
  VRAM guard passes. **TruFor is never enabled** (non-profit license).
- **`StubForensicProvider` (tests)** — deterministic, seeded by image content
  hash, returns canned heatmaps. CPU, always available — lets the whole stage be
  tested with no skimage/OpenCV/torch.

### VRAM guard (DL methods only)

Before instantiating any DL method: query free VRAM (`torch.cuda.mem_get_info`,
fallback `pynvml`); require `>= cfg.dl_min_free_vram_mb` (default `2048`). We
already run PaddleOCR (Stage 3/4) and Ollama (advisory) on the same RTX 4060 (8
GB) — if either holds memory the guard fails and the stage **degrades to classical
only** with a note. DL is off by default (`enable_dl_methods=False`).

---

## 4. PhotoHolmes evaluation (verified 2026-06-19)

Verified against the GitHub repo and the published paper (arXiv:2412.14969 /
*Multimedia Tools and Applications*, 2025) — not from memory.

| Criterion | Finding | Verdict |
|---|---|---|
| **License** | Base **Apache-2.0** (not AGPL — unlike the PyMuPDF concern). BUT per-method licenses bind when a method is used; **TruFor restricts use to non-profit**. | Base **OK** for an internal corporate tool; **TruFor excluded**; audit each DL method's license before enabling. |
| **Dependency weight** | Pulls **PyTorch** (+ `transformers` for some methods, installed manually). Heavy; GPU. | **Fail as a hard/default dependency.** Optional only. |
| **CPU vs GPU methods** | **Classical/CPU:** Noisesniffer, ZERO, DQ, Splicebuster, Adaptive-CFA. **DL/GPU (PyTorch):** CAT-Net, EXIF-as-Language, PSCC-Net, TruFor, FOCAL. | Classical set is exactly what we want CPU-side; DL set contends for VRAM. |
| **Copy-move** | **Not provided.** | We implement copy-move ourselves (OpenCV ORB+RANSAC). |
| **Double-JPEG / double-quant** | Covered by **DQ** (DCT coefficient double-quantisation) and **ZERO** (JPEG grid / compression detection). | Good reference implementations to cross-validate our classical DQ. |
| **Output** | Heatmaps: Adaptive-CFA, DQ, CAT-Net, Splicebuster, EXIF, TruFor. Scalars: Noisesniffer, ZERO, PSCC-Net, FOCAL. | Compatible with our heatmap→bbox localization (§6). |

**Decision:** PhotoHolmes passes the *license* test (Apache-2.0 base) but fails
the *weight* test as a default dependency (torch). → **Default to the classical
skimage/OpenCV provider; ship PhotoHolmes as an opt-in provider** behind the
`ForensicProvider` interface for cross-validation, with its DL methods gated by
`enable_dl_methods` + the VRAM guard and TruFor hard-excluded.

The classical fallback (the default) gives us the same forgery coverage without
PhotoHolmes: ELA (Pillow), double-JPEG/DQ (DCT re-quantisation histogram via
`scipy`/`numpy`), JPEG-grid (block-boundary energy via OpenCV), noise-residual
(median/wavelet residual + local variance map), copy-move (OpenCV ORB+RANSAC).

---

## 5. Method set

All methods run on the **original** image bytes (§2). Heatmap unless noted.

| Method | Forgery targeted | Class / device | Output | FP sources on scanned medical bills |
|---|---|---|---|---|
| **ELA** (error-level analysis) | direct pixel edit + re-save; splice paste | classical / CPU | heatmap | uniform recompression lifts everything; text/line edges and saturated regions ELA-bright by nature → threshold on *local* contrast, not absolute |
| **DQ / double-JPEG** (DCT double-quantisation) | localized edit then re-JPEG; splicing a singly-compressed region into a doubly-compressed page | classical / CPU (JPEG source only) | heatmap (per 8×8 block) | multi-generation scans / rescans are globally double-quantised → only a *localised* break is suspicious |
| **JPEG-grid alignment** (ZERO-style) | splice/overlay paste whose 8×8 grid is misaligned with the host | classical / CPU (JPEG source only) | heatmap / scalar | cropping & resizing shift the grid globally; scanner grid |
| **Noise / residual inconsistency** (Splicebuster/Noisesniffer-style) | splicing, inpainting, overlay paste | classical / CPU | heatmap | scanner denoising and JPEG blocking create texture; flat paper regions have ~no noise → low confidence there |
| **Copy-move** (ORB keypoints + RANSAC affine) | duplicated **stamp / signature / approval mark**; cloned background hiding a deletion | classical / CPU | matched-cluster boxes | repeated *legitimate* elements (logos, table rules, identical glyphs/letterhead) → require RANSAC-verified non-trivial offset + min match count; never HIGH alone |
| **CFA / demosaicing** | (camera-sensor tamper) | classical / CPU | heatmap | **Deferred / near-zero confidence here** — scanned & screenshot docs have no Bayer CFA, mirroring the PRNU/CFA no-sensor guard. Only meaningful for true camera-photo bills; gate behind a sensor-present check. |
| **DL methods** (CAT-Net / PSCC-Net / FOCAL via PhotoHolmes) | general splicing / inpainting; AI generative fill | DL / GPU (PyTorch) | heatmap / mask | trained on natural camera photos → **domain gap** on scanned documents inflates FPs; opt-in + VRAM-guarded; treated as one corroborating signal, never sole HIGH |

**Copy-move scope decision:** **in scope**, implemented with OpenCV ORB+RANSAC in
our classical provider (PhotoHolmes does not provide it). It targets a real
medical-bill forgery (duplicating a "PAID"/approval stamp, or cloning paper to
cover a deleted line). It is conservative: a verified duplication is **MEDIUM**
alone and only reaches HIGH when corroborated by a residual/JPEG break at the
paste boundary (§7).

---

## 6. Localization — heatmap → `BBox`

Each `ForensicMap.heatmap` is in the **decoded image's pixel grid**. To produce
finding bounding boxes in the coordinate convention the overlay UI consumes:

1. **Threshold** the heatmap at a method-specific `cfg` level → binary mask.
2. **Connected components** (`cv2.connectedComponentsWithStats`) → blob bboxes in
   **image-pixel space**; drop blobs `< cfg.min_blob_area_frac` of image area
   (kills speckle) and merge near-touching blobs.
3. **Map image-pixel bbox → page points** using the image placement matrix `M`
   from §2: pixel `(c, r)` → unit-square `(u, v) = (c/W, 1 - r/H)` (image rows run
   top-down; PDF y runs up) → page user-space point `M · (u, v)`. Take the
   axis-aligned bbox of the mapped corners, then flip to **pdfplumber top-left**
   (`top = page_height_pt - y_top`). Result: boxes in **points** + the page's
   `page_width_pt` / `page_height_pt` — the exact shape `revision_recovery`'s
   `FindingLocation` carries.
4. The Stage 6 payload carries these boxes **plus per-page point dimensions** so
   `aggregate._finding_bbox` can normalise to the canonical `[0,1]` top-left
   `BBox` — closing, for this stage, the same gap that was closed for
   `revision_recovery` (a `stage == "image_forensics"` branch in `_finding_bbox`,
   same positional-integrity guard).

The raw heatmap overlay PNG (real pixels) is **PHI** — produced only on demand by
the gated evidence endpoint (reuse `aggregate/overlay.py`'s render+lock pattern),
never carried in the scrubbed advisory channel.

---

## 7. Scoring rule tree

Conservative by design — scanned documents are inherently noisy. Reuses the
shared `core.ConfidenceTier` semantics and bands (INCONCLUSIVE / LOW 0–30 /
MEDIUM 30–70 / HIGH 70–100). Evaluated per image-dominant page; the stage tier is
the **worst-case** across analysed pages (matching the per-page→document
worst-case rollup used elsewhere).

A method "fires" when it localizes a blob (§6) above its threshold. Two fires are
**co-located** when their page-point bboxes overlap by `>= cfg.colocate_iou`
(default `0.30`).

```
INCONCLUSIVE  — no image-dominant page (method not applicable). score = None.

LOW (0–30)    — image-dominant page(s) analysed, and either:
                • no method fires above its noise floor, OR
                • only a GLOBAL/diffuse signal (whole-image ELA lift or uniform
                  double-quantisation) with no localised break — consistent with
                  an innocent rescan / recompression, NOT a local edit, OR
                • a single weak isolated blob from ONE method.

MEDIUM (30–70)— exactly one of:
                • one method localizes a suspicious region but it is
                  UNCORROBORATED (e.g. a DQ ghost with no co-located noise break),
                • a RANSAC-verified copy-move cluster with no corroborating
                  boundary residual (could be a repeated legitimate element),
                • a method ERRORED on an image-dominant page (never silently
                  dropped — mirrors revision_recovery recon-failure → MEDIUM and
                  Stage 3 lone-orphan → MEDIUM).

HIGH (70–100) — TWO INDEPENDENT, CO-LOCATED signals on the same region:
                • localised double-JPEG/DQ ghost AND a co-located noise/residual
                  discontinuity  → 70–85, OR
                • copy-move duplication corroborated by a residual/JPEG break at
                  the paste boundary → 70–85, OR
                • any of the above co-located with an opt-in DL detection → up to
                  90. A single method NEVER reaches HIGH on its own.
```

**Fusion role:** SUBSTANTIVE on image-dominant pages (can originate a verdict; not
added to `FusionConfig.corroborator_stages`). Because scanned evidence is noisy,
the lone-signal MEDIUM cap intentionally leaves room for *cross-stage*
corroboration to lift it in `fusion.fuse()` — e.g. a Stage 6 MEDIUM on a page
whose amount Stage 3's OCR also flags, or a `provenance_metadata` re-render
footprint, escalates MEDIUM→HIGH there (the same mechanism that lifts
`invoice_arithmetic`'s lone gross-break). INCONCLUSIVE (digital-native) is read by
fusion as no signal, never a drag-down.

All thresholds (`min_embedded_words`, `image_area_dominance_frac`, per-method
heatmap thresholds, `min_blob_area_frac`, `colocate_iou`, score-band values,
`enable_dl_methods`, `dl_min_free_vram_mb`) live in `ImageForensicsConfig` —
nothing magic hard-coded outside it.

---

## 8. PHI-safe logging

The decoded image and every heatmap are **document pixels = PHI**. Logging emits
only: per-image counts, blob **positions** (point/normalized bboxes), **salted
hashes** of the source stream bytes, method **names + versions**, **device**,
scalar scores, and blob areas. Never the pixel array, never the heatmap array,
never a crop. Reuse `aggregate.safe_log` / `salted_hash`.

**`phi_scrub` allow-list — confirmed no new raw fields needed.** Stage 6 findings
carry **no before/after text** (there is no text — only pixels). The descriptor
set already supported is sufficient:

- `finding.type` ← the **method / forgery-class label**, a canonical **space-free
  token < 64 chars** (e.g. `double_jpeg`, `splice`, `copy_move`,
  `noise_inconsistency`) so it passes `_assert_looks_like_token`. **Do not** use
  spaced labels like `"double JPEG"`.
- `finding.token_class` ← `None` (or a coordinate-class), never raw text.
- `finding.bbox` ← coordinates (already allow-listed).
- `before` / `after` ← always `None` for this stage.

The heatmap/overlay PNG is served **only** by the gated, audit-logged evidence
endpoint (the one authenticated path real pixels may cross), exactly like the
`revision_recovery` overlay.

---

## 9. Reproducibility manifest

Mirrors Stage 3's `RenderProvenance` convention. Every report records, in
`ForensicProvenance` (carried on the `StageResult.payload`):

- **Per run:** provider name, device, library versions (skimage/opencv/numpy, and
  PhotoHolmes/torch when used), config snapshot (all thresholds), `enable_dl_methods`.
- **Per method:** `name`, `version`, `params` (the exact thresholds applied).
- **Per analysed image:** `page_index`, `xobject_id`, decode path
  (`DCTDecode`/`JPXDecode`/`FlateDecode`), colour space, `(width, height)`, and a
  **salted content hash** of the source bytes — so a result is reproducible
  without retaining the pixels.

---

## Test fixtures needed

Deterministic generators in `scripts/make_image_forensics_fixtures.py`
(Pillow/numpy/opencv only, **no network**); session-scoped build via
`tests/conftest.py` like the other fixtures. Unit tests drive the
`StubForensicProvider`; real-provider acceptance tests skip gracefully when
skimage/OpenCV (or PhotoHolmes) are absent (mirroring the PaddleOCR pattern).

| Fixture | How built | Expected outcome |
|---|---|---|
| **clean scanned bill** (known-negative) | render a clean digital invoice to an image, JPEG-compress at a realistic QF, wrap as a single full-page image PDF | image-dominant, no localised signal → **LOW** (precision baseline) |
| **spliced amount** (known-positive) | take the clean scan, paste a digit region from a different JPEG-history source over the amount, re-save JPEG | co-located **DQ ghost + noise break** → **HIGH** |
| **double-compressed (whole-image) forgery** | edit then re-JPEG the *entire* page at a different QF (global, no localised break) | global double-quantisation only → **≤ MEDIUM** (guards against calling an innocent rescan HIGH) |
| **copy-moved stamp** (known-positive) | duplicate a "PAID"/approval stamp within the page | RANSAC copy-move cluster → **MEDIUM** alone; **HIGH** only with a corroborating boundary residual |
| **digital-native control** | an ordinary multi-page text PDF (reuse an existing fixture) | activation predicate → **INCONCLUSIVE** (proves pixel forensics never run on the re-render) |

The double-compressed and clean-scan fixtures are the **precision** proof for this
stage — the analogue of the still-owed real pristine-invoice baseline for
`invoice_arithmetic`. A real flatbed-scanned untouched bill should eventually be
dropped at `test_pdf's/` to activate a real-data precision test, as a synthetic
always-clean fixture is not a substitute.

---

## Integration checklist (for 6.1–6.3)

- Subpackage `src/pdf_forgery/image_forensics/`, `STAGE_NAME = "image_forensics"`.
- `ImageForensicsStage` conforms to `core.Stage`; read-only; never raises.
- Wire into the live `STAGES` list (`test.py` / pipeline) as a **substantive**
  stage; **not** added to `FusionConfig.corroborator_stages`.
- Adapter maps the rich `ImageForensicsReport` ↔ `core.StageResult` (rich report
  preserved as `payload`); PHI-safe JSON + advisory human-summary renderers.
- `aggregate._finding_bbox` gains a `stage == "image_forensics"` branch.
- Gated heatmap-overlay endpoint reuses `aggregate/overlay.py` (lock + PHI gating).

---

### Sources (PhotoHolmes evaluation)
- [PhotoHolmes GitHub](https://github.com/photoholmes/photoholmes)
- [PhotoHolmes paper (arXiv:2412.14969)](https://arxiv.org/html/2412.14969v1)
- [PhotoHolmes — Multimedia Tools and Applications (Springer, 2025)](https://link.springer.com/article/10.1007/s11042-025-20626-3)
</content>
</invoke>
