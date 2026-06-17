"""In-memory job manager + background pipeline runner for the reviewer UI.

The thin Stage 6 slice has no Celery/Redis/Postgres yet (those belong to the
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
)


def build_default_stages() -> list[Stage]:
    """Construct one fresh instance of each detection stage, in run order."""
    from ..font_forensics import FontForensicsStage
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
    ]


# job lifecycle states (distinct from a stage's per-row state)
QUEUED = "queued"
PROCESSING = "processing"
DONE = "done"
ERROR = "error"


@dataclass
class Job:
    """One uploaded document's analysis lifecycle (mutable, in-memory)."""

    job_id: str
    filename: str
    state: str = QUEUED
    # ordered per-stage progress: stage name -> queued/running/done/error
    stage_states: dict[str, str] = field(default_factory=dict)
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
