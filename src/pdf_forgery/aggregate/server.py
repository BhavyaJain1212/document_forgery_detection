"""FastAPI app that serves the reviewer UI and the Stage 7 job API.

Thin transport layer only: it maps the framework-agnostic handlers in
:mod:`pdf_forgery.aggregate.api` onto HTTP routes, serializes the descriptor-only
dataclasses to JSON / SSE, and serves the static frontend. No detection or
fusion logic lives here.

The endpoints serve the SCRUBBED descriptor view (:class:`AdvisoryInput`) and the
advisory prose only — never raw extracted text (see ``docs/STAGE7_DESIGN.md`` §2).

Run it:  ``./.venv/bin/python -m pdf_forgery.aggregate.server``
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles

from . import api
from .api import AdvisoryEvent
from .models import AdvisoryInput, AdvisoryOutput, AdvisoryStage, BBox

_WEBAPP_DIR = Path(__file__).parent / "webapp"
_MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MB — generous for a claim PDF
_PDF_MAGIC = b"%PDF"
_JPEG_MAGIC = b"\xff\xd8\xff"
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_IMAGE_COMING_SOON = "Image forgery detection — implementation coming soon"

app = FastAPI(title="Claim Document Review", version="0.1.0")


# ---------------------------------------------------------------------------
# Serialization (descriptor-only dataclasses -> JSON-able dicts)
# ---------------------------------------------------------------------------

def _bbox_dict(bbox: BBox | None) -> dict | None:
    if bbox is None:
        return None
    return {"x0": bbox.x0, "y0": bbox.y0, "x1": bbox.x1, "y1": bbox.y1}


def _stage_dict(stage: AdvisoryStage) -> dict:
    return {
        "stage": stage.stage,
        "tier": stage.tier.value,
        "score": stage.score,
        "ok": stage.ok,
    }


def _advisory_input_dict(result: AdvisoryInput | None) -> dict | None:
    if result is None:
        return None
    return {
        "tier": result.tier.value,
        "score": result.score,
        "stages": [_stage_dict(s) for s in result.stages],
        "findings": [
            {
                "finding_id": f.finding_id,
                "stage": f.stage,
                "type": f.type,
                "tier": f.tier.value,
                "score": f.score,
                "token_class": f.token_class,
                "page": f.page,
                "bbox": _bbox_dict(f.bbox),
            }
            for f in result.findings
        ],
        "notes": list(result.notes),
    }


def _advisory_output_dict(output: AdvisoryOutput) -> dict:
    return {
        "summary": output.summary,
        "tier_statement": output.tier_statement,
        "finding_rationales": [
            {"finding_id": r.finding_id, "rationale": r.rationale}
            for r in output.finding_rationales
        ],
        "group_explanations": [
            {
                "finding_ids": list(g.finding_ids),
                "label": g.label,
                "what_we_found": g.what_we_found,
                "why_it_matters": g.why_it_matters,
                "what_to_check": g.what_to_check,
            }
            for g in output.group_explanations
        ],
        "model": output.model,
    }


def _sse(event: AdvisoryEvent) -> str:
    if event.event == "done" and event.output is not None:
        data = json.dumps(_advisory_output_dict(event.output))
    elif event.event == "error":
        data = json.dumps({"message": event.text})
    else:
        data = json.dumps({"text": event.text})
    return f"event: {event.event}\ndata: {data}\n\n"


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.post("/v1/documents")
async def post_document(file: UploadFile) -> JSONResponse:
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Empty file.")
    if len(raw) > _MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds the {_MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit.",
        )
    # Route by true format (magic bytes), never the extension (invariant #4).
    # A standalone image (JPEG/PNG) is detected but not yet analysed — image
    # forgery detection is not implemented yet, so we short-circuit with a
    # placeholder message rather than running the PDF pipeline over it.
    if raw.startswith(_JPEG_MAGIC) or raw.startswith(_PNG_MAGIC):
        return JSONResponse(
            status_code=200,
            content={"status": "unsupported", "message": _IMAGE_COMING_SOON},
        )
    if not raw.startswith(_PDF_MAGIC):
        raise HTTPException(
            status_code=415,
            detail="Unsupported file type. Please upload a PDF.",
        )
    submission = api.submit_document(raw, filename=file.filename or "document.pdf")
    return JSONResponse(
        status_code=202,
        content={"job_id": submission.job_id, "status_url": submission.status_url},
    )


@app.get("/v1/jobs/{job_id}")
def get_job(job_id: str) -> JSONResponse:
    try:
        status = api.get_job_status(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Unknown job.")
    return JSONResponse(
        {
            "job_id": status.job_id,
            "state": status.state,
            "stages": [
                {"stage": s.stage, "state": s.state} for s in status.stages
            ],
            "page_count": status.page_count,
            "result": _advisory_input_dict(status.result),
            "error": status.error,
        }
    )


@app.get("/v1/jobs/{job_id}/advisory")
def get_advisory(job_id: str) -> StreamingResponse:
    def event_stream():
        for event in api.stream_advisory(job_id):
            yield _sse(event)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/v1/jobs/{job_id}/findings/{finding_id}/overlay.png")
def get_finding_overlay(job_id: str, finding_id: str) -> Response:
    """GATED EVIDENCE: the annotated page PNG for one located finding.

    Returns real document pixels (PHI), distinct from the scrubbed descriptor
    endpoints. 404 when the finding is unknown, not localised, or rendering is
    unavailable.
    """
    png = api.get_finding_overlay(job_id, finding_id)
    if png is None:
        raise HTTPException(status_code=404, detail="No overlay for this finding.")
    return Response(content=png, media_type="image/png")


@app.get("/v1/jobs/{job_id}/pages/{page}/image.png")
def get_page_image(job_id: str, page: int) -> Response:
    """GATED EVIDENCE: a plain page image for the document viewer.

    Returns document pixels (PHI); the frontend overlays bounding boxes from the
    scrubbed ``bbox`` coordinates. 404 when the page is unknown or rendering is
    unavailable.
    """
    png = api.get_page_image(job_id, page)
    if png is None:
        raise HTTPException(status_code=404, detail="No page image available.")
    return Response(content=png, media_type="image/png")


# ---------------------------------------------------------------------------
# Frontend (static)
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def index() -> FileResponse:
    return FileResponse(_WEBAPP_DIR / "index.html")


app.mount("/static", StaticFiles(directory=_WEBAPP_DIR), name="static")


def main() -> None:
    import os

    import uvicorn

    engine = os.getenv("FDP_ADVISORY_ENGINE", "stub")
    model = os.getenv("FDP_ADVISORY_MODEL", "llama3.1")
    base_url = os.getenv("FDP_ADVISORY_BASE_URL", "http://localhost:11434")
    if engine != "stub":
        from .config import AggregateConfig
        api.configure_manager(
            AggregateConfig(
                advisory_engine=engine,
                advisory_model=model,
                advisory_base_url=base_url,
            )
        )

    uvicorn.run(app, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
