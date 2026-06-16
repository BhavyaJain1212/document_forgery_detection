# Stage 3 — OCR ↔ embedded-text divergence (cross-check layer)

**Status: DESIGN CONTRACT (Session 3.0). No detection logic implemented this
session — interfaces + stubs only.** Detection lands in 3.1 / 3.2 (see
`docs/TODO.md`).

Stage 3 is the **cross-check layer**. The structural stages
(`revision_recovery`, `font_forensics`) reason about *what the PDF file claims*
— its objects, revisions, glyph attribution. They are blind to a whole class of
forgeries where the **rendered pixels disagree with the embedded text layer**:

- an **image-layer overlay** pasted over the original text (the text layer still
  says the old value; the page renders the new one);
- **hybrid layer mixing** — part real text, part rasterised patch;
- **hidden / invisible text** — text in the layer that never renders (render
  mode 3, off-page, white-on-white, zero-size), used to spoof a text layer that
  contradicts the image.

Stage 3 catches these by extracting text **two independent ways** and looking
for *divergence*:

1. the deterministic **embedded text layer** via `pdfminer.six` (the same path
   the other stages already use, surfaced through `AnalysisContext.page_layouts`
   / `core.glyphs`), and
2. text recovered from the **rendered raster** — `pypdfium2` rasterises each
   page, then a pluggable OCR engine (default **PaddleOCR**, GPU) recognises it.

If the two sources disagree on a spatially-matched word — especially a
currency / date / ID token — that is the signal the structural stages cannot
see.

This stage assumes a **digital-native PDF with a real text layer**. A
scanned/image-only PDF is out of scope and is short-circuited to INCONCLUSIVE
with a hand-off flag (§5), never reported as mass divergence.

---

## 0. Where Stage 3 sits

- Subpackage: `src/pdf_forgery/ocr_crosscheck/`, a sibling of
  `revision_recovery/`, `font_forensics/`, `invoice_arithmetic/`,
  `provenance_metadata/`. It exposes a `core.Stage` (`OCRCrossCheckStage`) and an
  adapter mapping its rich report ↔ `StageResult`, exactly like every other
  stage. Direction stays one-way: `ocr_crosscheck → core`, never the reverse.
- **Fusion role: SUBSTANTIVE** (it can *originate* a verdict — it observes a real
  content/render divergence), not a corroborator. So it joins
  `FusionConfig` alongside `revision_recovery` / `font_forensics` /
  `invoice_arithmetic`; it is NOT added to `corroborator_stages`. A lone
  Stage-3 MEDIUM fuses to MEDIUM; a Stage-3 HIGH originates HIGH; a Stage-3 MEDIUM
  plus an independent substantive/corroborator signal escalates per the existing
  fusion rules. (Wiring it into `test.py`/`pipeline` `STAGES` is a 3.2 task — the
  stub is intentionally NOT registered this session so it cannot fire
  `NotImplementedError` in a live run.)
- **Queue split** (mirrors the project's CPU/GPU Celery routing):
  - **GPU queue** — raster render (`pypdfium2`) + OCR recognition
    (`ocr_engine.recognize`). The only GPU-bound step.
  - **CPU queue** — pdfminer embedded extraction, coordinate alignment,
    normalization, divergence classification, scoring.
  The two halves communicate only through the small `WordBox` list (positions +
  text + confidence + source), so the GPU step can run on a separate worker and
  hand back a serialisable result.
- **PHI-safe logging** (project invariant #10): log **counts, positions, token
  classes, and salted hashes** of tokens — **never raw extracted text**. Raw
  `before`/`after` strings live only inside the in-memory `Finding`/`Evidence`
  the human reviewer reads; they are never emitted to structured logs.

---

## 1. Coordinate alignment

The two sources live in different coordinate systems:

| Source | Origin | Unit | Y direction |
|---|---|---|---|
| pdfminer (`LTChar`/`core.glyphs.Glyph`) | bottom-left | points (1/72 in) | up |
| OCR on raster (pixels) | top-left | pixels | down |

### Render DPI

**Default render DPI = 300.** Rationale:

- 300 DPI is the de-facto floor for reliable document OCR — at 150 DPI (the
  `AnalysisContext.rasterized_pages` default used for cheap previews) small
  amount/date glyphs fall below PaddleOCR's reliable recognition size and inflate
  OCR noise, which directly drives false `MISMATCH`/`OCR_ONLY` divergence.
- 300 DPI keeps a typical A4 page at ~2480×3508 px — large enough for 8–10 pt
  body text, small enough to stay within GPU memory for batched recognition.
- Higher DPI (400/600) gives marginal recall for tiny text at a real
  memory/latency cost; expose it as config (`render_dpi`) but default 300.

Stage 3 renders at its **own** DPI (`render_dpi`, default 300) — it does **not**
reuse the 150-DPI preview cache. The DPI actually used is recorded in the report
provenance for reproducibility.

### The transform (embedded → pixel)

For a render at `D` DPI of a page whose pdfminer media box is
`page_width_pt × page_height_pt`:

```
scale         = D / 72.0
page_height_px = page_height_pt * scale          # = raster height in pixels

x_px = x_pt * scale
y_px = page_height_px - (y_pt * scale)            # flip Y: bottom-left → top-left
```

A pdfminer bbox `(x0, y0, x1, y1)` (bottom-left, points) maps to a pixel bbox:

```
left   = x0 * scale
right  = x1 * scale
top    = page_height_px - (y1 * scale)            # note: y1 (upper edge) → smaller px-y
bottom = page_height_px - (y0 * scale)            # y0 (lower edge) → larger px-y
```

so `top < bottom` in pixel space, as expected for a top-left origin. **All
matching happens in pixel space**: embedded boxes are transformed *into* pixel
space rather than transforming OCR boxes back, because OCR boxes can be rotated
quadrilaterals — we reduce each OCR quad to its axis-aligned pixel bbox once and
compare like-with-like.

A non-zero page `/Rotate` (90/180/270) is applied to the embedded bbox *before*
the flip (pypdfium2 renders the rotated page, so the raster is already upright);
the rotation handling is part of the transform contract and is unit-testable in
isolation.

### Matching primitive — word-level, not 1:1

Unit of comparison is the **word box**. Embedded words come from
`core.glyphs.group_lines(...)` (the shared extractor — do **not** add a second
extraction path); OCR words come from the engine's detection boxes.

Matching is **detection-driven and one-to-many**. PaddleOCR's detector groups
characters into a *line/phrase* box, so one OCR box routinely covers several
embedded words. We therefore match in two passes:

1. **Center-containment (primary).** An embedded word matches an OCR box when the
   embedded word's *center point* falls inside the OCR box (in pixel space). One
   OCR box may thus claim **many** embedded words → a `one-to-many` group. This is
   robust to the OCR detector drawing a looser/tighter box than the glyph extent.
2. **IoU ≥ `iou_floor` (fallback).** When no center is contained (e.g. the OCR
   box is split differently), fall back to axis-aligned IoU; an embedded↔OCR pair
   with `IoU ≥ iou_floor` (default 0.30) is matched.

For a one-to-many group, the embedded side is **re-joined in reading order**
(left-to-right, top-to-bottom by the embedded boxes) into a single comparison
string, and compared against the OCR box's recognised string as a whole. This
prevents a benign tokenisation difference ("Rs5,000" vs "Rs 5,000") from firing
as divergence. Comparison/tolerance then runs per §3 on the joined strings, and
high-value sub-tokens within the group are still classified individually so a
single altered amount is not diluted by surrounding prose.

Unmatched embedded words → candidate `EMBEDDED_ONLY`; unmatched OCR words →
candidate `OCR_ONLY` (both subject to the §4 guards before they count).

---

## 2. Divergence taxonomy

Every comparison yields exactly one `DivergenceType`. Base weights feed the
weighted aggregation in §6; the **token class** (§3) multiplies the weight.

| Type | Definition | Forgery class implicated | Base weight |
|---|---|---|---|
| `AGREE` | matched, text equal within tolerance | none — corroboration | `0.0` |
| `MISMATCH` | spatially matched, text differs **beyond tolerance** (§3) | **image-layer overlay / hidden-text swap** — the layer says one thing, the page renders another | `1.0` (strongest) |
| `EMBEDDED_ONLY` | embedded word, **no** OCR counterpart (and it *should* render — passes the clipping guard §4a) | **hidden / invisible text** — present in the layer, never rendered (render-mode 3, white-on-white, zero-size, covered by an overlay) | `0.6` |
| `OCR_ONLY` | OCR word, **no** embedded counterpart (above the confidence floor §4b) | **image-layer overlay** — rendered text with no backing text object (a pasted raster patch / scanned splice over a native page) | `0.7` |

Notes:
- `AGREE` carries weight 0 but is **counted** — the AGREE:divergent ratio drives
  the routing/normalisation sanity checks and the score denominator (§6), so a
  single mismatch on an otherwise fully-agreeing page scores differently from one
  mismatch on a page that barely matched at all.
- `MISMATCH` is the strongest because it is the hardest to produce benignly: the
  same region carries two *different* texts. `OCR_ONLY` ranks above
  `EMBEDDED_ONLY` because rendered-but-not-embedded text is a more direct overlay
  signature than embedded-but-not-rendered (which has more benign causes —
  ligatures, decorative glyphs, off-page operators).

---

## 3. Normalization + tolerance policy (the crux)

The whole stage lives or dies on **separating OCR noise from real divergence**.
OCR is lossy; naïve string equality would fire on every page. The policy folds
away *recognised* noise, then applies a length-scaled edit-distance tolerance —
**inverted for high-value tokens**.

### 3a. Fold both sides before comparing

Applied to both the embedded string and the OCR string before any distance is
computed:

1. **Unicode NFC + the project normalizer.** Reuse
   `revision_recovery.extract.normalize` semantics (NFC, strip zero-width /
   soft-hyphen, collapse whitespace, trim) so Stage 3 can never drift from the
   other stages' notion of "same text".
2. **Case fold** (`str.casefold`) — OCR casing is unreliable.
3. **Whitespace fold** — already collapsed; additionally drop *all* internal
   spaces for the one-to-many joined comparison (OCR word-spacing is arbitrary).
4. **OCR-confusion fold** — map a curated set of visually-confusable forms to a
   single canonical class on *both* sides, so a confusion is never counted as an
   edit. Config: `ocr_confusion_classes`. Default classes:
   - `0 ↔ O ↔ o ↔ Q ↔ D` → `0`
   - `1 ↔ l ↔ I ↔ | ↔ i` → `1`
   - `rn ↔ m` → `m`   (digraph fold, applied as a substring pass)
   - `5 ↔ S ↔ s` → `5`
   - `8 ↔ B` → `8`
   - `2 ↔ Z ↔ z` → `2`
   - `, ↔ .` in numeric context → unified separator (decimal/grouping ambiguity)
   The fold is intentionally *lossy* — it deliberately erases exactly the deltas
   OCR is known to invent, so what survives is real.

### 3b. Length-scaled Levenshtein tolerance (prose)

After folding, compute Levenshtein distance `d` between the two folded strings.
A pair is `AGREE` iff `d ≤ allowed_edits`, else `MISMATCH`:

```
allowed_edits(prose) = max(prose_floor_edits,
                           floor(len(folded) * prose_rel_tol))
```

Defaults: `prose_rel_tol = 0.15`, `prose_floor_edits = 1`. So a long sentence
tolerates ~15 % churn (OCR drops/merges characters); a short word tolerates one
edit. This keeps prose noise from flooding the report.

### 3c. CRITICAL INVERSION — high-value tokens get STRICTER tolerance

For a token classified high-value (currency / date / ID), **a single-character
delta IS the signal, not noise** — `5,000 → 50,000`, `2023 → 2028`,
`POL-4471 → POL-4477`. So the tolerance *tightens*:

```
allowed_edits(amount) = 0            # after confusion-fold: zero tolerance
allowed_edits(date)   = 0
allowed_edits(id)     = 0  (strong)  # or id_rel_tol≈0.0 — see note
```

- **Token classification reuses the existing classifier** —
  `revision_recovery.highvalue.classify_token` / `classify_token_kind`
  (AMOUNT / DATE / ID_LIKE, with `TokenClassification` strength + context). No
  second classifier. A one-to-many group is classified on its *sub-tokens*: if
  any sub-token is high-value, that sub-token is compared at strict tolerance
  even if the surrounding prose is folded loosely.
- **Confusion-fold still applies first** even at zero tolerance, so a genuine
  `O`-vs-`0` OCR confusion inside an amount does *not* false-fire — but a real
  `5,000`→`50,000` survives the fold (the inserted `0` is a true length change,
  not a confusable substitution) and registers as `MISMATCH`.
- ID is the noisiest high-value class (the existing classifier already treats it
  as WEAK). `id_strict` defaults `True`; an environment drowning in ID noise can
  set `id_rel_tol > 0` to relax just IDs without touching amounts/dates.

This inversion — loose on prose, zero on money/dates — is the single most
important calibration in the stage. It is the reason a one-character amount edit
hidden under an overlay can reach 90–100 (§6) while a paragraph of OCR mush stays
quiet.

---

## 4. False-positive guards

Two guards run **before** an unmatched word is allowed to count as divergence.

### 4a. Clipping guard (the pdfminer-vs-fitz divergence — do NOT let it fire)

pdfminer reports text-positioning operators that lie **outside the visible page
box** (negative coordinates, beyond the media/crop box, scratch text far off the
page). These never render, so OCR correctly never sees them — but naïvely they
look like mass `EMBEDDED_ONLY` divergence. This is exactly the
pdfminer-vs-fitz/raster discrepancy already understood from Stage 1; it must not
fire as forgery.

**Guard:** an embedded word whose **pixel bbox falls outside the rendered page
rect** (`[0,0,width_px,height_px]`, minus a small `clip_margin_px` slack, default
2 px) is an off-page operator. It is **excluded from scoring entirely** — not
counted as `EMBEDDED_ONLY`, not counted in the denominator. The count of
excluded off-page words is recorded as a diagnostic note (PHI-safe: a count, not
the text).

Partial overlap rule: a word whose center is inside the page rect but whose box
slightly overruns the edge is kept (clipped at the boundary); only words wholly
(or center-) outside are dropped.

### 4b. OCR-confidence floor

The OCR engine returns a per-word confidence. Words below `ocr_conf_floor`
(default `0.50`) are **dropped before matching** — they are the engine guessing
at noise/texture and are the dominant source of phantom `OCR_ONLY`/`MISMATCH`.
Dropped-word count is recorded as a diagnostic note. The floor is config so a
high-recall environment can lower it (accepting more noise) and a
high-precision one can raise it.

---

## 5. Routing — scanned / text-sparse short-circuit

Stage 3 is only meaningful on a PDF with a **real embedded text layer**. A
scanned / image-only PDF has (almost) no embedded text → *every* OCR word is
`OCR_ONLY` → naïve scoring would scream HIGH forgery on a perfectly innocent
scan. That is wrong, and it is a different forensic route (image forensics).

**Detection:** after extraction (and the §4 guards), compute the embedded word
count. Route to the scanned branch when the page/document is text-sparse:

```
scanned  ⟺  embedded_word_count < min_embedded_words           # absolute floor
        OR  embedded_word_count < embedded_ocr_ratio_floor * ocr_word_count
```

Defaults: `min_embedded_words = 10` (per page; documents roll up worst-case but
the *route* decision is whole-document by summed counts),
`embedded_ocr_ratio_floor = 0.10` (fewer than 1 embedded word per 10 OCR words ⇒
essentially OCR-only).

**Behaviour:** short-circuit to **INCONCLUSIVE** with
`routed_to = "image_forensics"` and a clear reason ("scanned / text-sparse PDF:
no real text layer to cross-check; hand off to image-forensics route"). **No
divergence is scored** in this case — Stage 3 explicitly declines rather than
emitting mass OCR_ONLY. This is the analogue of revision_recovery's
single-revision INCONCLUSIVE: the method does not apply, so say so and route on.

(`routed_to` is `None` on the normal digital-native path.)

---

## 6. Scoring → confidence tiers

Aggregate the surviving divergences (after §4 guards, on the non-scanned path)
into a single token-weighted score, then map to the locked
`core.ConfidenceTier` schema (INCONCLUSIVE / LOW / MEDIUM / HIGH, bands
0-30 / 30-70 / 70-100).

### 6a. Per-divergence weight

```
weight(d) = base_weight[d.type] * token_multiplier(d.token_class)
```

`token_multiplier` defaults: `amount = 3.0`, `date = 3.0`, `id = 1.5`,
`prose = 1.0`. (High-value multipliers are large *and* their tolerance is zero,
so a single altered amount dominates.)

### 6b. Aggregate

A normalized, saturating aggregate so one strong signal can reach the top of the
band while many weak ones accumulate but cannot alone fabricate HIGH:

```
divergence_mass = Σ weight(d)  over divergent d (MISMATCH/EMBEDDED_ONLY/OCR_ONLY)
agree_mass      = Σ 1          over AGREE
```

The headline tier is decided by a **rule tree** (consistent with the other
stages — explicit rules, not a black-box sum):

- **INCONCLUSIVE** — scanned/text-sparse route (§5), OR OCR engine unavailable /
  raster backend missing (degrade gracefully, record why), OR zero matched words
  to compare. `score = None`, `routed_to` set when applicable.
- **HIGH (70-100)** — at least one `MISMATCH` **on a high-value token**
  (amount/date/ID), i.e. the rendered value and the embedded value of a
  currency/date/ID disagree beyond zero tolerance. **Amount/date MISMATCH → 90-100**
  ("high-value field render-divergence"); ID MISMATCH → 80-89; a high-value
  `OCR_ONLY`/`EMBEDDED_ONLY` (an amount that renders with no text backing, or a
  layer amount that never renders) → 70-85.
- **MEDIUM (30-70)** — divergence present but not a high-value MISMATCH:
  prose `MISMATCH`(es), or clusters of `OCR_ONLY`/`EMBEDDED_ONLY` exceeding
  `medium_divergence_mass` (default 2.0) — consistent with a partial overlay /
  hybrid patch the structural stages should be cross-checked against. Score scales
  with `divergence_mass` within the band.
- **LOW (0-30)** — only sparse, low-mass prose divergence below
  `medium_divergence_mass`, fully explained as residual OCR noise; the text layer
  and the render substantially agree.

Per-finding tiers mirror this (one `Finding` per divergent group, HIGH for a
high-value MISMATCH, etc.), exactly as the other stages' adapters annotate
findings.

### 6c. Why a high-value MISMATCH can reach 90-100

`MISMATCH` base weight `1.0` × amount multiplier `3.0` = `3.0`, with zero
tolerance meaning it only fires on a *real* delta after confusion-folding. That
single divergence clears the HIGH threshold on its own — which is the whole point
of the stage: a one-character amount change that the structural stages cannot see
(no new revision, no font seam) but that the render exposes.

---

## 7. Interfaces + stubs

All modules live under `src/pdf_forgery/ocr_crosscheck/`. **Data models
(dataclasses/enums) and `OCRCrossCheckConfig` are real** (they are contracts, not
detection logic). **Every logic function is a stub** with a full signature +
docstring that raises `NotImplementedError`. Nothing computes a divergence this
session.

### Module map

| Module | Responsibility | Queue |
|---|---|---|
| `models.py` | `WordBox`, `WordSource`, `DivergenceType`, `TokenClass`, `Divergence`, `RenderProvenance`, `Stage3Result`, rich `OCRCrossCheckReport` | — (pure data) |
| `config.py` | `OCRCrossCheckConfig` — DPI, IoU/center thresholds, confusion classes, tolerances, guards, score values/weights, routing floors | — |
| `ocr_engine.py` | `OCREngine` Protocol + `PaddleOCREngine` default (GPU); records engine name / model version / language / device | **GPU** |
| `align.py` | embedded→pixel transform; center-containment + IoU matching; one-to-many grouping | CPU |
| `normalize.py` | fold (NFC/case/space/confusion); length-scaled + inverted high-value tolerance; Levenshtein | CPU |
| `divergence.py` | classify each matched/unmatched group into a `Divergence` (taxonomy §2) | CPU |
| `guards.py` | clipping guard (§4a) + OCR-confidence floor (§4b) | CPU |
| `routing.py` | scanned / text-sparse detection + short-circuit (§5) | CPU |
| `scoring.py` | weighted aggregate → tier/score rule tree (§6) | CPU |
| `analyze.py` | orchestration: render+OCR (GPU) → extract+align+classify+score (CPU) → `OCRCrossCheckReport` | both |
| `adapter.py` | `OCRCrossCheckReport` ↔ `core.StageResult`; JSON + advisory human summary | — |
| `stage.py` | `OCRCrossCheckStage` (`core.Stage`) | — |
| `__init__.py` | facade re-exports | — |

### Core dataclasses (real)

```python
class WordSource(str, Enum):      EMBEDDED, OCR
class DivergenceType(str, Enum):  AGREE, MISMATCH, EMBEDDED_ONLY, OCR_ONLY
class TokenClass(str, Enum):      AMOUNT, DATE, ID, PROSE     # maps from highvalue.HighValueKind

@dataclass(frozen=True)
class WordBox:
    text: str
    bbox: tuple[float, float, float, float]   # pixel space, top-left origin (x0,y0,x1,y1)
    source: WordSource
    conf: float | None                         # OCR confidence; None for embedded
    page_index: int

@dataclass(frozen=True)
class Divergence:
    type: DivergenceType
    embedded: tuple[WordBox, ...]              # one-to-many: >1 on the embedded side
    ocr: WordBox | None
    token_class: TokenClass
    weight: float
    page_index: int

@dataclass(frozen=True)
class RenderProvenance:                         # reproducibility record
    engine: str                                # e.g. "paddleocr"
    model_version: str
    language: str                              # e.g. "en"
    device: str                                # "cuda:0" / "cpu"
    render_dpi: int

@dataclass(frozen=True)
class Stage3Result:
    tier: ConfidenceTier
    score: int | None
    divergences: tuple[Divergence, ...]
    routed_to: str | None                      # "image_forensics" on the scanned route, else None
```

`OCRCrossCheckReport` is the richer per-file payload (path, ok/error, provenance,
per-page divergences, guard/diagnostic counts, the `Stage3Result`) carried as
`StageResult.payload` so the adapter can render the original JSON/summary —
exactly the pattern the other stages use.

### OCR engine — pluggable

```python
@runtime_checkable
class OCREngine(Protocol):
    name: str
    def recognize(self, page_png: bytes, *, dpi: int) -> list[WordBox]: ...
    def provenance(self) -> RenderProvenance: ...
```

Default `PaddleOCREngine` (GPU). The engine name, model version, language, and
device are recorded in `RenderProvenance` on every report for reproducibility.
PaddleOCR / pypdfium2 are **optional** dependencies — when absent, `analyze`
degrades to INCONCLUSIVE with a note (never crashes), matching the project-wide
"report and continue" constraint and `AnalysisContext.rasterized_pages` returning
`[]` when no backend is installed.

### PHI-safe logging helper

A `_safe_token_log(...)` helper emits `{count, page, bbox, token_class,
sha256(salt+folded_token)[:12]}` — never the raw string. All structured logging
in the stage goes through it.

---

## 8. Acceptance for this design session

- [x] Items 1–6 fully specified above (transform, taxonomy+weights,
      normalization/tolerance+inversion, FP guards, routing, scoring→tiers).
- [x] Interfaces + stubs defined (§7): dataclasses real; every logic function a
      `NotImplementedError` stub with signature + docstring; OCR engine behind a
      pluggable `Protocol`; reproducibility fields recorded; GPU/CPU queue split
      stated; PHI-safe logging specified.
- [x] Stubs compile; **no detection logic**; stage intentionally not wired into
      the live `STAGES` list.
- [x] `docs/TODO.md` updated with the 3.1 / 3.2 breakdown.
</content>
</invoke>
