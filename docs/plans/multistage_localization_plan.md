# Multi-stage bounding-box localization plan

**Status:** PLAN (implementation deferred to a later session). Read-only investigation
complete; no source changed by this doc.

**Scope:** extend on-page bounding-box localization — already shipped for
`revision_recovery` — to the other spatially-localizable detection stages:
`invoice_arithmetic`, `font_forensics`, `ocr_crosscheck`.

**Explicitly out of scope** (see [§9](#9-out-of-scope-and-non-localizable-categories)):
`provenance_metadata` (a producer/timestamp anomaly is a *document-level* fact with no
on-page location — a deliberate non-localizable category, not a gap) and raster/pixel
forensics (Stage 4, not yet built; its output is a heatmap/mask that would require
widening the one-rect `BBox` contract — separate future work).

**Constraint:** pdfplumber / pdfminer / pypdfium2 / pytesseract / pikepdf only.
**No PyMuPDF/fitz in production.** (`tests/test_microsoft_pdf.py` is a stray fitz scratch
script, excluded from collection — do not emulate it.)

---

## 0. The reference implementation (what we mirror, with citations)

Localization already works end-to-end for `revision_recovery`. Every new stage is the
**same small increment** against these existing contracts — do **not** invent parallel
mechanisms.

### 0.1 The canonical normalized box (the one contract everything funnels into)

`src/pdf_forgery/aggregate/models.py:31` — `BBox`:

```python
@dataclass(frozen=True)
class BBox:
    """Normalized [0,1], top-left origin, page-relative.
    (x0,y0) = top-left corner, (x1,y1) = bottom-right."""
    x0: float; y0: float; x1: float; y1: float
```

One rect per finding. DPI- and page-size-independent: the frontend multiplies by the
rendered page's pixel dimensions. `None` means "this stage cannot localize this finding
yet." `bbox` is already on `AggregateFinding`/`AdvisoryFinding`
(`aggregate/models.py:74`, `:131`) and is in `ADVISORY_FINDING_ALLOWLIST`
(`aggregate/models.py:103`) — **coordinates already cross the PHI boundary**; nothing to
widen.

### 0.2 The normalization site (the single place that learns each stage's geometry)

`src/pdf_forgery/aggregate/aggregate.py:174` — `_finding_bbox(stage_result, finding, index)`.
Today it returns a `BBox` for `revision_recovery` and `None` for everyone else
(`aggregate.py:192`). Its `revision_recovery` branch is the exact template
(`aggregate.py:192-222`):

1. Recover the rich finding via `_payload_findings(payload)[index]` (positional 1:1).
2. **Positional-integrity guard** (`aggregate.py:200-203`): verify
   `finding.page == rf.page_index` *and* `tuple(finding.object_ids) == tuple(rf.object_ids)`
   before trusting the index; on mismatch return `None` (never attach a box to the wrong
   finding).
3. Union the located sub-boxes, divide by page dims, `_clamp01` each coord → `BBox`.

This function already dispatches per-stage in a sibling function, `_finding_type`
(`aggregate.py:98-144`) — including the `ocr_crosscheck` AGREE-filter realignment
(`aggregate.py:111-115`, `_ocr_divergences_excluding_agree` at `:156`). **We extend
`_finding_bbox` with per-stage branches mirroring `_finding_type`'s structure.**

### 0.3 The stage-side carrier (what the stage attaches)

`revision_recovery` attaches geometry **on the rich finding**:
`models.py:402` `BoxPt` (pdfplumber space — top-left, points) and `models.py:417`
`FindingLocation(boxes, page_width_pt, page_height_pt, page_rotation)`, hung on
`Finding.location` (`models.py:475`). It is populated post-scoring by
`extract/locate.py:locate_findings` (never raises; emits diagnostic notes), and
gated by `Config.enable_localization` (`locate.py:210`).

**The crux of this whole plan:** `invoice_arithmetic` and `font_forensics` findings
**already carry native bbox geometry** — they are just missing the *page dimensions*
needed to normalize/flip. `ocr_crosscheck` carries pixel-space geometry but does not
persist the per-page pixel dimensions. So for each stage the increment is:

> **(a) attach page dimensions** (the one missing datum), then **(b) teach `_finding_bbox`
> to normalize that stage's native geometry into the canonical `BBox`.** The
> stage-agnostic renderer then draws it.

### 0.4 The renderer (stage-agnostic; no per-stage rendering code)

Two render paths, both already stage-agnostic:

- **Primary — CSS overlay over a bare page image.** `aggregate/jobs.py:137`
  `render_page` → `overlay.py:render_page_overlay(pdf_bytes, page, boxes=[], cfg)` renders
  the page with *no* boxes (`jobs.py:150`); the frontend draws each finding's normalized
  `bbox` as a CSS box over it (`webapp/app.js` `renderDocPane`). **This already works for
  any stage that populates `bbox`** — zero new rendering code. (Endpoint
  `GET /v1/jobs/{job}/pages/{page}/image.png`, `api.py:185`.)
- **Secondary — baked single-finding evidence PNG.** `aggregate/jobs.py:152`
  `render_overlay` bakes one finding's boxes into the raster
  (`overlay.py:25 render_page_overlay`, which takes **pdfplumber points, top-left**, and
  scales by `dpi/72`). It currently special-cases `revision_recovery` (`jobs.py:165-167`).
  See [§8](#8-the-gated-baked-png-evidence-path) for making it stage-agnostic.

### 0.5 PHI boundary (unchanged)

- The normalized `BBox` is coordinate-only → crosses the scrub boundary (already
  allowlisted). **Do not widen the allow-list.**
- Any baked PNG contains real document pixels → **PHI** → served only via the gated
  evidence endpoints (`api.py:174`, `:185`), never the advisory/`AdvisoryInput` channel.
  `overlay.py:8-11` states this; keep it true for every stage.

---

## 1. Cross-cutting rules (state once, apply to all three)

1. **One rect per finding, canonical `BBox`** — normalized `[0,1]`, top-left origin.
   Where a finding implicates several sub-boxes (multiple cells / multiple glyphs /
   embedded+OCR), **union them into one rect** (min x0/y0, max x1/y1) before normalizing.
2. **Reuse the stage-agnostic overlay** ([§0.4](#04-the-renderer-stage-agnostic-no-per-stage-rendering-code)).
   No per-stage rendering code.
3. **PHI** — coordinate-only `BBox` crosses; any baked PNG stays PHI-side/gated. Same as
   `revision_recovery`.
4. **Never-raise + graceful degradation** — if a finding cannot be localized, return
   `None` **plus an explicit diagnostic note**, never a silent empty and never an
   exception. Convert every could-not-localize into a visible, diagnosable state (same
   hardening as `extract/locate.py`). Notes flow to the stage report's `notes` /
   `AggregateResult.notes` so they surface in diagnostics.
5. **Positional-integrity guard, per stage** — before trusting `payload.findings[index]`
   (or the AGREE-filtered divergence list for OCR), cross-check **stable fields**
   (`page` + the stage's natural identifier — see each section). On mismatch return
   `None` + a note. This makes any future adapter filter/reorder fail *safe*, not wrong.
6. **Config-exposed knobs, per stage** — nothing hard-coded. Each stage gets at least
   `enable_localization: bool = True`.
7. **Uniform normalization is the substrate fusion reasons over** — see
   [§7](#7-fusion-note-why-uniform-normalization-is-load-bearing).

### 1.1 Shared geometry helper (new) — `core/geometry.py`

All three stages need to turn a native bbox into a canonical `BBox`. There are exactly
two native spaces. Add ONE shared, pure, dependency-free helper module so the algebra
lives in one place (direction stays `stages → core`, never the reverse):

```python
# core/geometry.py  (new; pure, no third-party imports)

def pdf_bbox_to_canonical(
    bbox: tuple[float, float, float, float],   # (x0,y0,x1,y1) PDF user space, BOTTOM-LEFT
    *, page_width_pt: float, page_height_pt: float, rotate: int = 0,
) -> BBox | None:
    """Bottom-left PDF points -> canonical normalized top-left BBox.
    Returns None if page dims are non-positive or rotate not in {0,90,180,270}."""

def pixel_bbox_to_canonical(
    bbox: tuple[float, float, float, float],   # (x0,y0,x1,y1) PIXEL space, TOP-LEFT
    *, page_width_px: float, page_height_px: float,
) -> BBox | None:
    """Already-top-left pixel bbox -> canonical normalized BBox (divide + clamp)."""
```

**`pdf_bbox_to_canonical` is exactly the rotation algebra already proven in
`ocr_crosscheck/align.py:54 embedded_to_pixel`** (its R=0/90/180/270 corner-traced
formulas at `align.py:69-103`). Under normalization the `scale = dpi/72` factor cancels,
so the implementation is literally:

```
px = embedded_to_pixel(bbox, page_height_pt=H, page_width_pt=W, dpi=72, rotate=r)
rendered_w, rendered_h = (W, H) if r in (0, 180) else (H, W)   # pypdfium2 swaps on 90/270
return BBox(clamp(px[0]/rendered_w), clamp(px[1]/rendered_h),
            clamp(px[2]/rendered_w), clamp(px[3]/rendered_h))
```

For `rotate == 0` this reduces to the familiar flip
`y0n=(H-y1)/H, y1n=(H-y0)/H, x0n=x0/W, x1n=x1/W` — identical in spirit to the
`revision_recovery` normalization at `aggregate.py:213-222` (which skips the flip only
because pdfplumber already hands it top-left visual coords).

**Recommended refactor (optional, low-risk):** lift the rotation algebra from
`align.embedded_to_pixel` into `core/geometry.py` and have `align` call it, so there is a
single copy. If the owner prefers minimal churn, `core/geometry.py` may instead *import*
`embedded_to_pixel` from `ocr_crosscheck.align` — but that makes `core` depend on a stage,
violating the one-way direction, so the lift is preferred. (Listed in
[§10](#10-decide-before-implementing).)

`_clamp01` already exists at `aggregate.py:169`; reuse/relocate it.

---

## 2. invoice_arithmetic (lowest effort — do first)

### 2.1 Investigation findings (against source)

- **Native geometry already on the finding.** `ArithmeticFinding.bbox`
  (`invoice_arithmetic/models.py:254`) is the localized **output cell** — the
  conventional tamper target (the broken total/amount cell). `Cell.bbox`
  (`models.py:98-99`) is documented as **`(x0,y0,x1,y1)` in pdfminer/PDF user space,
  origin BOTTOM-LEFT** — confirmed, not assumed. The finding also carries `page_index`
  (`models.py:240`), `cell_role` (`:252`), `relationship_kind` (`:243`),
  `convergence_count` (`:258`), and `corroborating` (`:262`).
- **Adapter is unfiltered 1:1.** `adapter.py:80`:
  `findings = tuple(_finding_to_core(f) for f in report.findings)` — so
  `payload.findings[index]` lines up with `stage_result.findings[index]` directly. Core
  `Finding.page = f.page_index`, `object_ids=()` (`adapter.py:51-52`).
- **Per-page / segmentation awareness exists.** `LogicalInvoice.page_indexes`
  (`models.py:159-160`), `Cell.page_index` (`models.py:97`). A finding names exactly one
  page (`finding.page_index`); its output cell sits on that page.
- **Missing datum: page dimensions.** `analyze.py:139-144` extracts glyphs via
  `core.glyphs.glyphs_from_layouts` / `glyphs_from_bytes` and discards the per-page
  `LTPage.width/height`. `Glyph` carries `page_index`+`bbox` but **no page dims**
  (`core/glyphs.py:42-57`). This is the original "`_finding_bbox` needs page height to
  flip origin" blocker noted in `aggregate/CLAUDE.md`.

### 2.2 What to box

**Default: the output cell alone** (`ArithmeticFinding.bbox`). It is precisely where the
forged value sits, and convergence already proves *that* cell is the tamper target. A
config knob `localize_union_input_cells` (default `False`) optionally unions the
implicated input cells (`Relationship.input_cells` → but note `ArithmeticFinding` does not
currently retain `input_cells`; if the knob is enabled, thread `input_cells` bboxes onto
the finding first). **Recommendation: ship default (output cell) first; the union is a
follow-up knob.**

Multi-page invoices: use `finding.page_index` for the page and the output cell's bbox for
the rect (the cell is on that page by construction). Cross-row checks
(`LogicalInvoice.allow_cross_row_checks`) don't change *which* page the output cell is on.

### 2.3 Files / functions to add or modify

- **`invoice_arithmetic/models.py`** — add to `InvoiceReport`:
  `page_dims: tuple[tuple[float, float], ...] = ()` (indexed by `page_index`;
  `(width_pt, height_pt)`) and `page_rotations: tuple[int, ...] = ()`.
- **`invoice_arithmetic/analyze.py`** — in `_extract` (`:139`), when `ctx.page_layouts`
  is present, capture `(float(p.width), float(p.height))` per page into `page_dims`; get
  `page_rotations` via the `ocr_crosscheck/analyze.py:323 _get_rotations` pattern (pikepdf
  `/Rotate`, reusing `ctx.pikepdf_doc` when available). For the `glyphs_from_bytes`
  fallback path (no layouts), best-effort re-derive dims from pdfplumber page sizes or
  pikepdf `/MediaBox`; on failure leave `page_dims` short and let `_finding_bbox` degrade
  (note). Populate both on the `InvoiceReport`.
- **`invoice_arithmetic/config.py`** — add `enable_localization: bool = True` and
  `localize_union_input_cells: bool = False`. (Localization is gated here even though the
  conversion lives in aggregate, so a stage can opt out and stop paying for dim capture.)
- **`aggregate/aggregate.py`** — add an `invoice_arithmetic` branch to `_finding_bbox`
  ([§2.4](#24-_finding_bbox-branch)).

### 2.4 `_finding_bbox` branch

```python
if stage == "invoice_arithmetic":
    rich = _payload_findings(payload)            # report.findings, 1:1
    if rich is None or index >= len(rich): return None
    rf = rich[index]
    # Positional-integrity guard: page + natural identifier (no object_ids here).
    if finding.page != getattr(rf, "page_index", None): return None
    if finding.high_value != (rf.high_value.value if rf.high_value else None): return None
    page = rf.page_index
    dims = getattr(payload, "page_dims", ())
    if page is None or page >= len(dims):
        note("invoice_arithmetic: page dims unavailable for page ...; not localized")
        return None
    W, H = dims[page]
    rot = payload.page_rotations[page] if page < len(payload.page_rotations) else 0
    bbox = rf.bbox  # union with input cells here when localize_union_input_cells
    if bbox == (0.0, 0.0, 0.0, 0.0):            # output_cell was None (models.py:230-233)
        note("invoice_arithmetic: finding has no localized cell; not localized")
        return None
    return pdf_bbox_to_canonical(bbox, page_width_pt=W, page_height_pt=H, rotate=rot)
```

Natural identifier for the guard: `page_index` + `high_value` (cheap, stable). Optionally
also compare `rounded(rf.bbox)` against nothing on the core side (core `Finding` doesn't
carry bbox), so `page + high_value + relationship_kind` is the strongest available;
`relationship_kind` is reconstructable from `rf` only, so it's a self-consistency check —
keep the guard to fields that exist on *both* sides (`page`, `high_value`).

### 2.5 Edge cases

- Output cell is `None` → `bbox == (0,0,0,0)` sentinel (`models.py:230-233`) → `None` +
  note. **Never** normalize the zero box (would land a rect at the page corner).
- Page dims missing/short (fallback extraction) → `None` + note.
- Rotated page → handled by `pdf_bbox_to_canonical` (the algebra), no degradation needed.
- Degenerate cell (x0==x1 or y0==y1) → produces a zero-area but valid box; allow it
  (clamp keeps it in range) — a reviewer still sees the location.

### 2.6 Test (`tests/test_invoice_localize.py`, new)

- **Known-position tamper:** build (or reuse `scripts/make_invoice_fixtures.py`
  `invoice_convergence_tamper.pdf`) an invoice whose forged amount cell sits at a known
  fraction of the page. Run the stage → aggregate; assert the `invoice_arithmetic-*`
  finding's `bbox` lands within ±0.02 of the expected `(x0,y0,x1,y1)` fraction, and that
  `y0 < y1` with top-left orientation (i.e. the flip happened — assert the box is in the
  page's lower half if the cell is printed low, proving bottom-left→top-left).
- **Degradation:** finding with output cell `None` → `bbox is None` + a note present;
  `page_dims=()` → `bbox is None` + note.
- **Rotation:** a `/Rotate 90` fixture (or unit-test `pdf_bbox_to_canonical` directly with
  `rotate=90`) → box still inside `[0,1]`.
- **Positional guard:** monkeypatch a payload whose `findings[index].page_index` disagrees
  with the core finding → `None`.
- Keep the existing suite green.

---

## 3. font_forensics (medium effort — do second)

### 3.1 Investigation findings (against source)

- **Native geometry already on the finding.** `FontFinding.bbox`
  (`font_forensics/models.py:87`) is the offending **token** box; `suspicious_bboxes`
  (`models.py:110`) are per-glyph boxes for the strong intra-token signal. Both come from
  the shared `core.glyphs` extractor: `Token.bbox` (`core/glyphs.py:73`) is the union of
  its glyph boxes, and glyph boxes are **pdfminer `LTChar` bbox — PDF user space,
  BOTTOM-LEFT origin** (`core/glyphs.py:43`, "size / bbox" per-char attribution).
  Confirmed bottom-left.
- **Two evidence strengths, both positioned (preserve the distinction):**
  - **Strong signal — `INTRA_TOKEN_FONT_MIX`** (`models.py:60`): minority glyph(s) inside
    a token in a foreign font (the single-inserted-char fingerprint). Carries
    `suspicious_glyph_indexes` (`models.py:106`) and `suspicious_bboxes` (`:110`).
  - **Supporting signal — whole-token difference** (`WHOLE_TOKEN_SUBSET_DIFFERENCE` /
    `WHOLE_TOKEN_FAMILY_DIFFERENCE` / `*_BASELINE_DEVIATION`, `models.py:41-52`): the whole
    token differs from line/page/document context. Position = the token bbox.
- **Adapter is unfiltered 1:1.** `adapter.py:99`:
  `tuple(_font_finding_to_core(f) for f in report.findings)`. Core `Finding.page =
  f.page_index`, `object_ids=()`, `high_value = f.high_value.value or None`
  (`adapter.py:66-76`). `before/after` are `None`.
- **Missing datum: page dimensions.** Same as invoice — `analyze.py:122-128` extracts
  glyphs and discards `LTPage.width/height`.

### 3.2 What to box (and how to keep the strong/supporting distinction)

Pick **one** glyph source and be consistent: **`core.glyphs` / pdfminer `LTChar`
(bottom-left)** — already the source of every `FontFinding.bbox`, so there is nothing to
reconcile. (Do *not* mix in pdfplumber word boxes here; that would be a second extraction
path and a coordinate-origin mismatch.)

- **`INTRA_TOKEN_FONT_MIX`** → box the **union of `suspicious_bboxes`** (precise — points
  at the inserted char(s)). Fall back to the token bbox if `suspicious_bboxes` is empty.
  Knob `localize_intra_token_use_suspicious_glyphs: bool = True`; when `False`, box the
  whole token for visibility.
- **Whole-token kinds** → box the **token bbox** (`FontFinding.bbox`).
- **Multi-token / token-spanning findings:** a single `FontFinding` is one token, so one
  rect. If a future detector emits a finding spanning multiple tokens/runs, union their
  boxes into one rect (cross-cutting rule 1). State this; no such finding exists today.

The strong/supporting distinction is preserved automatically: it already lives in
`FontFinding.kind`/`tier` (→ `AggregateFinding.type` via `_finding_type` at
`aggregate.py:126-130`, which reads `rich[index].kind.value`). The bbox just localizes
both; the *tier* still differentiates them.

### 3.3 Files / functions to add or modify

- **`font_forensics/models.py`** — add to `FontReport`:
  `page_dims: tuple[tuple[float, float], ...] = ()` and
  `page_rotations: tuple[int, ...] = ()`.
- **`font_forensics/analyze.py`** — capture `page_dims` from `ctx.page_layouts`
  (`:122`) and `page_rotations` via the `_get_rotations` pattern; populate on `FontReport`.
  Same `glyphs_from_bytes` fallback handling as invoice (degrade with note).
- **`font_forensics/config.py`** — add `enable_localization: bool = True` and
  `localize_intra_token_use_suspicious_glyphs: bool = True`.
- **`aggregate/aggregate.py`** — add a `font_forensics` branch to `_finding_bbox`.

### 3.4 `_finding_bbox` branch

```python
if stage == "font_forensics":
    rich = _payload_findings(payload)
    if rich is None or index >= len(rich): return None
    rf = rich[index]
    if finding.page != getattr(rf, "page_index", None): return None
    if finding.high_value != (rf.high_value.value if rf.high_value else None): return None
    page = rf.page_index
    dims = getattr(payload, "page_dims", ())
    if page is None or page >= len(dims):
        note("font_forensics: page dims unavailable ...; not localized"); return None
    W, H = dims[page]
    rot = payload.page_rotations[page] if page < len(payload.page_rotations) else 0
    # Choose source boxes (strong vs token).
    if rf.kind is FontFindingKind.INTRA_TOKEN_FONT_MIX and cfg_use_suspicious and rf.suspicious_bboxes:
        boxes = rf.suspicious_bboxes
    elif rf.bbox != (0,0,0,0):
        boxes = (rf.bbox,)
    else:
        note("font_forensics: finding has no glyph geometry; not localized"); return None
    x0=min(b[0] for b in boxes); y0=min(b[1] for b in boxes)
    x1=max(b[2] for b in boxes); y1=max(b[3] for b in boxes)
    return pdf_bbox_to_canonical((x0,y0,x1,y1), page_width_pt=W, page_height_pt=H, rotate=rot)
```

Natural identifier for the guard: `page` + `high_value`. (Token text is PHI-adjacent and
lives only on the rich side; `page + high_value + kind` are the stable cross-checkable
fields — `kind` is rich-only, so guard on `page + high_value`.)

Reading `cfg_use_suspicious` in aggregate: `_finding_bbox` has access only to
`AggregateConfig`. **Decision:** read the per-stage knob off the rich payload's own
config if the report carries it, else default `True`. Simpler: gate the *source choice*
on whether `suspicious_bboxes` is populated (it only is for `INTRA_TOKEN_FONT_MIX`), and
expose the override as a `font_forensics` config that, when `False`, causes `analyze` to
**not** populate `suspicious_bboxes` for localization purposes — keeping the knob inside
the stage. (See [§10](#10-decide-before-implementing).)

### 3.5 Edge cases

- Placeholder/unknown-font tokens never anchor a finding (already true in `detect.py`), so
  no zero-font boxes arrive here.
- `suspicious_bboxes` empty on an intra-token finding (shouldn't happen) → fall back to
  token bbox + note.
- Token bbox zero/sentinel → `None` + note.
- Rotation handled by the algebra.

### 3.6 Test (`tests/test_font_localize.py`, new)

- **Known-position tamper:** reuse `scripts/make_font_fixtures.py`
  `font_edited_subset.pdf` (amount `50,000` re-embedded in a foreign subset). Assert the
  `font_forensics-*` finding `bbox` lands at the expected fraction (±0.02), top-left
  oriented.
- **Strong vs supporting:** for an `INTRA_TOKEN_FONT_MIX` fixture (e.g. the Acrobat
  `18071.23` inserted-`0` case, skip-if-absent), assert the box is *tighter* than the
  token (suspicious-glyph union) when the knob is on, and equals the token box when off.
- **Degradation:** missing page dims → `None`+note; empty suspicious bboxes → token
  fallback.
- **Rotation:** unit-test `pdf_bbox_to_canonical(rotate=90)`.
- **Positional guard:** mismatched `page_index` → `None`.
- Keep the suite green.

---

## 4. ocr_crosscheck (trickiest — do last)

### 4.1 Investigation findings (against source)

- **Native geometry is already pixel-space, top-left.** `WordBox.bbox`
  (`ocr_crosscheck/models.py:65-73`): "**always PIXEL space with a top-left origin
  `(x0,y0,x1,y1)`, `y0<y1`**." Embedded words are transformed into pixel space via
  `align.embedded_to_pixel` **before** storage (so embedded and OCR boxes are already
  comparable, `models.py:70-73`). OCR boxes come straight from pytesseract/PaddleOCR in
  raster pixels (`align.quad_to_bbox`, `align.py:34`). **Rotation is already baked in** —
  `embedded_to_pixel` applied `/Rotate` (`align.py:54-103`), and OCR runs on the rendered
  raster. So **no flip and no rotation matrix is needed for OCR** — only divide by the
  rendered page's pixel dimensions.
- **The divergence carries the geometry.** `Divergence` (`models.py:82-97`):
  `embedded: tuple[WordBox, ...]`, `ocr: WordBox | None`, `type`, `token_class`,
  `page_index`. The divergence region is where text-layer and OCR disagree.
- **⚠ Adapter FILTERS — the AGREE gotcha (verified).** Unlike the other four stages,
  `adapter._divergence_to_finding` **returns `None` for `DivergenceType.AGREE`**
  (`adapter.py:33-34`), and `report_to_stage_result` builds findings via
  `tuple(f for d in result.divergences if (f := _divergence_to_finding(d)) is not None)`
  (`adapter.py:126-129`). So `stage_result.findings[index]` does **NOT** line up with
  `result.divergences[index]` — it lines up with the **AGREE-filtered** list. The
  aggregate already solves this for `_finding_type` via
  `_ocr_divergences_excluding_agree` (`aggregate.py:156-166`):
  `[d for d in divergences if d.type.value != "agree"]`. **`_finding_bbox` MUST use the
  same helper to index** — never `result.divergences[index]` raw.
- **Missing datum: per-page pixel dimensions.** `analyze.py:191` already computes
  `pw, ph = _page_px_dims(page_layouts[pi], scale, rot)` (`_page_px_dims` at
  `analyze.py:350`, rotate-swapped). But this is local to the loop and not persisted on
  `Stage3Result`/`OCRCrossCheckReport`. `render_dpi` is on `RenderProvenance`
  (`models.py:111`); `_get_rotations` exists (`analyze.py:323`).

### 4.2 What to box

The divergence region = **union of all usable boxes** in the divergence:

- **MISMATCH** → union(`embedded` boxes ∪ `ocr` box). (Both exist; gives the full
  disagreeing region.) Knob `localize_include_ocr_box: bool = True`; when `False`, box
  only the embedded side (the text-layer claim).
- **EMBEDDED_ONLY** → union(`embedded` boxes) (`ocr is None`). Hidden/invisible text.
- **OCR_ONLY** → the `ocr` box (`embedded` empty). Image-layer overlay.
- **No usable box** (degenerate: EMBEDDED_ONLY with empty `embedded`, or OCR_ONLY with
  `ocr is None`) → degrade to the other side if available, else `None` + note.

### 4.3 Files / functions to add or modify

- **`ocr_crosscheck/models.py`** — add to `OCRCrossCheckReport` (or `Stage3Result`):
  `page_dims_px: tuple[tuple[float, float], ...] = ()` (indexed by `page_index`;
  `(width_px, height_px)` at `render_dpi`). Storing **pixel** dims directly avoids
  re-deriving dpi/rotate downstream and matches `WordBox`'s pixel space exactly.
- **`ocr_crosscheck/analyze.py`** — accumulate `pw, ph` per page (already computed at
  `:191`) into a `page_dims_px` list and set it on the report. (For the text-sparse /
  routed-to short-circuit at `:222`, dims may be partial — fine; those reports have no
  findings to localize.)
- **`ocr_crosscheck/config.py`** — add `enable_localization: bool = True` and
  `localize_include_ocr_box: bool = True`.
- **`aggregate/aggregate.py`** — add an `ocr_crosscheck` branch to `_finding_bbox`,
  reusing `_ocr_divergences_excluding_agree`.

### 4.4 `_finding_bbox` branch (AGREE-filter aware)

```python
if stage == "ocr_crosscheck":
    divergences = _ocr_divergences_excluding_agree(payload)   # SAME filter as _finding_type
    if divergences is None or index >= len(divergences): return None
    d = divergences[index]
    # Positional-integrity guard: page + token_class + type vs the core finding.
    if finding.page != d.page_index: return None
    if finding.high_value != _HIGH_VALUE_LABEL_OR_NONE(d.token_class): return None
    page = d.page_index
    dims = getattr(payload, "page_dims_px", ())
    if page is None or page >= len(dims):
        note("ocr_crosscheck: page pixel dims unavailable ...; not localized"); return None
    Wpx, Hpx = dims[page]
    boxes = []
    if d.embedded: boxes += [w.bbox for w in d.embedded]
    if d.ocr is not None and cfg_include_ocr: boxes.append(d.ocr.bbox)
    if not boxes and d.ocr is not None: boxes.append(d.ocr.bbox)   # degrade: ocr-only
    if not boxes:
        note("ocr_crosscheck: divergence has no usable box; not localized"); return None
    x0=min(b[0] for b in boxes); y0=min(b[1] for b in boxes)
    x1=max(b[2] for b in boxes); y1=max(b[3] for b in boxes)
    return pixel_bbox_to_canonical((x0,y0,x1,y1), page_width_px=Wpx, page_height_px=Hpx)
```

Guard fields available on both sides: `page` (== `d.page_index`) and `high_value`
(== mapping of `d.token_class`; `_HIGH_VALUE_LABEL` at `adapter.py:21` maps AMOUNT/DATE/ID,
PROSE→`None`). These are exactly what the adapter used to set the core finding, so they are
the correct, stable cross-check. Use `pixel_bbox_to_canonical` (no flip — already
top-left).

### 4.5 Edge cases

- **AGREE filter (the headline gotcha):** always index the AGREE-filtered list. A unit
  test must plant an AGREE divergence *between* two real ones and assert the boxes still
  map to the right findings.
- EMBEDDED_ONLY with empty `embedded` / OCR_ONLY with `None` ocr → degrade or `None`+note.
- Page pixel dims missing (routed/text-sparse report) → `None` + note (those reports
  carry no findings anyway).
- Rotation: **already handled upstream** in `embedded_to_pixel`; do **not** re-apply.
  (This is the one stage that is fully rotation-correct for free.)
- Off-page boxes (clipping-guard survivors) → `_clamp01` keeps them in `[0,1]`.

### 4.6 Test (`tests/test_ocr_localize.py`, new)

- **Known-position divergence:** construct a `Divergence` (or use the synthetic
  WordBox-level fixtures already in `tests/test_ocr_*`) with a MISMATCH at a known pixel
  region on a page of known pixel dims; assert `bbox` ≈ region / dims (±0.02).
- **AGREE-filter alignment:** divergence list `[MISMATCH, AGREE, OCR_ONLY]` → two findings;
  assert finding 0's box is the MISMATCH region and finding 1's is the OCR_ONLY region
  (proves `_finding_bbox` skipped AGREE identically to the adapter).
- **Degradation:** EMBEDDED_ONLY with empty embedded → `None`+note; missing `page_dims_px`
  → `None`+note.
- **Rotation sanity:** because rotation is upstream, a `/Rotate 90` page's box should still
  be inside `[0,1]` and land on the rendered (rotated) raster — assert via a small rotated
  fixture or by checking `Wpx/Hpx` swap.
- **Positional guard:** mismatched `page_index`/`token_class` → `None`.
- Keep the suite green (note: the real-PaddleOCR acceptance tests skip gracefully when the
  engine is absent — keep that pattern; the localization unit tests must NOT require a GPU,
  build `Divergence`/`WordBox` directly).

---

## 5. Page-dimension carriers — summary

| Stage | Native bbox space | Carrier to add | Conversion |
|---|---|---|---|
| invoice_arithmetic | PDF points, **bottom-left** (`models.py:99`) | `InvoiceReport.page_dims` (pt) + `page_rotations` | `pdf_bbox_to_canonical` (flip + rotate) |
| font_forensics | pdfminer `LTChar` points, **bottom-left** (`core/glyphs.py:43`) | `FontReport.page_dims` (pt) + `page_rotations` | `pdf_bbox_to_canonical` (flip + rotate) |
| ocr_crosscheck | **pixel, top-left** (`models.py:65-73`) | `OCRCrossCheckReport.page_dims_px` (px) | `pixel_bbox_to_canonical` (divide only) |

All three then funnel into the one `BBox` contract and the one renderer.

---

## 6. Sequencing (each increment independently shippable on the same contract)

1. **`core/geometry.py`** (`pdf_bbox_to_canonical`, `pixel_bbox_to_canonical`, `_clamp01`
   relocation) + its unit tests. Foundation for all three.
2. **invoice_arithmetic** — lowest effort (native bbox already the tamper cell; pure
   wiring + origin flip). Ships first.
3. **font_forensics** — adds the glyph-source choice (suspicious-glyph union vs token) but
   reuses the same point→canonical helper.
4. **ocr_crosscheck** — last; reuses `pixel_bbox_to_canonical` (no flip) but must thread
   the **AGREE-filter** indexing and persist pixel dims.

Each stage is a self-contained PR: attach page dims → add one `_finding_bbox` branch → add
one test file. None depends on another beyond step 1.

---

## 7. Fusion note — why uniform normalization is load-bearing

Consistent `[0,1]` top-left normalization **across all stages** is the substrate the
cross-stage co-location / co-reference fusion logic (the "Session C" escalation) reasons
over: when two or more stages localize findings to the **same region** of the same page,
that spatial agreement is itself strong corroboration → HIGH escalation (e.g. a forged
amount cell flagged by `invoice_arithmetic` *and* an intra-token font seam at the same box
from `font_forensics`, *and* an OCR↔text mismatch there from `ocr_crosscheck`). That
comparison is only meaningful if every stage's box is in the **same coordinate space** —
which is exactly what canonical `BBox` guarantees. So uniform normalization is **not
cosmetic**; it is the geometric key fusion joins on. `provenance_metadata` stays the
deliberate non-spatial exception (it corroborates by *document-level* footprint, not
location — see `fusion.py` corroborator role).

---

## 8. The gated baked-PNG evidence path

The **primary** UI path needs nothing new: `render_page` renders a bare page and the
frontend draws every finding's normalized `bbox` (CSS) — already stage-agnostic
([§0.4](#04-the-renderer-stage-agnostic-no-per-stage-rendering-code)).

The **secondary** baked single-finding PNG (`jobs.py:152 render_overlay`) currently
special-cases `revision_recovery` and reaches into `rf.location.boxes` (points,
`jobs.py:165-186`). To make it stage-agnostic **without per-stage rendering code**, drive
it from the canonical `BBox` instead of stage-native geometry:

1. Look up the `AggregateFinding` for `finding_id`; if its `bbox is None` → `None`.
2. Convert canonical `BBox` → top-left **points** for that page:
   `boxes_pt = [(x0·Wpt, y0·Hpt, x1·Wpt, y1·Hpt)]`, where `Wpt/Hpt` are the page's
   **visual** point dims (pypdfium2 page size with `/Rotate` applied — fetch from the
   rendered page, or reuse the stage's stored dims).
3. Call the existing `overlay.render_page_overlay(pdf_bytes, page, boxes_pt, cfg)`
   unchanged (it scales points by `dpi/72`, `overlay.py:46-61`).

This deletes the `revision_recovery`-only branch and works for all stages from one code
path. **Optional / lower priority** — the CSS path already covers the product need; do
this only if the gated baked-PNG evidence view is wanted for the new stages. (Note:
`revision_recovery` boxes are pdfplumber **visual** points; the canonical-`BBox` round-trip
yields the same rect, so behavior is preserved.)

---

## 9. Out of scope and non-localizable categories

- **`provenance_metadata` — deliberate non-localizable category, not a gap.** A producer
  string, an `ModDate > CreationDate` anomaly, an XMP/`/Info` mismatch is a *document-level*
  fact with **no on-page location**. Its `_finding_bbox` stays `None` permanently and that
  is correct. Document this explicitly so a future reader does not mistake the `None` for
  unfinished work. It corroborates via fusion's document footprint, not geometry.
- **Raster/pixel forensics (Stage 4, not yet built).** Its output is a heatmap / pixel
  mask (ELA, copy-move, inpainting, double-JPEG), which does **not** fit the one-rect
  `BBox` contract — representing it faithfully would mean widening the contract (a mask or
  multi-rect region). That is separate future work; mentioned here only to mark the
  boundary. Do **not** attempt to squeeze a mask into a single `BBox`.

---

## 10. Decide before implementing

These are the few genuinely-open choices (everything else is resolved above):

1. **Shared-helper location & the rotation algebra's single copy.** Recommended: **lift**
   the R=0/90/180/270 algebra from `ocr_crosscheck/align.py:embedded_to_pixel` into
   `core/geometry.py` and have `align` delegate to it (one copy; preserves `stages → core`
   direction). Alternative (more churn-averse, but inverts the dependency direction):
   `core/geometry.py` imports `embedded_to_pixel` from the stage. Pick the lift unless the
   owner objects to touching `align`.
2. **font_forensics strong-signal box granularity.** Default resolved to **suspicious-glyph
   union** for `INTRA_TOKEN_FONT_MIX` (tighter, points at the inserted char), token box for
   whole-token kinds, with knob `localize_intra_token_use_suspicious_glyphs`. Confirm the
   owner prefers the tight box over always-token (some reviewers want the whole token
   highlighted for readability).
3. **How aggregate reads per-stage localization knobs.** `_finding_bbox` only sees
   `AggregateConfig`. Resolved approach: keep per-stage knobs *inside the stage* and let
   them shape what the stage **persists** (e.g. when `localize_include_ocr_box=False`, the
   stage still stores both boxes and aggregate unions them — so this knob may instead need
   to live on `AggregateConfig`). Decide per-knob whether it gates *capture* (stage config)
   or *union* (aggregate config). Recommendation: capture-affecting knobs → stage config;
   union/source-choice knobs → `AggregateConfig` (so they're tunable at the normalization
   site without re-running stages).
4. **invoice input-cell union.** Ship output-cell-only first; `localize_union_input_cells`
   requires threading `Relationship.input_cells` bboxes onto `ArithmeticFinding` (not
   currently retained, `models.py:236-270`). Decide whether the union view is wanted before
   adding that field.

---

## 11. Acceptance checklist (per stage)

- [ ] Stage report carries page dimensions (pt for invoice/font, px for OCR) + rotations.
- [ ] `_finding_bbox` has a branch that: applies the positional-integrity guard (page +
      natural identifier; OCR also re-applies the AGREE filter), unions sub-boxes,
      converts via the shared helper, returns a canonical `BBox` or `None`+note.
- [ ] Could-not-localize is always `None` **plus a diagnostic note** — never silent, never
      an exception.
- [ ] New `config` knobs; nothing hard-coded; `enable_localization` honored.
- [ ] No PyMuPDF/fitz; only pdfplumber/pdfminer/pypdfium2/pytesseract/pikepdf.
- [ ] New test file: known-position tamper asserts the normalized box at the expected
      fraction (±0.02, top-left oriented) + degradation cases + positional guard + rotation.
- [ ] Existing suite stays green (currently 758 passed, 1 skipped).
- [ ] No per-stage rendering code added; the stage-agnostic overlay draws the box.
```