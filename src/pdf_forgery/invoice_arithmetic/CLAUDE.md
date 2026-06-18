# invoice_arithmetic — working notes

Detailed history + owner calibration decisions below. Canonical spec / layout /
status live in the repo-root `CLAUDE.md`.

- [x] **Stage — `invoice_arithmetic/` (broken accounting relationships)** (2026-06-15)
  - Goal: catch the clean-re-render edit (e.g. Sejda) that font_forensics and
    revision_recovery cannot — uniform fonts, single revision, no structural
    seam. On `tampered.pdf` the line amount was edited `249.69 → 24019.69` while
    qty `3.00` and rate `83.23` were left untouched, so `qty*rate != amount` is
    the only signal left.
  - [x] `table.py`: reconstruct the table from glyph COORDINATES (reuses the
    shared `core.glyphs` extractor — no third extraction path). Rows clustered
    on y, columns defined by the header row's role labels, data cells assigned to
    columns by midpoint-between-header-centres (handles right-aligned numbers
    under left-aligned headers + missing columns). `summary.py` adds a
    `label: value` summary-block extractor (subtotal/discount/CGST/SGST/grand
    total/deposit/balance printed below the table).
  - [x] `numbers.py`: robust parse — strips `Rs`/`INR`/`₹`/`$`/`Rupees`, removes
    grouping commas (Western `1,234,567.89` AND Indian `1,00,000.00`), decimals,
    parenthesised negatives; rejects percentages/IDs.
  - [x] `relationships.py`: evaluates ONLY labelled relationships (never
    brute-forces number pairs) — qty*rate=amount, sum(amounts)=subtotal,
    subtotal-discount+tax=grand total, CGST+SGST(+IGST)=GST, deposit+balance=
    final. Tolerance = absolute epsilon OR relative %; legitimate rounding
    (`249.685 → 249.69`) does not flag. `localize.py`: tamper localization +
    convergence — the single cell whose correction reconciles the MOST broken
    equations; convergence count drives confidence.
  - [x] `config.py` `InvoiceConfig` (all thresholds/score values/role-label
    vocab/toggles), `scoring.py` rule tree, `models.py`, `analyze.py`
    (`FontReport`-style `InvoiceReport` + `_as_stage`), `adapter.py`, `stage.py`
    `InvoiceArithmeticStage` (conforms to `core.Stage`).
  - **Owner calibration decisions (2026-06-15):**
    - **Convergence-gated tiering**: a lone gross broken equation (no subtotal to
      corroborate) is capped at **strong MEDIUM**; HIGH requires convergence
      (one cell reconciles ≥2 equations) — the only self-contained corroboration
      from the submitted bill alone. So `tampered.pdf` → **MEDIUM 65** flagging
      `3.00*83.23 != 24019.69` localized to the amount cell (prompt accepts
      "strong MEDIUM"). The convergence→HIGH path is proven on the fixture below.
    - **`Microsoft-Sample-Invoice.pdf` is NOT a clean baseline.** It carries a
      GENUINE line-item break `9.00*41.61 = 374.49 != 37004.49` (confirmed
      contiguous uniform `YWNRZS+Calibri` glyphs — not extraction noise, not a
      misclassified total row), present in BOTH the clean and tampered copies,
      plus a Sejda producer + ModDate>CreationDate. Both Microsoft files are
      therefore positives. **A real, untouched invoice is still needed for the
      LOW precision baseline** — drop one at `test_pdf's/pristine-invoice.pdf`
      to activate `test_pristine_invoice_low_on_arithmetic` (currently skipped).
  - [x] `scripts/make_invoice_fixtures.py` (deterministic, single-revision,
    uniform font): KNOWN-NEGATIVE `invoice_clean.pdf` (line items + subtotal +
    discount + CGST/SGST + grand total all reconcile, incl. a `249.685 → 249.69`
    rounding row) → **LOW 10**; KNOWN-POSITIVE `invoice_convergence_tamper.pdf`
    (amount `300.00 → 30000.00`, totals untouched → line item AND subtotal both
    break, convergence 2 on the edited cell) → **HIGH 92** localized to the
    `30000.00` amount cell.
  - [x] Tests: `test_invoice_numbers.py` (parsing), `test_invoice_relationships.py`
    (eval/tolerance/localization/convergence on constructed tables),
    `test_invoice_scoring.py` (tier boundaries), `test_invoice_end_to_end.py`
    (fixtures + real Sejda tamper + Microsoft broken row + INCONCLUSIVE on
    malformed/non-invoice).

- [x] **Rate-display-rounding false positives on Azure-style usage bills** (2026-06-18)
  - Symptom: `Microsoft-Sample-Invoice_clear.pdf` (clean, 13-page) scored
    **MEDIUM 65 with 69 broken line items**. Every one was a LINE_ITEM
    `qty * rate = amount` break; subtotal/GST/grand-total all reconciled.
  - **Root cause: the `rate` column is printed rounded to 2 dp while the true
    rate carries more precision** (e.g. `2434.87 * shown 0.08 = 194.79` vs
    stated `204.53`; true rate ≈0.084). With a large `qty` the rounding error
    is large in both absolute and relative terms, so it blew past `abs_tolerance`
    / `rel_tolerance` / `gross_rel_error`. This is inherent to the invoice
    format, not a forgery — and confirms the principle that no fixed tolerance
    can verify `qty*rate` tighter than the rate's *printed* precision allows.
  - **Fix (deterministic; declined the LLM-agent idea — conflicts with the
    fully-local/no-cloud constraint, determinism, and testability):**
    `relationships._rate_rounding_ok` + config `rate_precision_aware` (default
    True). For LINE_ITEM only, also accept an amount that falls in the interval
    `qty * (rate ± ½·10^-rate_decimals)` (rate decimals read from the cell's
    raw text via `_displayed_decimals`). **`qty` is treated as exact** — widening
    by an integer qty's ±0.5 would dangerously mask a moderate amount edit, and
    quantities are exact counts shown at full precision. Threaded through
    `_make_relationship(within_override=...)` so it can only *accept*, never turn
    a real break into a false pass. Does NOT touch sum/subtotal/GST/grand-total
    checks.
  - **Tests:** `test_invoice_relationships.py` — rate-rounding row → no flag;
    band disabled → flags; genuine 100× tamper → still flags+gross; moderate
    real edit (300 vs band top 206.96) → NOT masked.
  - **Verified:** `_clear` MEDIUM 65 → **LOW 10** (0 broken). `tampered.pdf`
    still **MEDIUM 65** (both edited rows flag), `Microsoft-Sample-Invoice.pdf`
    still **MEDIUM 65** (genuine `37004.49` break preserved). Full suite:
    **821 passed, 1 skipped**.
