# TODO / change log

## Done

### revision_recovery: xref-driven changed-object set (false-positive fix) — 2026-06-15
- **Symptom:** `Microsoft-Sample-Invoice_clear.pdf` (a clean hybrid-reference PDF
  using cross-reference streams) returned revision_recovery **MEDIUM(60)** —
  ~4183 phantom "new CONTENT objects" with no text change — which fused with the
  invoice MEDIUM to an overall **HIGH(85)**.
- **Root cause:** the changed-object set was derived by diffing two pikepdf
  object *enumerations* from differently-truncated reparses. qpdf's enumeration
  of a truncated revision depends on what its xref can resolve, so the truncated
  first revision under-enumerated (~62 objects) while the full file enumerated
  4244 — inventing ~4183 phantom objects. The actual final increment was a
  184-byte compatibility xref append (empty `0 0` subsection + a back-pointing
  `/XRefStm`) that writes **zero** objects.
- **Fix:** `diff_objects` now derives the changed set from
  `objects_written_in_increment(raw, start, end)` — the in-use `(num, gen)` whose
  xref record is physically authored within the revision's own appended byte
  range `rev_b.data[len(rev_a.data):]`. Reads the increment's classic table,
  cross-reference stream, and any in-increment `/XRefStm`; a `/XRefStm` that
  points back into an earlier revision authors nothing. The hybrid/compatibility
  append falls out as zero writes with no special-casing. No cross-revision
  enumeration diff remains. The text-diff path is untouched.
- **Classifier:** `/Type /XRef` and `/Type /ObjStm` containers now classify as
  META (structural plumbing), never CONTENT.
- **Tests:** `tests/test_objects_written.py` (classic / xref-stream / hybrid
  cases + defensive contract); `tests/test_objectdiff.py` migrated to genuine
  incremental updates (`_append_increment`); acceptance guard
  `test_microsoft_hybrid_revision_recovery_not_medium`. Regression guard
  intact: `Acrobat_Demo_File.pdf` still HIGH(95). Full suite: 444 passed, 1
  skipped.

## Open / out of scope for the above fix
- **Overall on `Microsoft-Sample-Invoice_clear.pdf` is still MEDIUM(65)**, now
  driven *solely* by `invoice_arithmetic`: 69 "lone broken equation (could be
  source/extraction error)" findings from column-misreading on the 13-page
  multi-invoice document (e.g. `qty * rate(0) = 0`). This is a separate
  invoice-segmentation precision issue, not a revision-recovery one. To reach an
  overall LOW, gate invoice MEDIUM on segmentation confidence / suppress
  zero-rate phantom equations.
- **Real precision baseline still owed:** drop a genuine untouched invoice at
  `test_pdf's/pristine-invoice.pdf` to activate the skipped
  `test_pristine_invoice_low_on_arithmetic`.
- Planned sibling stage: `ocr_crosscheck`.
