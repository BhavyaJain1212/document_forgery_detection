# Stage 6 — Aggregation + PHI-scrub boundary + Advisory + UI (thin slice)

**Status: DESIGN CONTRACT (Session 6.0). No logic implemented this session —
data models / config / interfaces + the advisory prompt are real; every logic
function is a `NotImplementedError` stub.** Implementation lands in 6.1 / 6.2
(see `docs/TODO.md`).

Stage 6 is **not a detector**. The five stages before it
(`revision_recovery`, `font_forensics`, `invoice_arithmetic`,
`provenance_metadata`, `ocr_crosscheck`) each emit a
`core.StageResult`; `fusion.fuse()` already collapses those into one advisory
headline. Stage 6 is the **assembly + presentation layer** that turns that
into something a human reviewer (and a frontend) can consume safely:

1. **Aggregate** — roll the per-stage `StageResult`s up into ONE
   `AggregateResult`: a headline tier/score, the per-stage results, and a *flat*
   list of findings each carrying a stable `finding_id` and a **`bbox`** so a
   future document-overlay is a pure rendering job.
2. **PHI-scrub boundary** — project the `AggregateResult` down to an
   `AdvisoryInput` of finding **descriptors only** (an explicit allow-list).
   This is the trust boundary: nothing else may cross toward the LLM or the
   frontend. NEVER raw extracted text, patient identifiers, or document content.
3. **Advisory** — a swappable local LLM that explains, in plain language and
   grounded ONLY in the supplied descriptors (citing `finding_id`s), why the
   document was flagged or not — without ever re-judging the verdict.
4. **UI** — the reviewer-facing information architecture and the API contract
   the frontend consumes. Copy is decision-support, never an absolute verdict.

This is a **THIN vertical slice**: minimal fusion (delegated to the existing
`fusion.fuse()`), descriptor-only boundary, one advisory prompt, one API shape.
Full fusion (cross-stage geometric correlation, dedup, calibration curve) and
the full Stage 6 build come later.

---

## 0. Where Stage 6 sits

- Subpackage: `src/pdf_forgery/aggregate/`, downstream of every detection stage.
  Direction stays one-way: `aggregate → {core, fusion, stages' adapters}`, never
  the reverse. (Doc named `STAGE6` ↔ package `aggregate`, mirroring the
  established `STAGE3` ↔ `ocr_crosscheck` naming.)
- It does **not** implement a `core.Stage`; it runs *after* the pipeline. Input
  is the `list[StageResult]` that `run_pipeline()` already returns.
- **Headline fusion is delegated, not reinvented.** `aggregate()` calls the
  existing `fusion.fuse()` (the project's evidence-weighted, max-severity-with-
  corroboration rule) for the headline tier/score, then attaches the flat
  finding list + bbox + descriptors. "Minimal fusion only / full fusion
  deferred" means Stage 6 adds NO new fusion math this slice — it wraps the
  current rule via `AggregateConfig.fusion`.
- **GPU note.** The advisory LLM is the only GPU-bound part. It contends with
  PaddleOCR (Stage 3) **only under concurrent load**; in the normal pipeline OCR
  has finished before advisory starts. The advisory model sits behind a
  swappable `AdvisoryEngine` interface (exactly like Stage 3's `OCREngine`), so
  the GPU backend can be swapped, queued, or replaced by the deterministic
  `StubAdvisoryEngine` (no GPU) without touching callers.
- **PHI-safe logging** (project invariant #10): structured logs carry finding
  **descriptors** only (`finding_id`, `stage`, `type`, `tier`, `score`,
  `token_class`, `page`, `bbox`) — never `before`/`after` text, never `reason`
  strings that may quote content. All logging goes through `safe_log`.

---

## 1. AggregateResult schema (item 1)

The roll-up of the per-stage results into one object. It lives **server-side,
behind the PHI boundary** — it still references the rich `StageResult`s (whose
findings carry raw before/after text the gated reviewer-evidence view needs).

```python
@dataclass(frozen=True)
class BBox:
    """Region of a finding on its page, in the CANONICAL overlay space:
    normalized [0,1], top-left origin, page-relative. (x0,y0)=top-left,
    (x1,y1)=bottom-right. None when a stage cannot localise a finding yet."""
    x0: float; y0: float; x1: float; y1: float

@dataclass(frozen=True)
class AggregateFinding:
    """One finding, flattened across stages, as a DESCRIPTOR (no raw text)."""
    finding_id: str               # stable, deterministic: f"{stage}-{n}"
    stage: str                    # originating stage name
    type: str                     # finding/forgery-method kind (see note)
    tier: ConfidenceTier
    score: int | None
    token_class: str | None       # "amount" / "date" / "id" / "prose" / None
    page: int | None
    bbox: BBox | None             # canonical normalized region (see §1a)

@dataclass(frozen=True)
class AggregateResult:
    tier: ConfidenceTier                       # headline (from fusion.fuse())
    score: int | None
    stage_results: tuple[StageResult, ...]     # rich per-stage (server-side only)
    findings: tuple[AggregateFinding, ...]     # flat descriptor list
    reasons: tuple[str, ...]                   # how the headline was reached
    contributing_stages: tuple[str, ...]
    notes: tuple[str, ...]                     # diagnostics (e.g. failed stages)
```

### 1a. The `bbox` decision — carried NOW even though nothing renders it

The future document-overlay (highlight each finding on the rendered page) must
be a **pure rendering job** — read `finding.page` + `finding.bbox`, draw a box.
So `bbox` is in the contract from this slice forward.

- **Canonical space = normalized `[0,1]`, top-left origin, page-relative.** Every
  stage natively reports a different space (`ocr_crosscheck`: pixel space at
  `render_dpi`; `revision_recovery` / `font_forensics`: PDF points, bottom-left
  origin). Normalizing to `[0,1]` per page makes the overlay independent of DPI
  and page size — the frontend multiplies by its rendered page dimensions.
- **Who fills it:** a small per-stage bbox extractor in `aggregate.py`
  (`_finding_bbox(stage_result, finding)`) converts each stage's native geometry
  into the canonical box. For this slice it returns `None` for stages that don't
  yet surface geometry; `ocr_crosscheck` is the first to populate it (its
  `Divergence.embedded[].bbox` / `ocr.bbox` are already pixel-space rects — divide
  by the page pixel size from `RenderProvenance.render_dpi`). The contract is
  fixed now; backfilling each stage's extractor is incremental and does not
  change any consumer.
- `bbox` is **geometry, not PHI** — it carries no document content, so it is
  allowed across the boundary (§2).

### 1b. The `type` and `token_class` descriptors

- `type` — the finding/forgery-method kind, a short stable string supplied per
  stage (e.g. `ocr_crosscheck` → `mismatch` / `embedded_only` / `ocr_only`;
  `revision_recovery` → `content_edit` / `field_edit` / …). It should converge on
  the canonical literals in `docs/FORGERY_METHODS.md`; until each adapter exposes
  one, the extractor derives it from the stage payload. Not free text — an enum-
  like token a UI can group/filter on.
- `token_class` — the high-value class from the existing classifier, mapped from
  `Finding.high_value` (`"amount"`/`"date"`/`"id"`) or `None`/`"prose"`. Lets the
  UI and advisory weight an altered amount over altered prose without ever seeing
  the value.

### 1c. Minimal fusion (config-exposed; full fusion deferred)

`aggregate()` computes the headline by calling `fusion.fuse(stage_results,
config.fusion)` and copying `tier` / `score` / `reasons` / `contributing_stages`
/ `notes` from the returned `FusedAssessment`. No new fusion math is added here.
`AggregateConfig.fusion` exposes the existing `FusionConfig` (corroborator
stages, escalation bonus, bands). **Deferred to full Stage 6:** geometric
correlation of findings across stages (same region flagged by two stages → one
fused finding), cross-stage dedup, and a calibration curve over the headline
score.

---

## 2. The PHI-scrub boundary (item 2)

The single trust boundary in the system. **`AdvisoryInput` is the ONLY object
that may cross toward the LLM or the frontend.** It is defined as an explicit
**allow-list**, not a deny-list: a field exists in `AdvisoryInput` only if it has
been affirmatively cleared as non-PHI. Adding a field to a stage's rich result
can never leak it, because nothing downstream reads the rich result.

```python
# The allow-list, as code: these fields and ONLY these may cross.
ADVISORY_FINDING_ALLOWLIST = (
    "finding_id", "stage", "type", "tier", "score", "token_class", "page", "bbox",
)

@dataclass(frozen=True)
class AdvisoryFinding:
    finding_id: str
    stage: str
    type: str
    tier: ConfidenceTier
    score: int | None
    token_class: str | None
    page: int | None
    bbox: BBox | None          # region id — geometry, not content

@dataclass(frozen=True)
class AdvisoryStage:
    stage: str
    tier: ConfidenceTier
    score: int | None
    ok: bool

@dataclass(frozen=True)
class AdvisoryInput:
    tier: ConfidenceTier                       # headline
    score: int | None
    stages: tuple[AdvisoryStage, ...]
    findings: tuple[AdvisoryFinding, ...]
    notes: tuple[str, ...]                     # diagnostics only (no content)
```

### What is FORBIDDEN to cross (enumerated for reviewers)

- **Raw extracted text** — `Finding.before` / `Finding.after` / `Evidence.*`,
  any OCR / pdfminer word text, any document string.
- **Stage `reason` / `summary` strings** — these frequently quote before→after
  text, so they are NOT allow-listed; the advisory regenerates prose from
  descriptors instead.
- **Patient / claimant identifiers, policy/claim numbers, provider IDs** — note
  that `token_class="id"` (the *class*) is allowed; the ID *value* is not.
- **The rich `payload`** (pikepdf objects, layouts, divergence text, file path).

`token_class` carries the *kind* of a high-value field ("an amount changed"),
never the value ("5,000 → 50,000"). That distinction is the whole boundary.

### Enforcement

- `phi_scrub.to_advisory_input(aggregate)` constructs `AdvisoryFinding`s by
  copying ONLY the allow-listed fields from each `AggregateFinding`. Because
  `AggregateFinding` is already a descriptor (it never held raw text), the scrub
  is a projection, but the **separate `AdvisoryFinding` type is the enforced
  contract** — if `AggregateFinding` ever gains a content field, it still cannot
  reach the LLM/frontend unless someone explicitly adds it to the allow-list.
- `phi_scrub.assert_advisory_safe(advisory_input)` is a defensive post-check that
  walks the object and raises if any field outside the allow-list is present or
  any string looks like leaked content (used in tests and before any egress).

### Where the raw before→after text lives

The detectors' core promise — show the reviewer the exact before→after text —
is **not** abandoned; it simply does not cross *this* boundary. Raw evidence
stays server-side in the `StageResult.payload`s referenced by `AggregateResult`,
reachable only through a **separate, authenticated, audit-logged evidence
endpoint** (`GET /v1/jobs/{id}/findings/{finding_id}/evidence`, out of scope for
this slice). The frontend's default result view and the advisory LLM see
descriptors only.

---

## 3. The advisory LLM prompt (item 3)

A local model that produces **decision-support** text grounded ONLY in the
`AdvisoryInput`. It explains; it never decides. Hard constraints baked into the
system prompt:

- Ground every statement in the supplied findings; **cite `finding_id`s**.
- **Never introduce a claim not present in the findings** (no invented amounts,
  names, page contents — it has none).
- **Never override, re-judge, raise, or lower the verdict.** Report the headline
  tier as given. State **INCONCLUSIVE honestly** ("these methods could not
  assess this document"), never dress it up as clean or as suspicious.
- Decision-support tone: "a reviewer should…", "this warrants review because…";
  the system does not make final determinations.

### System prompt (verbatim — `prompts.SYSTEM_PROMPT`)

```
You are a decision-support assistant for a human fraud reviewer examining a
health-insurance claim document. An automated forgery-detection pipeline has
already analysed the document and produced a set of findings. Your ONLY job is
to explain those findings in plain, measured language so the reviewer can decide
what to do next.

Strict rules:
1. Ground every statement ONLY in the findings provided in the user message.
   Cite the finding_id(s) you are referring to, e.g. (ocr_crosscheck-0).
2. You have NOT been given the document, any names, amounts, dates, or
   identifiers. Do not guess, infer, or invent any such content. If a finding
   says an "amount" token diverged, you may say "an amount field was flagged" —
   you must NOT state what the amount was.
3. Do NOT override, re-judge, raise, or lower the verdict. Report the overall
   tier exactly as given. If it is INCONCLUSIVE, say plainly that these methods
   could not assess the document — do not imply it is clean or forged.
4. This is advisory only. A human reviewer makes the final decision. Use
   decision-support phrasing ("a reviewer should verify…"), never absolute
   claims ("this document is fraudulent").
5. Be concise. No preamble, no restating these rules.

Respond as JSON matching this shape:
{
  "summary": "<2-4 sentence plain-language overview of why the document was or
              was not flagged, naming the overall tier>",
  "tier_statement": "<one honest sentence stating the overall tier and what it
                      means, especially if INCONCLUSIVE>",
  "finding_rationales": [
    {"finding_id": "<id>", "rationale": "<one sentence, grounded in this finding
                                          only, citing its id>"}
  ]
}
```

### User prompt (template — `prompts.USER_PROMPT_TEMPLATE`)

The serialized `AdvisoryInput` (descriptors only) injected as JSON:

```
Overall tier: {tier} (score: {score})

Per-stage results:
{stages_block}          # one line per stage: "<stage>: <tier> (score <score>), ran ok=<ok>"

Findings (descriptors only — no document content was extracted for you):
{findings_json}         # JSON array of AdvisoryFinding

Write the decision-support explanation as instructed.
```

### Output shape (`AdvisoryOutput`)

```python
@dataclass(frozen=True)
class FindingRationale:
    finding_id: str
    rationale: str

@dataclass(frozen=True)
class AdvisoryOutput:
    summary: str
    tier_statement: str
    finding_rationales: tuple[FindingRationale, ...]
    model: str = ""             # engine name + version, for the audit trail
```

The advisory layer validates that every `finding_rationale.finding_id` exists in
the `AdvisoryInput` (the model may not cite an id it was not given) and that the
output is parseable JSON; a malformed response degrades to a templated fallback
summary rather than failing the job.

---

## 4. UI information architecture (item 4)

A claims reviewer's workflow. Every screen frames the result as **decision
support**, never a machine verdict.

### Reviewer states

| State | What the reviewer sees |
|---|---|
| **Upload** | Drop zone for one PDF. Copy: "Upload a claim document for an automated forgery review. Results are advisory; you make the final decision." Shows accepted type/size limits. |
| **Processing** | Per-stage progress list — one row per stage (`revision_recovery`, `font_forensics`, `invoice_arithmetic`, `provenance_metadata`, `ocr_crosscheck`) with a state pill (queued / running / done / skipped / error). A stage erroring shows "could not run" and the others continue. |
| **Result** | **Verdict hero**: overall tier as an advisory band (e.g. "Review recommended — MEDIUM") + score, with the standing caveat "Advisory — a reviewer makes the final call." Then the **advisory explanation** (summary + tier_statement). Then an **expandable per-stage drill-down**: each stage's tier + its findings (descriptor chips: type, token_class, page) with the advisory rationale cited by `finding_id`. (Raw before→after is shown only via the separate gated evidence action, §2.) |
| **Empty** | No findings on a clean/INCONCLUSIVE document: "No tampering signals from these methods" (LOW) or "These methods could not assess this document" (INCONCLUSIVE) — explicitly NOT "this document is genuine." |
| **Error** | Job failed to run at all (unreadable / unsupported file): plain error + retry, never a verdict. Distinct from a stage that ran and found nothing. |

### API contract the UI consumes

Non-blocking, mirroring project invariant #8 (the API never blocks on inference).

| Method & path | Purpose | Returns |
|---|---|---|
| `POST /v1/documents` | Submit one PDF for review. | `202 {job_id, status_url}` |
| `GET /v1/jobs/{job_id}` | Poll job + per-stage progress; includes the **scrubbed** `AdvisoryInput`-shaped result when done. | `{state, stages:[{stage,state}], result?: <AggregateResult projected to descriptors>}` |
| `GET /v1/jobs/{job_id}/advisory` | Stream the advisory explanation (SSE) as it generates. | `text/event-stream` of advisory chunks; final event is the full `AdvisoryOutput`. |
| `GET /v1/jobs/{job_id}/findings/{finding_id}/evidence` | (Out of scope this slice) gated, audit-logged raw before→after for one finding. | `{before, after}` — authenticated only. |

**Critical:** the result and advisory endpoints serve **descriptors + advisory
prose only** (the `AdvisoryInput`/`AdvisoryOutput` shapes). Raw extracted text
never transits these endpoints — only the gated evidence endpoint, which is not
part of the thin slice.

---

## 5. Interfaces + stubs (item 5)

All modules live under `src/pdf_forgery/aggregate/`. **Data models / enums /
config and the prompt text are real** (they are contracts/content). **Every
logic function is a stub** with a full signature + docstring that raises
`NotImplementedError`. The `safe_log` formatting helper is real (a small safety
primitive, no detection logic). Nothing computes an aggregate or calls a model
this session.

### Module map

| Module | Responsibility |
|---|---|
| `models.py` | `BBox`, `AggregateFinding`, `AggregateResult`, `AdvisoryStage`, `AdvisoryFinding`, `AdvisoryInput`, `FindingRationale`, `AdvisoryOutput`, `ADVISORY_FINDING_ALLOWLIST` — all real (contracts). |
| `config.py` | `AggregateConfig` — wraps `FusionConfig`, advisory toggle/engine name, allow-list, score bands. Nothing magic hard-coded outside it. |
| `aggregate.py` | `aggregate(results, config) -> AggregateResult` + `_flatten_findings` + per-stage `_finding_bbox` / `_finding_type`. **Stubs.** |
| `phi_scrub.py` | `to_advisory_input(aggregate, config) -> AdvisoryInput` + `assert_advisory_safe(...)`. The trust boundary. **Stubs.** |
| `prompts.py` | `SYSTEM_PROMPT`, `USER_PROMPT_TEMPLATE` (**real**) + `build_advisory_messages(advisory_input, config) -> list[Message]` (**stub**). |
| `advisory.py` | `Message`, `AdvisoryEngine` Protocol, `StubAdvisoryEngine` (deterministic, no GPU — the 6.1 default), `LocalLLMAdvisoryEngine` (GPU, swappable), `generate_advisory(advisory_input, engine, config)`. **Stubs.** |
| `api.py` | HTTP contract dataclasses (`JobSubmission`, `JobStatus`, `StageProgress`, `JobResult`, `AdvisoryEvent`) + stub handlers. No web framework imported (kept dependency-free this slice). **Stubs.** |
| `safe_log.py` | `finding_log_record(finding)` (allow-listed dict) + `salted_hash(value, salt)` — **real**, PHI-safe. |
| `__init__.py` | Facade re-exports. |

### `AdvisoryEngine` — pluggable, like `OCREngine`

```python
@runtime_checkable
class AdvisoryEngine(Protocol):
    name: str
    def generate(self, messages: list[Message]) -> AdvisoryOutput: ...
    def is_available(self) -> bool: ...
```

- `StubAdvisoryEngine` — deterministic, templated from the descriptors, **no
  GPU, no network**. This is the default so the pipe runs end-to-end on any
  machine (the 6.1 implementation target).
- `LocalLLMAdvisoryEngine` — wraps a local GPU model. **GPU contention with
  PaddleOCR only under concurrent load**; in the normal sequential pipeline OCR
  has released the GPU before advisory runs. When the model/GPU is absent,
  `is_available()` is `False` and `generate_advisory` degrades to the stub /
  templated fallback (never raises) — matching the project-wide "report and
  continue" and Stage 3's `OCREngine` graceful-absence pattern.
- **Never attempt to download model weights in the sandbox** (project
  invariant). The real-weights path is documented, not executed.

### PHI-safe logging

All structured logging in the layer goes through `safe_log.finding_log_record`,
which emits ONLY the allow-listed descriptor fields (`finding_id`, `stage`,
`type`, `tier`, `score`, `token_class`, `page`, `bbox`) — never `before` /
`after` / `reason` / payload. A `salted_hash` helper is provided for the rare
case a token reference must be correlated across logs without exposing it.

---

## 6. Acceptance for this design session

- [x] Item 1 — `AggregateResult` schema specified: headline tier/score, per-stage
      results, flat findings with `finding_id` + descriptor set + **`bbox`
      carried now** (canonical normalized space); minimal fusion delegated to
      `fusion.fuse()`, full fusion deferred and config-exposed.
- [x] Item 2 — PHI-scrub boundary as an explicit **allow-list** (`AdvisoryInput`);
      forbidden set enumerated; raw before→after relocated to a gated evidence
      endpoint out of scope this slice.
- [x] Item 3 — advisory system + user prompt (grounded, cite ids, never re-judge,
      INCONCLUSIVE honesty) + `AdvisoryOutput` shape.
- [x] Item 4 — UI states (upload / processing / result / empty / error) + API
      contract (upload→job, status/result, advisory stream); decision-support copy.
- [x] Item 5 — stubs + interfaces: data models / config / prompt text real; every
      logic function a `NotImplementedError` stub; `AdvisoryEngine` pluggable; GPU
      contention noted; PHI-safe logging via `safe_log`.
- [x] Stubs compile; **no logic implemented**; the aggregate layer is NOT yet run
      after the pipeline (wiring is a 6.2 task).
- [x] `docs/TODO.md` updated with the 6.1 / 6.2 breakdown.
</content>
</invoke>
