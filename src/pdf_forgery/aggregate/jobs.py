"""In-memory job manager + background pipeline runner for the reviewer UI.

The thin Stage 7 slice has no Celery/Redis/Postgres yet (those belong to the
full build). This module is the minimal stand-in: it accepts an uploaded PDF,
runs the five detection stages in a background thread, reports live per-stage
progress, then rolls the results up via :func:`~pdf_forgery.aggregate.aggregate`
and scrubs them at the PHI boundary via
:func:`~pdf_forgery.aggregate.to_advisory_input`.

Everything the UI consumes is the **scrubbed** descriptor view
(:class:`AdvisoryInput`) plus the advisory prose. The rich server-side
:class:`AggregateResult` is held only so a future gated evidence endpoint can
reach the raw before→after text; it never crosses to the client from here.

Design note (project invariant #8 / #10): submission never blocks on inference,
and progress reporting carries stage NAMES + tiers only — never document
content.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field

from ..core.stage import Stage
from ..pipeline import run_pipeline
from .advisory import generate_advisory
from .aggregate import aggregate
from .config import AggregateConfig
from .models import AdvisoryInput, AdvisoryOutput, AggregateResult
from .phi_scrub import to_advisory_input

# The stages, in run order. Built fresh per job (cheap, and keeps any per-run
# state isolated). Mirrors the live ``STAGES`` tuple in ``test.py``.
STAGE_ORDER: tuple[str, ...] = (
    "revision_recovery",
    "font_forensics",
    "invoice_arithmetic",
    "provenance_metadata",
    "ocr_crosscheck",
    "image_forensics",
)


def build_default_stages() -> list[Stage]:
    """Construct one fresh instance of each detection stage, in run order."""
    from ..font_forensics import FontForensicsStage
    from ..image_forensics import ImageForensicsStage
    from ..invoice_arithmetic import InvoiceArithmeticStage
    from ..ocr_crosscheck.stage import OCRCrossCheckStage
    from ..provenance_metadata import ProvenanceMetadataStage
    from ..revision_recovery import RevisionRecoveryStage

    return [
        RevisionRecoveryStage(),
        FontForensicsStage(),
        InvoiceArithmeticStage(),
        ProvenanceMetadataStage(),
        OCRCrossCheckStage(),
        ImageForensicsStage(),
    ]


# job lifecycle states (distinct from a stage's per-row state)
QUEUED = "queued"
PROCESSING = "processing"
DONE = "done"
ERROR = "error"


def _pdf_page_count(pdf_bytes: bytes) -> int:
    """Return the number of renderable PDF pages, or ``0`` on failure.

    Page count is non-PHI metadata used only to tell the reviewer UI which gated
    page-image URLs to request.  Keep this best-effort like page rendering: a
    malformed PDF or unavailable renderer must not prevent job submission.
    """
    try:
        import pypdfium2 as pdfium
        from .overlay import _RENDER_LOCK

        # Pdfium has process-global state and is not thread-safe. Share the
        # renderer lock because submissions can overlap existing page renders.
        with _RENDER_LOCK:
            document = pdfium.PdfDocument(pdf_bytes)
            try:
                return len(document)
            finally:
                document.close()
    except Exception:
        return 0


@dataclass
class Job:
    """One uploaded document's analysis lifecycle (mutable, in-memory)."""

    job_id: str
    filename: str
    state: str = QUEUED
    # ordered per-stage progress: stage name -> queued/running/done/error
    stage_states: dict[str, str] = field(default_factory=dict)
    pdf_bytes: bytes | None = None  # server-side only (PHI); for the gated evidence view
    page_count: int = 0  # non-PHI document metadata; computed once at submission
    aggregate_result: AggregateResult | None = None  # server-side, behind PHI boundary
    advisory_input: AdvisoryInput | None = None  # scrubbed; safe to serve
    advisory_output: AdvisoryOutput | None = None  # cached after first SSE generation
    error: str | None = None
    created_at: float = field(default_factory=time.time)


class JobManager:
    """Thread-safe in-memory store + background runner for analysis jobs."""

    def __init__(self, config: AggregateConfig | None = None) -> None:
        self._config = config or AggregateConfig()
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    @property
    def config(self) -> AggregateConfig:
        return self._config

    def submit(self, pdf_bytes: bytes, filename: str) -> Job:
        """Register a job and start analysing it in the background (non-blocking)."""
        job = Job(
            job_id=uuid.uuid4().hex,
            filename=filename,
            stage_states={name: QUEUED for name in STAGE_ORDER},
            pdf_bytes=pdf_bytes,
            page_count=_pdf_page_count(pdf_bytes),
        )
        with self._lock:
            self._jobs[job.job_id] = job
        thread = threading.Thread(
            target=self._run, args=(job.job_id, pdf_bytes), daemon=True
        )
        thread.start()
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def ensure_advisory(self, job_id: str) -> AdvisoryOutput | None:
        """Generate (once) and cache the advisory output for a finished job.

        Returns ``None`` if the job is unknown or not yet done. The advisory is
        produced lazily so the verdict can surface the instant detectors finish,
        with the prose following afterward.
        """
        job = self.get(job_id)
        if job is None or job.advisory_input is None:
            return None
        with self._lock:
            if job.advisory_output is None:
                job.advisory_output = generate_advisory(
                    job.advisory_input, config=self._config
                )
            return job.advisory_output

    def render_page(self, job_id: str, page_index: int) -> bytes | None:
        """Render a plain page image (no boxes) for the document viewer (PHI / gated).

        The frontend draws the bounding boxes itself as a CSS overlay using the
        normalized ``bbox`` that already crosses the scrub boundary; this endpoint
        supplies only the page pixels. ``None`` when the job/page is unknown or
        rendering is unavailable.
        """
        from .overlay import render_page_overlay

        job = self.get(job_id)
        if job is None or job.pdf_bytes is None:
            return None
        return render_page_overlay(job.pdf_bytes, page_index, [], config=self._config)

    def render_overlay(self, job_id: str, finding_id: str) -> bytes | None:
        """Bake the annotated page PNG for one located finding (PHI / gated).

        Returns annotated PNG bytes, or ``None`` when the job/finding is unknown,
        the finding is not localised, or rendering is unavailable. The PNG holds
        real document pixels, so this is the gated evidence path — it is NOT part
        of the scrubbed :class:`AdvisoryInput` the status endpoint serves.
        """
        from .overlay import render_page_overlay

        job = self.get(job_id)
        if job is None or job.pdf_bytes is None or job.aggregate_result is None:
            return None
        # finding_id is "{stage}-{index}"; only revision_recovery is localised today.
        stage, _, index_str = finding_id.rpartition("-")
        if stage != "revision_recovery":
            return None
        try:
            index = int(index_str)
        except ValueError:
            return None

        for result in job.aggregate_result.stage_results:
            if result.stage != "revision_recovery":
                continue
            rich = getattr(result.payload, "findings", None)
            if not rich or index < 0 or index >= len(rich):
                return None
            rf = rich[index]
            location = getattr(rf, "location", None)
            if location is None or not location.boxes or rf.page_index is None:
                return None
            boxes_pt = [(b.x0, b.top, b.x1, b.bottom) for b in location.boxes]
            return render_page_overlay(
                job.pdf_bytes, rf.page_index, boxes_pt, config=self._config
            )
        return None

    # -- internals ----------------------------------------------------------

    def _set_stage(self, job_id: str, stage: str, state: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None and stage in job.stage_states:
                job.stage_states[stage] = state

    def _run(self, job_id: str, pdf_bytes: bytes) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                job.state = PROCESSING

        def on_progress(stage: str, state: str) -> None:
            self._set_stage(job_id, stage, state)

        try:
            stages = build_default_stages()
            results = run_pipeline(
                pdf_bytes, stages, path=None, on_progress=on_progress
            )
            agg = aggregate(results, self._config)
            advisory_input = to_advisory_input(agg, self._config)
        except Exception as exc:  # the runner must never crash the server
            with self._lock:
                job = self._jobs.get(job_id)
                if job is not None:
                    job.state = ERROR
                    job.error = f"analysis failed: {exc}"
            return

        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                job.aggregate_result = agg
                job.advisory_input = advisory_input
                job.state = DONE


__all__ = [
    "Job",
    "JobManager",
    "STAGE_ORDER",
    "build_default_stages",
    "QUEUED",
    "PROCESSING",
    "DONE",
    "ERROR",
]
