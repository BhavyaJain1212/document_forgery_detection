"""The HTTP contract the reviewer UI consumes, backed by the in-memory runner.

Defines the request/response SHAPES (dataclasses) and the framework-agnostic
handlers for the non-blocking job API in ``docs/STAGE7_DESIGN.md`` §4. The
handlers operate on a module-level :class:`~pdf_forgery.aggregate.jobs.JobManager`;
``server.py`` maps them onto FastAPI routes (and serializes the dataclasses to
JSON / SSE), so this module imports no web framework.

Critical: the status/result and advisory endpoints serve DESCRIPTORS + advisory
prose only (the :class:`AdvisoryInput` / :class:`AdvisoryOutput` shapes). Raw
extracted text never transits these endpoints — only the separate, gated,
audit-logged evidence endpoint (out of scope this slice).
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from dataclasses import dataclass

from .config import AggregateConfig
from .jobs import DONE, ERROR, STAGE_ORDER, JobManager
from .models import AdvisoryInput, AdvisoryOutput, FindingRationale


@dataclass(frozen=True)
class JobSubmission:
    """Response to ``POST /v1/documents`` — non-blocking (``202``)."""

    job_id: str
    status_url: str


@dataclass(frozen=True)
class StageProgress:
    """One stage's progress row for the processing view."""

    stage: str
    state: str
    """``queued`` / ``running`` / ``done`` / ``skipped`` / ``error``."""


@dataclass(frozen=True)
class JobStatus:
    """Response to ``GET /v1/jobs/{job_id}`` — progress + scrubbed result."""

    job_id: str
    state: str
    """``queued`` / ``processing`` / ``done`` / ``error``."""

    stages: tuple[StageProgress, ...] = ()
    page_count: int = 0
    """Number of document pages available through the gated image endpoint."""

    result: AdvisoryInput | None = None
    """The scrubbed (descriptor-only) result; present once ``state == "done"``."""

    error: str | None = None


@dataclass(frozen=True)
class AdvisoryEvent:
    """One SSE event on ``GET /v1/jobs/{job_id}/advisory``."""

    event: str
    """``chunk`` (partial text) or ``done`` (final :class:`AdvisoryOutput`)."""

    text: str = ""
    output: AdvisoryOutput | None = None


# ---------------------------------------------------------------------------
# Shared in-memory runner (the thin-slice stand-in for Celery/Redis)
# ---------------------------------------------------------------------------

_manager: JobManager | None = None


def get_manager() -> JobManager:
    """Return the process-wide :class:`JobManager`, creating it on first use."""
    global _manager
    if _manager is None:
        _manager = JobManager(AggregateConfig())
    return _manager


def configure_manager(config: AggregateConfig) -> JobManager:
    """(Re)create the shared manager with a specific config (used by the server)."""
    global _manager
    _manager = JobManager(config)
    return _manager


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def submit_document(pdf_bytes: bytes, *, filename: str | None = None) -> JobSubmission:
    """``POST /v1/documents``: enqueue analysis, return a ``202`` job handle.

    Never blocks on inference (project invariant #8) — returns immediately with a
    ``job_id`` the UI polls while the pipeline runs in the background.
    """
    job = get_manager().submit(pdf_bytes, filename or "document.pdf")
    return JobSubmission(job_id=job.job_id, status_url=f"/v1/jobs/{job.job_id}")


def get_job_status(job_id: str) -> JobStatus:
    """``GET /v1/jobs/{job_id}``: per-stage progress + the scrubbed result.

    Raises :class:`KeyError` for an unknown job (mapped to ``404`` by the server).
    """
    job = get_manager().get(job_id)
    if job is None:
        raise KeyError(job_id)
    stages = tuple(
        StageProgress(stage=name, state=job.stage_states.get(name, "queued"))
        for name in STAGE_ORDER
    )
    return JobStatus(
        job_id=job.job_id,
        state=job.state,
        stages=stages,
        page_count=job.page_count,
        result=job.advisory_input if job.state == DONE else None,
        error=job.error,
    )


def stream_advisory(job_id: str) -> Iterator[AdvisoryEvent]:
    """``GET /v1/jobs/{job_id}/advisory``: yield :class:`AdvisoryEvent`\\s (SSE).

    Generates the advisory (once, cached) for a finished job and streams it as
    ``chunk`` events followed by a final ``done`` event carrying the full
    :class:`AdvisoryOutput`. An unknown job, or one not yet finished/failed,
    yields a single error event rather than raising.
    """
    manager = get_manager()
    job = manager.get(job_id)
    if job is None:
        yield AdvisoryEvent(event="error", text="unknown job")
        return
    if job.state == ERROR:
        yield AdvisoryEvent(event="error", text=job.error or "analysis failed")
        return
    if job.state != DONE:
        yield AdvisoryEvent(event="error", text="advisory not ready")
        return

    output = manager.ensure_advisory(job_id)
    if output is None:
        yield AdvisoryEvent(event="error", text="advisory unavailable")
        return

    yield from _advisory_events(output)


def _advisory_events(
    output: AdvisoryOutput, *, chunk_delay: float = 0.04
) -> Iterator[AdvisoryEvent]:
    """Split an :class:`AdvisoryOutput` into streamed ``chunk`` events, then
    ``done``. The deterministic stub produces the whole text at once, so we chunk
    it word-by-word here to make the wait feel intentional (design §4)."""
    for word in _word_chunks(output.summary):
        yield AdvisoryEvent(event="chunk", text=word)
        if chunk_delay:
            time.sleep(chunk_delay)
    yield AdvisoryEvent(event="done", output=output)


def _word_chunks(text: str) -> Iterator[str]:
    """Yield ``text`` in small, render-friendly pieces preserving trailing space."""
    words = text.split(" ")
    for index, word in enumerate(words):
        yield word if index == len(words) - 1 else word + " "


def get_finding_overlay(job_id: str, finding_id: str) -> bytes | None:
    """``GET /v1/jobs/{job_id}/findings/{finding_id}/overlay.png`` (GATED EVIDENCE).

    Bake the annotated page PNG for one located finding. Unlike the status /
    advisory endpoints, this returns real document pixels (PHI) — it is the gated
    evidence path, not part of the scrubbed descriptor view. ``None`` when the
    finding is unknown / not localised / rendering unavailable (server -> 404).
    """
    return get_manager().render_overlay(job_id, finding_id)


def get_page_image(job_id: str, page_index: int) -> bytes | None:
    """``GET /v1/jobs/{job_id}/pages/{page}/image.png`` (GATED EVIDENCE).

    A plain page image for the document viewer; the frontend overlays the
    bounding boxes itself from the (already-scrubbed) ``bbox`` coordinates. Real
    document pixels (PHI), gated like :func:`get_finding_overlay`. ``None`` ->
    server 404.
    """
    return get_manager().render_page(job_id, page_index)


__all__ = [
    "JobSubmission",
    "StageProgress",
    "JobStatus",
    "AdvisoryEvent",
    "get_manager",
    "configure_manager",
    "submit_document",
    "get_job_status",
    "stream_advisory",
    "get_finding_overlay",
    "get_page_image",
]
