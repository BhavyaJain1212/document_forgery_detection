# Forgery Detection Pipeline (FDP)

Async document-forgery detection for healthcare insurance claims. A claims investigator
uploads a PDF/JPEG/PNG; the system triages it, runs deterministic + neural forensics,
fuses the evidence into a calibrated risk score with an evidence trail, and routes
borderline cases to a human reviewer. All input is treated as PII/PHI.

## Sources of truth — read before implementing anything
- **Build spec:** @docs/ARCHITECTURE.md — what to build and how the pieces fit. Every
  design decision is already made here. Section numbers (§) below refer to it.
- **Methodology:** @docs/document_forgery_reference.html — the canonical mapping of each
  forgery method → detection technique → output tag → research paper.
- **Canonical strings:** @docs/FORGERY_METHODS.md — the exact `forgery_method` and
  evidence-tag literals. These are frozen; do not invent or paraphrase them.
- **Conflict rule (§13):** if the spec and the reference disagree, the **reference wins**.

## Hard invariants (do not violate without flagging to me first)
1. **`forgery_method` strings are verbatim from the reference.** No paraphrasing, no
   pluralizing, no casing changes. See @docs/FORGERY_METHODS.md.
2. **The Pydantic envelope in §5 is a contract.** Do not rename or drop fields on
   `Evidence`, `ModuleResult`, `PageResult`, `DocumentResult`. Add, never mutate.
3. **Never re-encode before compression-history analysis (§7).** CAT-Net, ELA, and
   DocTamper on JPEG must receive the ORIGINAL bytes. `image_normalize` may produce a
   separate normalized tensor for models that need it, but must always preserve and pass
   the original buffer. Re-saving destroys the double-quantization signal — make that
   code path impossible.
4. **Route by true format, never the extension (§8.1).** Detect via magic bytes. An
   extension/format mismatch is itself a weak forgery signal (emit a `METADATA_FLAG`).
5. **Never execute PDF active content (§8.4).** JavaScript / `/Launch` / embedded files
   are sandboxed and flagged, never run.
6. **Never convert unsupported formats.** TIFF/WEBP/HEIC → terminal REJECT. Re-encoding
   destroys forensic signal and breaks chain of custody.
7. **CPU/GPU split is enforced by Celery queue routing (§2, §3):** deterministic tasks →
   `cpu` queue; neural tasks → `gpu` queue. A job fans out across both, re-converges at
   synthesis.
8. **The API never blocks on inference (§6).** POST returns `202 + {job_id, status_url}`.
9. **Every access is authenticated and audit-logged (§6).** The audit trail is
   hash-chained (chain of custody). No silent failures — failures become explicit
   terminal/error states (§11).
10. **Logging is PHI-safe** (structlog). Never log document contents or extracted PII/PHI.

## The model boundary (§0, §7) — this shapes the whole build
- Two adapters behind one `ForensicModel` Protocol, selected by
  `FDP_MODEL_BACKEND=stub|forensichub` (default `stub`).
- **`StubForensicModel`** — build it for real, deterministic, seeded by image content
  hash so tests are reproducible. This is what makes the system runnable with no GPU.
- **`ForensicHubModel`** — wraps scu-zjz/ForensicHub; documented real-weights path.
  **Never attempt to download model weights in the sandbox.** Document the step in
  `models/WEIGHTS.md` instead.
- Switching backends must change zero pipeline code — only what the registry returns.

## Fusion & calibration (§10) — rules, not a learned classifier
- Hard rules first (override soft scores): valid signature + no post-sign change → risk
  ceiling 0.2; registry lookup fails → floor 0.8; arithmetic inconsistent → floor 0.6;
  cross-document image reuse → floor 0.7.
- Then confidence-weighted soft aggregation of remaining (neural) sub_scores.
- Then a conservative monotone calibration curve. Raw model scores are **ranking signals,
  not probabilities** (the DocForge-Bench gap). Never surface a raw score as a probability.
- Force human review — regardless of the numeric score — when AI-inpainting/diffusion is
  suspected or aggregate confidence is low (the AIForge-Doc collapse zone). Set
  `forced_review_reason`.
- Per-page → document rollup is **worst-case escalation**, not averaging.

## Tech stack (§3) — fixed; flag before substituting
Python 3.11+ · FastAPI + Uvicorn · Pydantic v2 · Celery + Redis (broker) ·
PostgreSQL (SQLAlchemy + Alembic) · S3-compatible blob store (MinIO local) ·
PDF: pikepdf, pdfid, pdfsig (poppler-utils), pypdf ·
Image (CPU): Pillow, numpy, opencv-python, imagehash, pyexiftool, pngcheck ·
OCR: PaddleOCR (fallback Tesseract) · Neural: ForensicHub (scu-zjz/ForensicHub) ·
Observability: structlog (PHI-safe) + OpenTelemetry.

## Commands (target — wire these up in Phase 1)
- `docker compose up` — runs api, worker-cpu, worker-gpu (CPU-only in stub mode), redis,
  postgres, minio.
- `pytest` — test suite. Every phase ends green.
- `alembic upgrade head` — apply DB migrations.

## Definition of done (§0 acceptance bar)
With `FDP_MODEL_BACKEND=stub`, a user can POST a real PDF/JPEG/PNG, poll, and receive the
full §5 JSON envelope with every tag populated, every §11 edge case handled, and a
hash-chained audit trail. **No GPU required to demonstrate the architecture.**

## Workflow expectations
- Read @docs/TODO.md and work one phase at a time. Do not scaffold later phases early.
- End every phase with a runnable demonstration against a real file in stub mode.
- When a decision isn't covered by the spec or reference, ask — don't guess and don't
  silently substitute libraries or rename contract fields.
