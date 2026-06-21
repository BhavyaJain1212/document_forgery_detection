# Stage 7 — Advisory readability upgrade (implementation brief for Sonnet)

**Audience:** the engineer implementing this (Sonnet).
**Goal:** make the reviewer-facing advisory genuinely readable — fix the run-on
summary paragraph and eliminate the meaningless "An anomaly was detected by this
detector" cards. Owner has explicitly accepted some extra latency if it buys
better explanations (i.e. using the real LLM is on the table).

This brief is grounded in the current code. File/line references were accurate
at the time of writing; re-read before editing. **Do not break the PHI boundary**
(`AdvisoryInput` / `ADVISORY_FINDING_ALLOWLIST`) — none of these changes require
sending document content to the model or the frontend.

---

## 0. TL;DR of what's wrong and the fix

There are **two** reviewer complaints, and they have **three** root causes.

| # | Symptom (reviewer sees) | Root cause | Fix |
|---|---|---|---|
| 1 | Summary is one long `;`-separated run-on sentence | `_render_advisory` builds the summary as a single joined string | Emit the summary as **Markdown** (headline + bullet-per-group), add a tiny Markdown→HTML renderer in `app.js` |
| 2a | Cards say "An anomaly was detected by this detector" / "Review the cited finding in context…" | **Glossary key mismatch** — most type tokens emitted by stages are NOT keys in `FINDING_TYPE_GLOSSARY`, so they fall through to `_GENERIC` | **Complete the glossary** so every emitted token has a real entry (deterministic floor; also feeds the LLM prompt) |
| 2b | Even non-generic cards are templated, not tailored | Default engine is the `stub`; the LLM never runs | Optionally enable the **Ollama `local_llm`** engine; it now grounds on the completed glossary and writes per-group prose |

**Priority order:** 2a first (biggest, cheapest, deterministic win — kills every
generic card with zero latency), then 1 (formatting), then 2b (LLM polish).

The owner's "perfect card" example (the `ocr_only` text/image-patch card) is
**already what the stub produces** — because `ocr_only` happens to have a good
glossary entry. The job is to bring *every* finding type up to that same bar.

---

## 1. Where everything lives (orientation)

The advisory layer is `src/pdf_forgery/aggregate/`:

- `glossary.py` — `FINDING_TYPE_GLOSSARY: dict[str, (meaning, reviewer_implication)]`
  and `_GENERIC` fallback. **This is the source of the card text.**
- `advisory.py` — `_render_advisory()` builds both the **summary** string and the
  per-group `GroupExplanation`s (`what_we_found` / `why_it_matters` /
  `what_to_check`). `_what_to_check()` is keyed by **stage**. The stub and the
  LLM-fallback share this one templating path.
- `prompts.py` — `SYSTEM_PROMPT` + `build_advisory_messages()`. The LLM is handed
  the same glossary, so **fixing the glossary also fixes the LLM's grounding.**
- `aggregate.py::_finding_type()` — derives the `type` token per finding from each
  stage's rich payload enum. **This is the token that must match a glossary key.**
- `models.py` — `GroupExplanation`, `AdvisoryOutput`, the PHI allow-list. Pure data.
- `config.py` / `server.py` — `advisory_engine` defaults to `"stub"`;
  `FDP_ADVISORY_ENGINE=local_llm` switches to Ollama.
- `webapp/app.js` — `finishAdvisory()` renders the summary (`textContent`) and the
  three expl-cards (`Found` / `Why it matters` / `Check`). **Markdown rendering
  goes here.**

Rendering flow on the frontend: the summary streams in word-by-word as plain
escaped text (SSE `chunk`), then `done` calls `finishAdvisory(out)` which sets the
final `summary`, `tier_statement`, and fills each card from `group_explanations`.

---

## 2. PROBLEM 2a (do this first) — complete the glossary

### Why the cards are generic

`aggregate.py::_finding_type()` returns the **raw enum `.value`** from each stage's
payload. Many of those values are **not keys** in `FINDING_TYPE_GLOSSARY`, so
`get_glossary_entry()` returns `_GENERIC` = `("An anomaly was detected by this
detector.", "Review the cited finding in context with the other findings.")` —
exactly the dead text the owner pasted.

Worse: several **current glossary keys are dead** — they don't match any emitted
token (`inter_token_font_mix`, `line_outlier`, `edit_tool_detected`,
`modification_after_signing`, and `broken_relationship` is unreachable via this
path because invoice findings are typed by `RelationshipKind`, not
`ArithmeticFindingKind`).

### Exhaustive token → glossary audit

These are the **actual tokens** `_finding_type()` can emit (verified against each
stage's enums). ✅ = already has a correct glossary entry; ❌ = missing → falls to
`_GENERIC` today.

**ocr_crosscheck** (`DivergenceType`, `agree` is filtered out):
- `mismatch` ✅ · `embedded_only` ✅ · `ocr_only` ✅  → all good (this is why the
  owner's example card is great).

**revision_recovery** (`_OBJECT_CLASS_TYPE` in `aggregate.py`):
- `content_edit` ✅ · `overlay` ✅ · `field_edit` ✅ · `form_fill` ✅ ·
  `signature_change` ✅ · `metadata_change` ✅ · **`markup` ❌**

**font_forensics** (`FontFindingKind`):
- `intra_token_font_mix` ✅
- **`whole_token_subset_difference` ❌** · **`whole_token_family_difference` ❌** ·
  **`page_baseline_deviation` ❌** · **`document_baseline_deviation` ❌** ·
  **`intra_line_subset_split` ❌**
- (the existing `inter_token_font_mix` / `line_outlier` keys are **dead** — remove
  or repurpose)

**invoice_arithmetic** (`RelationshipKind` — every one is missing today, which is
why the owner's "Line item in amount fields" card was generic):
- **`line_item` ❌** (qty × rate = amount) · **`subtotal_sum` ❌** ·
  **`grand_total` ❌** · **`gst_sum` ❌** · **`deposit_balance` ❌** ·
  **`room_charge` ❌** · **`date_span` ❌**
- (the existing `broken_relationship` key is **dead** via this path)

**provenance_metadata** (`ProvenanceFindingKind` — all missing; this is the other
batch of generic cards the owner saw: "Version only producer", "Browser creator",
"Moddate after creation", "Composite edit footprint"):
- **`web_editor_producer` ❌** · **`version_only_producer` ❌** ·
  **`browser_creator` ❌** · **`moddate_after_creation` ❌** ·
  **`xmp_info_producer_mismatch` ❌** · **`id_halves_mismatch` ❌** ·
  **`composite_edit_footprint` ❌**
- (existing `edit_tool_detected` / `modification_after_signing` keys are **dead**;
  `provenance_anomaly` only reachable via `_finding_type`'s fallback)

### Task 2a.1 — add a glossary entry for every ❌ token

Author one `(meaning, reviewer_implication)` tuple per missing token, **to the same
quality bar as the `ocr_only` entry** the owner praised:
- `meaning` = a plain-language, reviewer-readable sentence describing *what the
  detector found* (no jargon, no enum names).
- `reviewer_implication` = *why it could matter* for a forgery review, in
  decision-support tone ("…suggesting…", "…a reviewer should check…").

**Source of truth for what each token means** (read these, don't guess):
- font: `src/pdf_forgery/font_forensics/models.py::FontFindingKind` — each member
  has a docstring (e.g. `whole_token_subset_difference` = "a uniformly-rendered
  token uses another subset of the line's base face; supporting evidence only").
- invoice: `src/pdf_forgery/invoice_arithmetic/models.py::RelationshipKind` — each
  has an inline formula comment (e.g. `line_item` = `qty * rate = amount`).
- provenance: `src/pdf_forgery/provenance_metadata/models.py::ProvenanceFindingKind`
  — each member has a docstring.
- revision `markup`: a comment/highlight/note annotation (vs `overlay` = stamp/
  redaction over text). See `revision_recovery` object-class docs.

Keep `_GENERIC` as the final safety net, but after this task **no real token should
ever reach it.**

### Task 2a.2 — add a regression test that closes the gap permanently

Add a test (e.g. in `tests/test_aggregate.py`) that **enumerates every enum value**
each stage can feed into `_finding_type` and asserts
`get_glossary_entry(token) is not _GENERIC`. Import the enums directly
(`FontFindingKind`, `RelationshipKind`, `ProvenanceFindingKind`,
`DivergenceType` minus `agree`, plus the `_OBJECT_CLASS_TYPE` tokens) so any new
enum member added later fails this test until its glossary entry exists. This is
the durable fix — it prevents the gap from silently reopening.

### Task 2a.3 — make `_what_to_check` type-aware (smaller win)

`advisory.py::_what_to_check()` is keyed only by **stage**, so every invoice card
says the same "Re-verify the arithmetic…" line. That's acceptable (not generic),
but for parity with the owner's example, consider keying the most common types
(e.g. `line_item` vs `grand_total`, `moddate_after_creation` vs
`web_editor_producer`) to a more specific "Check" sentence. Optional; do 2a.1
first.

---

## 3. PROBLEM 1 — readable summary (Markdown, not a `;` run-on)

The owner's read of the current summary is correct: it's a list of
*problem : where it occurred* flattened into one sentence. Render it as a list.

### Approach: advisory emits Markdown, frontend renders it

This mirrors what the owner already did successfully on their chatbot (model
replies in Markdown; a small regex converts `**bold**`/newlines to formatted
output). In a browser the natural target is HTML, not "txt".

### Task 1.1 — restructure the stub summary in `advisory.py::_render_advisory`

Build `summary` as Markdown instead of the current `'; '.join(...)` run-on. Target
shape (a one-line headline + the tier statement + one bullet per group, where each
bullet is **bold problem name** — *where it occurred*):

```
Across **font_forensics**, **invoice_arithmetic**, **provenance_metadata** and
**ocr_crosscheck**, these methods flagged 7 finding groups:

- **Intra-token font mix** — page 1
- **Line-item arithmetic break** (amount fields) — page 1 (2×)
- **Version-only producer**
- **Browser creator**
- **Modification after creation**
- **Composite edit footprint**
- **OCR-only text** (id fields) — page 1

Overall confidence is **HIGH (score 80)** — these methods found strong evidence of
an edit; a reviewer should confirm.
```

Notes:
- Keep the existing grouping logic (`_group_findings`, `_humanize_type`,
  `_format_pages`, the `count`/`token_class` formatting) — just change the
  *assembly* from a joined sentence to a bulleted Markdown block.
- Keep the `tier_statement` field as-is (plain text is fine; it's shown
  separately in `#adv-tier`).
- The empty-findings and INCONCLUSIVE branches must stay honest — don't let
  Markdown formatting drop the "INCONCLUSIVE is not the same as clean" disclaimer.

### Task 1.2 — Markdown→HTML renderer in `webapp/app.js`

Add a small, **escape-first** renderer (XSS-safe: escape the text, *then* apply a
whitelist of inline rules). Support only what we emit: `**bold**`, `- ` bullet
lines, and blank-line paragraph breaks. Example contract:

```js
// Escape first (reuse esc), then apply a tiny whitelist of Markdown rules.
// Supports: **bold**, "- " bullets -> <ul><li>, blank line -> paragraph break.
function renderMarkdown(src) { /* returns safe HTML string */ }
```

Then in `finishAdvisory()` (currently `app.js:384`) replace
`$("adv-summary").textContent = out.summary` with
`$("adv-summary").innerHTML = renderMarkdown(out.summary)`.

**Streaming caveat:** during SSE streaming (`connectAdvisory`, `app.js:359`) the
text arrives token-by-token and is shown as plain escaped text with a typing
caret. Leave the streaming path as plain text (rendering half-formed Markdown mid-
stream looks broken); only render Markdown on the `done` event in
`finishAdvisory()`. The "verdict now, prose-types-in, formats-on-finish" feel is
fine.

### Task 1.3 — (optional) allow Markdown in card bodies too

If you want bold inside the `Found` / `Why it matters` / `Check` card bodies,
route `g.what_we_found` etc. through the same `renderMarkdown` in `finishAdvisory`
(the loop at `app.js:399`). Not required — the cards are already visually
structured; the main readability win is the summary. If you do this, the glossary
text can use `**…**` to emphasize the key phrase.

Add CSS for `.advisory__body ul`/`li`/`strong` in `webapp/styles.css` so the
bullet list has proper spacing (match the existing design tokens).

---

## 4. PROBLEM 2b (optional polish) — let the LLM write the prose

With the glossary completed (Problem 2a), the **stub already produces meaningful,
non-generic cards** for free with zero latency. Problem 2b is about going beyond
templated text to *tailored* prose, which the owner has green-lit latency for.

### How to turn it on

The Ollama-backed `LocalLLMAdvisoryEngine` is **already implemented** in
`advisory.py` (it wraps `GET /api/tags` + `POST /api/chat` with `format:json`).
Enable it by running the server with:

```
FDP_ADVISORY_ENGINE=local_llm FDP_ADVISORY_MODEL=llama3.1 \
  ./.venv/bin/python -m pdf_forgery.aggregate.server
```

It degrades gracefully to the stub templating if Ollama isn't reachable or the
model tag isn't pulled (`is_available()` → False), so nothing breaks when it's
off. **Do not pull model weights in the sandbox** — that's an out-of-band step on
the owner's machine.

### What to verify / adjust for 2b

1. **The prompt already asks for per-group `what_we_found`/`why_it_matters`/
   `what_to_check` + a synthesized summary** (`prompts.py::SYSTEM_PROMPT`, rules
   5–8). With the completed glossary now injected (`build_advisory_messages`
   builds the glossary block from `get_glossary_entry`), the model has real
   grounding instead of "An anomaly was detected."
2. **Markdown from the LLM:** if you want the LLM summary bulleted too, add one
   line to the system prompt telling it the `summary` field may use Markdown
   (`**bold**` for each flagged problem, one bullet per group). The
   `renderMarkdown` from Task 1.2 then handles both stub and LLM output uniformly.
3. **Validation already guards safety:** `generate_advisory()` (advisory.py:209)
   checks every cited `finding_id` exists and falls back to the stub on
   malformed/unavailable/erroring responses. Keep that intact.
4. **PHI:** the model only ever sees descriptors (`AdvisoryInput`). Do **not** add
   raw text to the prompt to "help" it — that breaks the boundary and the
   `assert_advisory_safe` contract.

---

## 5. Suggested order of work & test checklist

1. **2a.1** complete the glossary (every ❌ token) — biggest visible win.
2. **2a.2** add the enum-coverage regression test (locks the gap shut).
3. **1.1 + 1.2** Markdown summary + `renderMarkdown` in `app.js` (+ 1.3/CSS).
4. **2a.3** type-aware `_what_to_check` (optional).
5. **2b** enable/verify the Ollama engine + Markdown-in-summary prompt line (optional).

Run the suite (note the existing ignore):

```
./.venv/bin/python -m pytest --ignore=tests/test_microsoft_pdf.py
```

Existing baseline before your change: **726 passed, 1 skipped** (the skip is the
unrelated pristine-invoice precision baseline). Targeted files:
`tests/test_aggregate.py`, `tests/test_web.py`.

Manual check: run the server, upload a known-positive (e.g. the tampered invoice
under `test_pdf's/`), and confirm (a) the summary renders as a bulleted list with
bold problem names, and (b) **no card** shows "An anomaly was detected by this
detector" — every card has a real `Found` / `Why it matters` / `Check`.

---

## 6. Hard constraints (don't regress these)

- **PHI boundary:** only `AdvisoryInput` descriptors cross to the LLM/frontend.
  No raw before/after text, names, amounts, or dates. `assert_advisory_safe` and
  the `test_web.py` boundary test must stay green.
- **Never re-judge the verdict** in advisory copy — tier/score come from
  `fusion.fuse()`; the advisory only *explains*. Keep INCONCLUSIVE honesty
  ("not the same as clean").
- **Graceful degradation:** advisory generation must never raise; unavailable LLM
  → stub fallback. `generate_advisory` already enforces this.
- **No web fonts / no network** in `webapp/` — keep the local system-font stack;
  `renderMarkdown` must be dependency-free vanilla JS.
- **Escape before formatting** in `renderMarkdown` — never `innerHTML` un-escaped
  model/descriptor text.
</content>
</invoke>
