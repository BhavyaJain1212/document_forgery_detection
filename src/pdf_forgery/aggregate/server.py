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


def _wrap_image_as_pdf(raw: bytes) -> bytes:
    """Embed a standalone JPEG or PNG into a single-page PDF.

    JPEG bytes are stored verbatim with DCTDecode — no re-encode — so
    ELA / double-JPEG see the original quantisation history.  PNG is decoded
    to raw RGB/gray pixels (JPEG-specific methods are not applicable anyway).
    Raises on failure (caller handles it).
    """
    from io import BytesIO as _BytesIO

    import pikepdf
    from PIL import Image
    from pikepdf import Array, Dictionary, Name, Pdf

    img = Image.open(_BytesIO(raw))
    w, h = img.size
    is_jpeg = raw[:3] == _JPEG_MAGIC

    pdf = Pdf.new()

    if is_jpeg:
        mode = img.mode
        if mode in ("RGB", "RGBA"):
            cs = Name.DeviceRGB
        elif mode == "L":
            cs = Name.DeviceGray
        elif mode == "CMYK":
            cs = Name.DeviceCMYK
        else:
            cs = Name.DeviceRGB
        img_stream = pdf.make_stream(raw)
        img_stream.Type = Name.XObject
        img_stream.Subtype = Name.Image
        img_stream.Width = w
        img_stream.Height = h
        img_stream.ColorSpace = cs
        img_stream.BitsPerComponent = 8
        img_stream.Filter = Name.DCTDecode
    else:
        # PNG: decode to raw pixels; JPEG-specific methods are inapplicable.
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        cs = Name.DeviceRGB if img.mode == "RGB" else Name.DeviceGray
        img_stream = pdf.make_stream(img.tobytes())
        img_stream.Type = Name.XObject
        img_stream.Subtype = Name.Image
        img_stream.Width = w
        img_stream.Height = h
        img_stream.ColorSpace = cs
        img_stream.BitsPerComponent = 8

    page_w, page_h = float(w), float(h)
    content = pdf.make_stream(
        f"q {page_w} 0 0 {page_h} 0 0 cm /Im0 Do Q".encode("latin-1")
    )
    page = pdf.make_indirect(
        Dictionary(
            Type=Name.Page,
            MediaBox=Array([0, 0, page_w, page_h]),
            Contents=content,
            Resources=Dictionary(XObject=Dictionary(Im0=img_stream)),
        )
    )
    pdf.Root.Pages.Kids.append(page)
    pdf.Root.Pages.Count = len(pdf.Root.Pages.Kids)
    buf = _BytesIO()
    pdf.save(buf)
    return buf.getvalue()


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
    if raw.startswith(_PDF_MAGIC):
        pdf_bytes = raw
    elif raw.startswith(_JPEG_MAGIC) or raw.startswith(_PNG_MAGIC):
        try:
            pdf_bytes = _wrap_image_as_pdf(raw)
        except Exception as exc:
            raise HTTPException(
                status_code=422,
                detail=f"Could not process image file: {exc}",
            )
    else:
        raise HTTPException(
            status_code=415,
            detail="Unsupported file type. Please upload a PDF, JPEG, or PNG.",
        )
    submission = api.submit_document(pdf_bytes, filename=file.filename or "document.pdf")
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
