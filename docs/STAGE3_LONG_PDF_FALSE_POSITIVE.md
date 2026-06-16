# Stage 3 — Long-PDF OCR false positive (`Microsoft-Sample-Invoice_clear.pdf`)

**Status: FIXED (2026-06-16).** A+B (RC#1) and E+F (RC#2) all implemented per
the plan below. Real-engine result: **HIGH 95 → LOW 15**.

```
BEFORE: TIER=HIGH  SCORE=95   agree=2201 mismatch=3  (3x AMOUNT MISMATCH, bare "3")
                              non-agree mass=25.5 (uncapped, absolute-only gate)
AFTER:  TIER=LOW   SCORE=15   agree=2204 mismatch=0  (the 3 false MISMATCHes gone)
                              non-agree: 14x OCR_ONLY/ID + 2x EMBEDDED_ONLY/ID
                              (repeated "Microsoft Azure" header, capped)
                              mass≈3.9 vs relative floor 0.02*2204≈44.1 → LOW
```

Full suite: 692 passed, 1 skipped (the skip is the pre-existing
pristine-invoice precision baseline, unrelated). See
`src/pdf_forgery/ocr_crosscheck/CLAUDE.md` for the task-by-task implementation
log.

The OCR cross-check stage (`ocr_crosscheck`) flags
`test_pdf's/Microsoft-Sample-Invoice_clear.pdf` as **HIGH 95** even though the
file is a clean, untampered 13-page Microsoft Azure invoice. This is a false
positive. This document records the reproduction, the two independent root
causes, candidate solutions for each, and an ordered implementation plan.

This is a *follow-on* to the earlier single-page fix recorded in the stage
`CLAUDE.md` ("False-positive fix: every clean PDF scored HIGH", 2026-06-16),
which fixed the PaddleOCR geometry / ID-over-classification / lone-orphan
issues. Those fixes hold; the two causes below are **new and specific to long,
multi-page documents with repeated headers and product-SKU lines.**

---

## 1. Reproduction

```
./.venv/bin/python -c "
from pdf_forgery.ocr_crosscheck.analyze import analyze_path
r = analyze_path(\"test_pdf's/Microsoft-Sample-Invoice_clear.pdf\")
print(r.result.tier, r.result.score, r.diagnostics)"
```

Observed (real PaddleOCR engine, GPU):

```
TIER: HIGH   SCORE: 95   routed_to: None
diagnostics: {'offpage_dropped': 0, 'low_conf_dropped': 0,
              'matched': 2204, 'agree': 2201, 'mismatch': 3}
total divergences: 2220
by type: {'AGREE': 2201, 'EMBEDDED_ONLY': 2, 'OCR_ONLY': 14, 'MISMATCH': 3}
non-agree by (type, class):
    ('OCR_ONLY', 'ID')      14
    ('MISMATCH', 'AMOUNT')   3
    ('EMBEDDED_ONLY', 'ID')  2
total mass: 25.5
```

The document is **2201/2204 agree** — overwhelmingly clean. Two distinct kinds
of noise produce the false verdict.

---

## 2. Root cause #1 (drives the HIGH 95) — bare-digit AMOUNT misclassification + whole-line zero tolerance

### What fires

Three matched groups on page 2 are classified `MISMATCH / AMOUNT` (weight 3.0
each), and a single AMOUNT/DATE `MISMATCH` is enough to originate **HIGH 95**
(`scoring.py:86-94`, `score_high_amount_date_mismatch = 95`):

```
p1 MISMATCH AMOUNT emb='Standard_M64s, AU East, 3'      ocr='Standard M64s, AU East, 3'
p1 MISMATCH AMOUNT emb='Standard_B2s, AU East, 3 Years' ocr='Standard B2s, AU East, 3 Years'
p1 MISMATCH AMOUNT emb='Standard_E4s_v3, AU East, 3'    ocr='Standard E4s v3, AU East, 3'
```

These are Azure reservation **product-description lines**, not money. No forgery.

### The exact failure chain

1. The line contains a **bare integer** (`3` — the reservation term in *years*).
   `revision_recovery.highvalue.classify_token_kind("3")` → `AMOUNT`, because the
   spec's amount pattern is "digits with optional separators/decimals" and a bare
   digit run matches it (`normalize.classify` → `TokenClass.AMOUNT`).

2. `divergence._group_token_class` (`divergence.py:49-64`) walks **every
   whitespace-split sub-token** of the joined group and returns the
   **most-sensitive** class found (priority `AMOUNT < DATE < ID < PROSE`). One
   stray `3` therefore elevates the **entire 25-character prose+SKU line** to
   `AMOUNT`.

3. `classify_group` then compares the **whole joined string** at the AMOUNT
   tolerance, which is **zero** (`amount_allowed_edits = 0`, the deliberate
   high-value inversion in `normalize.allowed_edits`).

4. The embedded SKU has an underscore (`Standard_M64s`) that renders as a thin
   low glyph; PaddleOCR reads it as a space / drops it (`Standard M64s`). After
   folding, the only surviving delta is that underscore:

   ```
   emb_fold = '5tan0ar0_m645,auea5t,3'
   ocr_fold = '5tan0ar0m645,auea5t,3'     # underscore gone
   levenshtein = 1   →  AMOUNT tol 0  →  MISMATCH
                     →  PROSE  tol 3  →  AGREE
   ```

   The altered character is in the **prose/SKU part of the line, not in the
   digit** — yet the whole comparison is judged at amount strictness.

### Why this is wrong (two compounding defects)

- **(1a) Over-broad AMOUNT classification.** A standalone small integer with no
  currency symbol, thousands separator, or decimal is treated as a monetary
  amount. On invoices, bare small integers are quantities, terms, list indices,
  page numbers — not amounts.
- **(1b) Strictest sub-token poisons the whole group, then zero tolerance is
  applied to the whole concatenated line.** The zero-tolerance amount rule is
  correct for a token that *is* an amount (`5,000` vs `6,000` — 1 char *is* the
  signal). It is wrong when applied to a long joined line whose "amount" is one
  buried integer: any 1-char OCR artifact *anywhere* in the line trips it.

---

## 3. Root cause #2 (would still give MEDIUM after #1 is fixed) — absolute divergence-mass threshold does not scale with document length

### What fires

Even after #1 is fixed (the 3 AMOUNT MISMATCHes become AGREE), the residual
non-AGREE mass is:

```
14 × OCR_ONLY/ID  (weight 1.05)  = 14.70
 2 × EMBEDDED_ONLY/ID (weight 0.90) =  1.80
                              mass  = 16.50
```

`scoring.py:136-147` compares this to `medium_divergence_mass = 2.0` (an
**absolute** count) → **MEDIUM 50**. Still a false positive, just less severe.

### The repeated artifact

13 of the 14 OCR_ONLY orphans are the **same** "Microsoft Azure" page header /
logo, one per page:

```
p0 OCR_ONLY ID 'Microsoft Azure'   (×2)
p1 OCR_ONLY ID 'Microsoft Azure'
p2 ... p12 OCR_ONLY ID 'Microsoft Azure'
```

It is a vector logo / header that OCR recognises but pdfminer either does not
emit in the text layer or emits at a position whose center does not fall inside
the OCR box (and IoU < 0.30) — so it is an unmatched OCR-only word on **every
page**.

### Why this is wrong

`medium_divergence_mass` is an **absolute** threshold that does not scale with
document size. The steady-state OCR-noise rate is per-page (or per-word): here
~1.2 orphans/page. On a **1-page** invoice that is mass ≈ 1.05 < 2.0 → LOW
(correct). On a **13-page** invoice the *same per-page rate* accumulates to
mass ≈ 16.5 → MEDIUM (false positive). The noise fraction is **0.7 %** of the
2204 compared words — clearly clean — but the absolute threshold can't see that.

**Any** sufficiently long clean document with a repeated header/footer/logo will
cross an absolute mass floor. The threshold must be **relative to the volume of
text actually compared**, not a fixed count.

---

## 4. Candidate solutions

### For root cause #1

**Solution A (recommended, structural) — localize the high-value tolerance to
the high-value sub-token.** When a multi-token group is elevated to AMOUNT/DATE
only because of an embedded sub-token, do **not** judge the whole joined string
at zero tolerance. Instead:
  1. Extract the high-value sub-tokens (amount/date) from the embedded side.
  2. Require each to survive **intact** in the folded OCR string (zero-tolerance
     *containment* check — this is the real signal: was the *amount* altered?).
  3. Judge the **remainder of the line at PROSE tolerance**.
  4. Result = AGREE if both hold; AMOUNT/DATE MISMATCH only if a high-value
     sub-token is actually missing/changed; prose MISMATCH if only prose differs
     beyond prose tolerance.

  This fixes the underscore case (digit `3` is intact → governed by prose
  tolerance → AGREE) while still catching a genuinely tampered amount
  (`5,000`→`6,000`: the high-value token is no longer contained → AMOUNT
  MISMATCH → HIGH). This is the correct general fix.

**Solution B (recommended companion, narrowing) — require monetary context for a
group-elevating AMOUNT.** Locally in `ocr_crosscheck` (do **not** mutate Stage
1's shared `highvalue` classifier — other stages depend on it), treat a bare
1–2 digit integer with no currency symbol/word, decimal, or thousands separator
as **not** AMOUNT-elevating for the group-class decision. A lone `3` → PROSE.
This removes the elevation at the source and is cheap. Use a config flag
(e.g. `amount_requires_monetary_context: bool = True`).

**Solution D (optional hardening, band-aid) — fold the underscore.** Add `_`
(and similar non-semantic joiner punctuation) to the fold so underscore-vs-space
noise is erased before comparison. Fixes *this* artifact only; the structural
defect (any other 1-char noise in a digit-bearing line) remains, so this is a
complement, **not** a substitute for A/B.

> Recommendation: implement **A + B**. A is the structural correctness fix; B
> reduces how often the situation arises and keeps the change conservative. D
> optional.

### For root cause #2

**Solution E (recommended) — make the MEDIUM mass threshold relative to compared
volume.** Add `divergence_mass_ratio` (e.g. `0.02`) and require, for MEDIUM:

```
mass >= max(medium_divergence_mass, divergence_mass_ratio * compared_words)
```

where `compared_words` = number of matched groups + unmatched on the page set
(≈ 2204 here → relative floor ≈ 44 ≫ 16.5 → LOW). Keeps the absolute floor so a
*short* doc with a real cluster still reaches MEDIUM, but a long clean doc's
proportional steady-state noise stays LOW. `score()` currently has no access to
the compared-word count — it must be threaded in (see plan).

**Solution F (recommended companion) — collapse identical repeated orphans.** A
single orphan text repeated once per page (`'Microsoft Azure'` ×13) is a
*systematic* extraction artifact, not 13 independent anomalies. Count
**distinct** orphan texts (folded), or cap the total contribution of any one
repeated orphan token, before summing mass. This directly neutralises repeated
headers/footers/logos regardless of page count.

> Recommendation: implement **E** (general, principled) and **F** (cheap,
> targets the exact artifact). Either alone fixes this file; together they are
> robust.

---

## 5. Implementation plan (for Sonnet)

Work in `src/pdf_forgery/ocr_crosscheck/`. Keep every threshold in `config.py`
(project invariant — nothing magic at call sites). Read-only stage, never
raises. Run the suite with
`./.venv/bin/python -m pytest --ignore=tests/test_microsoft_pdf.py`.

### Task 1 — RC#1 fix B: narrow bare-digit AMOUNT elevation (`normalize.py` / `divergence.py`, `config.py`)
- Add `amount_requires_monetary_context: bool = True` to `OCRCrossCheckConfig`.
- Add a helper (in `normalize.py`) `is_monetary_amount(token) -> bool` that
  returns True only when an AMOUNT-classified token has a currency symbol/word
  (`₹ Rs INR $`), a decimal point, **or** a thousands separator — i.e. a bare
  `\d{1,2}` with none of these is *not* monetary.
- In `divergence._group_token_class` and `classify_unmatched`, when
  `amount_requires_monetary_context` is set, demote a bare-integer AMOUNT to
  PROSE for the class decision. **Do not** touch
  `revision_recovery.highvalue` — keep the narrowing local to Stage 3.

### Task 2 — RC#1 fix A: localized high-value tolerance in `classify_group` (`divergence.py`, `normalize.py`)
- Add `is_within_tolerance_localized(embedded_join, ocr_text, token_class, cfg)`
  (or extend `classify_group`): when `token_class` is AMOUNT/DATE **and** the
  embedded join is multi-token, (a) verify each embedded high-value sub-token is
  contained (folded, zero-tolerance) in the folded OCR text; (b) evaluate the
  whole line at PROSE tolerance. AGREE iff both pass. If a high-value sub-token
  is missing/changed → keep AMOUNT/DATE MISMATCH (real signal preserved).
- Single-token high-value groups keep the existing strict whole-string check
  (no behaviour change for a true `5,000`-vs-`6,000` amount).

### Task 3 — RC#2 fix E: relative MEDIUM mass threshold (`scoring.py`, `analyze.py`, `config.py`)
- Add `divergence_mass_ratio: float = 0.02` to config.
- Thread a `compared_words: int` argument into `score(...)` (count = matched
  groups + unmatched embedded + unmatched OCR across all pages; compute in
  `analyze._run` step 8/9 and pass it).
- Change the MEDIUM gate to
  `mass >= max(cfg.medium_divergence_mass, cfg.divergence_mass_ratio * compared_words)`.
- Default `compared_words=0` so existing direct `score()` unit-test calls keep
  the absolute-floor behaviour (back-compat).

### Task 4 — RC#2 fix F: collapse repeated orphans (`scoring.py` or `divergence.py`)
- Before summing mass, group EMBEDDED_ONLY/OCR_ONLY divergences by folded text;
  count each **distinct** orphan once (or cap its contribution, e.g. at
  `2 × its weight`). Put the cap factor in config
  (`repeated_orphan_cap: int = 2`). This stops a per-page header/logo from
  scaling mass with page count.

### Task 5 — tests
- **Unit** (`tests/test_ocr_divergence.py`, `test_ocr_scoring.py`,
  `test_ocr_normalize.py`):
  - bare-digit line (`'Standard_M64s, AU East, 3'` vs `'Standard M64s, AU East, 3'`)
    → AGREE, not AMOUNT MISMATCH.
  - genuine amount edit (`'Total: 5,000'` vs `'Total: 6,000'`) → AMOUNT MISMATCH
    (regression guard — the fix must NOT blind the real signal).
  - relative-mass: many cheap orphans below `ratio * compared_words` → LOW;
    a real dense cluster on a short doc → MEDIUM.
  - repeated identical orphan ×N collapses to a capped contribution.
- **Acceptance** (`tests/test_acceptance_samples.py`): add
  `test_microsoft_long_invoice_ocr_crosscheck_not_high` running the real engine
  on `test_pdf's/Microsoft-Sample-Invoice_clear.pdf`, asserting tier is **LOW or
  INCONCLUSIVE** (not HIGH/MEDIUM). Skip gracefully if PaddleOCR/pypdfium2
  unavailable (mirror the existing `test_page4_*` skip guard).

### Task 6 — verify + record
- Re-run the reproduction from §1 → expect **LOW** (mass ≈ 16.5 now below the
  relative floor, or collapsed to ≈ 1–2 by Task 4; the 3 AMOUNT MISMATCHes gone).
- Full suite green: `./.venv/bin/python -m pytest --ignore=tests/test_microsoft_pdf.py`.
- Update `src/pdf_forgery/ocr_crosscheck/CLAUDE.md` task history and flip this
  doc's status to FIXED with the before→after numbers.

### Acceptance criteria
- `Microsoft-Sample-Invoice_clear.pdf` → **not** HIGH and **not** MEDIUM
  (LOW or INCONCLUSIVE).
- A genuinely tampered amount still → AMOUNT MISMATCH → HIGH (no signal lost).
- Single-page clean fixtures unchanged (no new regressions).
- Full suite passes.

---

## 6. Risks / notes
- **Do not weaken the real signal.** The whole point of Stage 3 is catching an
  altered amount/date. Task 2's localized check and Task 1's narrowing must keep
  a true single high-value token at zero tolerance — the regression test in
  Task 5 is mandatory, not optional.
- **Keep the narrowing local to `ocr_crosscheck`.** `revision_recovery.highvalue`
  is shared by other stages; changing what it calls AMOUNT risks cross-stage
  regressions. Demote bare integers only inside Stage 3's class decision.
- **Relative threshold needs the compared-word count plumbed through `score()`**
  — keep the default at 0 so existing unit tests that call `score()` directly
  keep asserting the absolute-floor behaviour.
- Either E or F alone fixes this specific file; doing both makes long-document
  robustness general rather than artifact-specific.
</content>
</invoke>
