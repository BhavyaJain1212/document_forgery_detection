# Stage 7 — Reviewer-UI + Advisory Fixes (implementation plan)

**Status: PLAN ONLY — not yet implemented.** Hand this to an implementer
session. Three reviewer-reported problems against the live UI
(`python -m pdf_forgery.aggregate.server`):

1. No way to go back / re-upload a different document from the result screen.
2. The result screen never shows which file is being reviewed.
3. The advisory ("LLM analysis") is near-useless: it repeats the same
   templated sentence per finding (15× for `ocr_crosscheck`) and explains
   nothing a human can act on.

This plan stays inside the existing thin-slice architecture (in-memory
`JobManager`, descriptor-only PHI boundary, swappable `AdvisoryEngine`). It does
**not** touch fusion math, stage detectors, or the PHI allow-list semantics. All
copy stays decision-support, never an absolute verdict (design §3/§4).

Read first: `docs/STAGE7_DESIGN.md` (the contract) and
`src/pdf_forgery/aggregate/CLAUDE.md` (session history). Run the suite with
`./.venv/bin/python -m pytest --ignore=tests/test_microsoft_pdf.py`.

---

## Problem 1 — Back / re-upload button

### Root cause
`webapp/app.js` has views `upload → processing → result → error`. Only the
**error** view has a "Try another file" button (`renderError`, lines ~367–381).
`renderResult` (lines ~196–235) renders the hero + advisory + breakdown with no
escape hatch, so once a verdict shows the only way back is a full page reload.

### Design
Add a persistent **"Review another document"** action and reset cleanly. Two
complementary touches; do both:

1. **A header action** (always visible once you've left the upload screen).
   The header (`index.html` lines 11–29) currently shows `brand` +
   `.header-tag`. Keep the tag; this is a result-screen affordance, so prefer
   placing the button in the result view to avoid header clutter — but a small
   header "New review" `.btn--ghost` is acceptable if it's hidden on the upload
   view. **Recommended: result-view button**, simpler and matches the error
   view's existing pattern.

2. **A button at the top of the result view.** In `renderResult`, prepend a
   thin action row above the hero:
   ```html
   <div class="result__actions">
     <button class="btn btn--ghost" type="button" id="new-review-btn">
       <!-- reuse the upload arrow icon or a left-chevron from the I.* set -->
       Review another document
     </button>
   </div>
   ```
   Bind it in `renderResult` (next to `bindAccordions()`):
   ```js
   $("new-review-btn").addEventListener("click", goToUpload);
   ```
   Add one shared helper (reused by the error view's retry too):
   ```js
   function goToUpload() {
     reset();                 // clears pollTimer + closes advisorySource
     $("file-input").value = ""; // so re-selecting the SAME file re-fires change
     currentFilename = "";    // see Problem 2
     showView("upload");
   }
   ```
   Repoint the existing `#retry-btn` handler in `renderError` to call
   `goToUpload()` instead of inlining the same three lines.

### Styling (theme-consistent — do NOT invent new tokens)
- Reuse the existing `.btn .btn--ghost` classes (`styles.css` 308–335): surface
  background, `--border-strong`, hover `--surface-2`. This is the exact button
  the error view already uses, so it's automatically on-theme.
- Add a tiny `.result__actions` rule near `.result` (`styles.css` ~496): margin
  below it (`margin-bottom: var(--s-5)`), left-aligned. Use existing spacing
  tokens only (`--s-*`).
- Icon: reuse an inline icon from the `I.*` map in `app.js` (e.g. a left chevron
  — you can add one `I.arrowLeft` following the same stroke/`currentColor`
  pattern as the others, lines 9–19). No emoji (house rule).

### Files
- `webapp/app.js` — add `goToUpload`, the action row in `renderResult`, the
  bind, repoint `#retry-btn`. Optionally one new `I.arrowLeft` icon.
- `webapp/styles.css` — one small `.result__actions` rule.
- `webapp/index.html` — only if you choose the header-button variant.

### Acceptance
- From a HIGH/MEDIUM/LOW/INCONCLUSIVE result, the button returns to the upload
  screen; the in-flight advisory SSE and poll timer are closed (verify no
  console errors, no leaked `EventSource`).
- Re-selecting the **same** file immediately re-runs (the `file-input.value`
  reset is what makes the `change` event fire again).

---

## Problem 2 — Show the uploaded document's name on the result screen

### Root cause
The filename is shown only during **processing** (`renderProcessing(file.name)`,
app.js 119–129). `renderResult` never receives or renders it. Server-side the
`Job` stores `filename` (jobs.py 76) but `JobStatus`/`_advisory_input_dict`
never return it, so after analysis the reviewer has lost track of which document
this is.

### Design — keep it client-side (no API/PHI-boundary change)
The browser already has the filename at upload time. Store it in app state and
render it in the result hero. **Do not** route the filename through the advisory
input or the `/v1/jobs/{id}` result payload — a filename can contain a patient
name (PHI), and the PHI boundary (design §2) is about descriptors only. Keeping
it purely client-side sidesteps that entirely and is simpler.

1. Add module state near `let pollTimer` (app.js ~55):
   ```js
   let currentFilename = "";
   ```
2. In `submit(file)` (app.js ~94), set it before rendering:
   ```js
   currentFilename = file.name;
   ```
   (Also cleared in `goToUpload`, see Problem 1.)
3. In `renderResult`, show it in the hero band. Add a filename element to the
   `hero__band` (app.js ~205) — e.g. a `.hero__file mono` span next to the
   findings chip, or a dedicated row just under `.hero__band`:
   ```html
   <div class="hero__doc">
     <!-- small document icon (add I.doc, same stroke pattern) -->
     <span class="hero__file mono">${esc(currentFilename || "document.pdf")}</span>
   </div>
   ```
   `esc()` already guards against HTML injection from the filename. Use `mono`
   (the tabular class already used for ids/scores) for the filename.

### Styling
- New `.hero__doc` / `.hero__file` rule near the hero styles (`styles.css`
  ~519). Muted color (`--ink-2`/`--ink-3`), `--fs-sm`, truncate long names with
  `text-overflow: ellipsis; overflow: hidden; white-space: nowrap; max-width`.
  Use only existing tokens.

### (Optional, only if persistence-across-reload is wanted later)
If a reload-survivable filename is ever required, add `filename` to `JobStatus`
and `_advisory_input_dict`'s sibling in `get_job` — but treat it as PHI in logs
(`safe_log` must never receive it). Out of scope unless the reviewer asks; the
client-side approach above is the recommended fix.

### Files
- `webapp/app.js` — `currentFilename` state + set in `submit` + render in hero.
- `webapp/styles.css` — `.hero__doc`/`.hero__file` rule.

### Acceptance
- After uploading `claim_xyz.pdf`, the result hero shows `claim_xyz.pdf`.
- The filename never appears in `/v1/jobs/{id}` JSON, the advisory SSE, or logs.

---

## Problem 3 — The advisory is repetitive and explains nothing (the real fix)

### Root cause (two independent causes — fix both)

**(a) The reviewer is seeing the STUB, not an LLM.** The pasted output
> `(ocr_crosscheck-0) ocr_crosscheck flagged a embedded_only finding at HIGH confidence, involving a id field — a reviewer should verify it.`

is produced **verbatim** by `_render_advisory` in `advisory.py` (lines 286–301).
The default `AggregateConfig.advisory_engine` is `"stub"` (config.py 28), and
`server.py`'s `main()` never calls `configure_manager`, so the live server runs
the `StubAdvisoryEngine`. No model is ever consulted.

**(b) The architecture forces one rationale per finding.** Both the stub and the
prompt (`prompts.py` `USER_PROMPT_TEMPLATE`, output schema in `SYSTEM_PROMPT`)
emit one `finding_rationale` per `finding_id`. `ocr_crosscheck` can emit 15
near-identical findings (same `type`, same `token_class="id"`, consecutive
pages), so the reviewer gets 15 near-identical sentences. Even a perfect LLM,
handed 15 individual descriptor rows and asked for one rationale each, will
repeat itself. **The fix is to GROUP before explaining**, and to explain each
distinct group/type once, with meaning + why-it-matters + what-to-check.

### Design overview
Three coordinated changes, in dependency order:

1. **Group findings** (new, deterministic, PHI-safe) → feed the advisory groups
   instead of raw findings.
2. **Rewrite the prompt + output schema** to be group-based and explanatory
   (plain-language meaning of each finding type, no repetition), and **rewrite
   the stub** to match (grouped, not 15×).
3. **Turn the LLM on** for the server (Ollama via the existing
   `LocalLLMAdvisoryEngine`), with the already-implemented graceful fallback to
   the improved stub. Render groups in the UI.

The PHI boundary is unchanged: grouping operates only over already-scrubbed
`AdvisoryFinding` descriptors (`stage`, `type`, `tier`, `token_class`, `page`,
`finding_id`) — never raw text.

---

### 3.1 Finding grouping (deterministic, in the advisory layer)

Add grouping that collapses repetitive findings. Group key:
`(stage, type, token_class)`. For each group compute:
- `stage`, `type`, `token_class`
- `count` (number of findings)
- `pages` — sorted unique 0-based pages (render as 1-based in UI)
- `tier` — the **max** tier across the group (escalation, never averaging —
  mirrors the project's worst-case rollup rule)
- `finding_ids` — the member ids (so the UI can still key/overlay each one and
  the LLM can cite the group by its representative id)

Where to put it: a new pure helper, e.g. `advisory.py::_group_findings(
advisory_input) -> list[FindingGroup]` (or a small `grouping.py` module). Add a
frozen `FindingGroup` dataclass to `models.py`. It is derived from
`AdvisoryFinding`s only, so it inherits PHI-safety; `assert_advisory_safe`
already covers the underlying findings.

**Type glossary (the key to "explain, don't restate").** Add a frozen mapping
of `type → plain-language meaning + reviewer implication`, e.g.:
```python
FINDING_TYPE_GLOSSARY = {
  "embedded_only": "Text exists in the PDF's text layer but was NOT found in the rendered page image — it may be hidden, white-on-white, or covered by an overlay.",
  "ocr_only":      "Text is visible in the rendered page but is MISSING from the PDF text layer — typical of an image patch pasted over the original (the edit isn't in the real text).",
  "mismatch":      "The PDF text layer and the rendered image disagree on the same region — the visible text was changed without updating the underlying text.",
  # revision_recovery: content_edit / field_edit ...
  # font_forensics, invoice_arithmetic, provenance_metadata: fill from docs/FORGERY_METHODS.md
}
```
Populate the literals from `docs/FORGERY_METHODS.md` (the frozen canonical
strings — do not paraphrase the *keys*; the human-readable *values* are new
explanatory copy and are yours to write well). Keep this in `prompts.py` or a
new `glossary.py`. It feeds BOTH the LLM prompt (so the model has the meaning)
AND the stub fallback (so it can explain without a model).

---

### 3.2 New advisory output schema + prompt (group-based, explanatory)

**Schema change (`models.py`).** Replace per-finding rationales with per-group
explanations. Add:
```python
@dataclass(frozen=True)
class GroupExplanation:
    finding_ids: tuple[str, ...]   # members this explanation covers (for UI mapping + citation)
    label: str                     # short human label, e.g. "Text/image mismatch (id fields, 5×, pages 1–2)"
    what_we_found: str             # plain language, grounded in descriptors
    why_it_matters: str            # what this could indicate (decision-support, not a verdict)
    what_to_check: str             # concrete next action for the reviewer
```
Add `group_explanations: tuple[GroupExplanation, ...]` to `AdvisoryOutput`.
**Keep `finding_rationales` for backward compat** OR remove it and update all
readers — recommended: **keep the field but deprecate it** (default `()`), and
have the UI prefer `group_explanations`. Update `__all__` and the serializers.

**`SYSTEM_PROMPT` rewrite (`prompts.py` 23–56).** Keep all five strict rules
(ground-only-in-findings, no PHI/values, no re-judging the verdict,
decision-support phrasing, concise). Change the JSON schema it requests to the
grouped shape, and ADD instructions:
- "Findings are pre-grouped. Write ONE explanation per group. Do NOT repeat the
  same sentence for similar findings."
- "For each group, write three short parts: what the detector found (plain
  language), why it could matter for a forgery review, and what the reviewer
  should check next."
- "A glossary of finding types is provided — use it to explain what each type
  means; do not just restate the type name."
- "The summary must synthesize across groups into a 2–4 sentence narrative, not
  a count of findings."

Inject the glossary (only for the types actually present) and the grouped
findings into the user prompt. **`USER_PROMPT_TEMPLATE` rewrite** to send groups
(stage, type, glossary line, count, pages, tier, representative finding_id +
member ids) instead of the raw findings JSON array.

> Note: `advisory.py::_parse_user_message` (310–327) round-trips the user prompt
> back into structured data for the stub. If you change the template, update
> this parser **or** (cleaner) refactor so the stub builds from the structured
> `AdvisoryInput`/groups directly instead of re-parsing prompt text. **Prefer
> the refactor** — `_fallback_output` already has the structured object in hand
> (advisory.py 226–241); make `StubAdvisoryEngine.generate` do the same so there
> is one grouped templating path and no fragile prompt re-parsing.

**Stub/fallback rewrite (`advisory.py` `_render_advisory`).** Make it group-aware
and explanatory using `FINDING_TYPE_GLOSSARY`:
- `summary`: synthesize across groups ("Two detectors flagged this document:
  text-vs-image checks found id fields visible in the image but absent from the
  text layer across pages 1–2, and …"). Name the tier honestly.
- one `GroupExplanation` per group: `what_we_found` from count+pages+glossary;
  `why_it_matters` from the glossary implication; `what_to_check` a concrete,
  generic-but-useful action (e.g. "Compare the id field on pages 1–2 against the
  source/registry; confirm it wasn't pasted over.").
- Even with NO model available, this alone fixes the "15 identical sentences"
  complaint.

**`generate_advisory` validation (advisory.py 187–192).** Update the cited-id
check: every `finding_id` inside every group's `finding_ids` must exist in the
input (the model may not invent ids). Keep the degrade-to-fallback-on-anything
behavior.

---

### 3.3 Turn the LLM on (Ollama) with graceful fallback

`LocalLLMAdvisoryEngine` is already a real Ollama wrap (advisory.py 82–157):
`GET /api/tags` gates availability, `POST /api/chat` with `format:json`. It just
isn't selected. To enable it on the server:

- In `server.py::main` (or via an env var read into `AggregateConfig`), call
  `api.configure_manager(AggregateConfig(advisory_engine="local_llm",
  advisory_model="<tag>"))`. Recommend reading from env so no model is hard-wired:
  `advisory_engine = os.getenv("FDP_ADVISORY_ENGINE", "stub")`,
  `advisory_model = os.getenv("FDP_ADVISORY_MODEL", "llama3.1")`.
- **Do NOT pull/download weights in the sandbox** (project invariant). Document
  in the plan/README that the operator runs e.g. `ollama pull llama3.1` out of
  band; if absent, `is_available()` is False and the layer falls back to the
  improved stub automatically (advisory.py 178–179) — never crashes.
- Model choice: the GPU note (design §0/§5) says an 8B model alongside Stage 3's
  PaddleOCR is tight on 8 GB. In the sequential pipeline OCR has finished before
  advisory runs, so an 8B (e.g. `llama3.1:8b`) is fine; recommend a small
  instruct model and keep `temperature 0.0` (already set, advisory.py 132).
- The SSE path (`api.py::_advisory_events`) currently chunks only
  `output.summary`. That's fine — the streamed text is the narrative summary;
  the grouped explanations land in the final `done` event. Confirm the UI renders
  groups from the `done` payload (3.4).

---

### 3.4 Render groups in the UI (instead of 15 finding rows)

Currently `stageCard` (app.js 243–273) lists every finding via `findingRow`
(275–288), and `finishAdvisory` (334–358) fills one rationale per
`data-finding-id`. Change to group rendering:

- In `stageCard`, group that stage's findings by `(type, token_class)` (same key
  as 3.1, client-side mirror) and render ONE row per group: the humanized type,
  a `token_class` chip, a **count** ("5×"), and a **page range** ("pages 1–2")
  instead of 15 separate `page N` chips. Keep `finding_id`s as a `data-` attr or
  a small expandable list for the overlay/evidence future.
- In `finishAdvisory`, consume `group_explanations`: for each group, render
  `what_we_found` / `why_it_matters` / `what_to_check` (three labeled lines or a
  small definition list) into the matching group row, matched by the group's
  member `finding_ids`. Drop the per-finding "Reviewing this finding…" /
  "Flagged for reviewer attention." placeholders (or keep a single neutral
  group-level placeholder).
- The summary + `tier_statement` rendering (app.js 334–347) stays; it now shows
  a real narrative.

### Styling
- Reuse existing chips (`.chip`, `.chip--type`, `.chip--token`, `.chip--page`)
  and the `.finding` / `.finding__rationale` styles. Add a `.chip--count` (e.g.
  "5×") reusing chip tokens. For the three explanation lines, a small
  `.expl` / `.expl__label` rule (label in `--ink-3` `--fs-xs`, body in `--ink`).
  Tokens only; no new color outside the tier palette.

---

### Files (Problem 3)
- `models.py` — `FindingGroup` (if exposed) + `GroupExplanation` +
  `AdvisoryOutput.group_explanations`; update `__all__`.
- `prompts.py` — new system prompt (grouped + explanatory schema), new user
  template (grouped + glossary), glossary (or new `glossary.py`).
- `advisory.py` — `_group_findings`; grouped, glossary-driven `_render_advisory`
  / `_fallback_output`; refactor stub to build from the structured object (drop
  prompt re-parsing); updated cited-id validation.
- `config.py` — (optional) nothing required; engine already configurable.
- `server.py` — select `local_llm` via env into `AggregateConfig` +
  `configure_manager`.
- `api.py` / `server.py` serializers — serialize `group_explanations` in the
  advisory `done` event (`_advisory_output_dict`, server.py 82–91).
- `webapp/app.js` + `styles.css` — group rendering + explanation lines + count
  chip.

### Acceptance (Problem 3)
- A document that produced 15 `ocr_crosscheck` findings now shows **one grouped
  explanation per `(type, token_class)`** (e.g. 2–3 groups), each with a plain
  meaning, why-it-matters, and what-to-check — no sentence repeated.
- With Ollama running and the model pulled, the advisory is model-generated
  (the `model` field shows `local_llm:<tag>`), grounded, cites only real ids,
  and never re-judges the tier; with Ollama absent, the improved **stub**
  produces the same grouped structure (no crash, `model` shows
  `local_llm (unavailable)` or `stub`).
- PHI boundary intact: no raw text/values in any served payload; the
  `assert_advisory_safe` test still passes.

---

## Testing & verification

- Update `tests/test_aggregate.py` (advisory output shape, grouping, glossary
  use, cited-id-subset over groups, PHI-leak assertion still raises) and
  `tests/test_web.py` (served advisory `done` event carries
  `group_explanations`, still no before/after, SSE chunk+done).
- Add a grouping unit test: N near-identical `ocr_crosscheck` findings → 1 group
  with `count=N`, correct page range, max tier.
- Keep the whole suite green:
  `./.venv/bin/python -m pytest --ignore=tests/test_microsoft_pdf.py`
  (baseline today: 726 passed, 1 skipped).
- Visual check: run `./.venv/bin/python -m pdf_forgery.aggregate.server`, upload
  a real PDF, confirm (1) the back button returns to upload, (2) the filename
  shows on the result, (3) the advisory is grouped + readable. Do NOT download
  model weights in the sandbox — verify the stub path; note Ollama as an
  operator step.

## Out of scope (do not start)
- Full fusion (cross-stage geometric correlation, dedup, calibration).
- The `bbox` overlay view (still `None` from every stage — unchanged here).
- The gated raw-evidence endpoint.
- Durable jobs (Celery/Redis/Postgres). The in-memory `JobManager` stays.
