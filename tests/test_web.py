"""Web layer (Stage 7.2): job manager, API handlers, FastAPI server, SSE.

These are kept fast by stubbing the detection stages — the real five-stage
pipeline (PaddleOCR etc.) is exercised by the acceptance tests, not here. The
focus is the transport + the PHI boundary: that the served payloads are the
scrubbed descriptor view and never carry raw before/after text.
"""

from __future__ import annotations

import json
import time

import pytest

from pdf_forgery.aggregate import api, jobs, server
from pdf_forgery.aggregate.config import AggregateConfig
from pdf_forgery.core.types import ConfidenceTier, Finding, StageResult

_MINIMAL_PDF = b"%PDF-1.4\n%%EOF\n"


class _FakeStage:
    """A trivial Stage that returns a canned result (no real analysis)."""

    def __init__(self, result: StageResult) -> None:
        self.name = result.stage
        self._result = result

    def run(self, pdf_bytes, ctx) -> StageResult:  # noqa: ANN001 - test stub
        return self._result


def _fake_results() -> list[StageResult]:
    high = StageResult(
        stage="invoice_arithmetic",
        tier=ConfidenceTier.HIGH,
        score=85,
        findings=(
            Finding(
                stage="invoice_arithmetic",
                tier=ConfidenceTier.HIGH,
                reason="a total does not reconcile",
                page=1,
                before="37004.49",  # raw text — MUST NOT cross the boundary
                after="374.49",
                high_value="amount",
            ),
        ),
        summary="one broken equation",
    )
    benign = StageResult(
        stage="provenance_metadata",
        tier=ConfidenceTier.LOW,
        score=10,
        findings=(),
        summary="nothing notable",
    )
    return [high, benign]


@pytest.fixture
def stub_pipeline(monkeypatch):
    """Point the job manager's stage builder at fast canned stages."""
    results = _fake_results()
    monkeypatch.setattr(
        jobs, "build_default_stages", lambda: [_FakeStage(r) for r in results]
    )
    # Fresh manager per test so jobs do not leak across tests.
    api.configure_manager(AggregateConfig())
    yield results


def _run_to_done(job_id: str, manager) -> None:
    for _ in range(200):
        job = manager.get(job_id)
        if job is not None and job.state in (jobs.DONE, jobs.ERROR):
            return
        time.sleep(0.01)
    raise AssertionError("job did not finish in time")


def _blank_pdf(page_count: int = 1) -> bytes:
    """Small valid PDF used when page-count behavior matters."""
    import io

    import pikepdf

    pdf = pikepdf.Pdf.new()
    for _ in range(page_count):
        pdf.add_blank_page(page_size=(72, 72))
    buffer = io.BytesIO()
    pdf.save(buffer)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Job manager / API handlers
# ---------------------------------------------------------------------------

def test_submit_runs_pipeline_and_scrubs(stub_pipeline):
    manager = api.get_manager()
    submission = api.submit_document(_MINIMAL_PDF, filename="claim.pdf")
    assert submission.status_url.endswith(submission.job_id)

    _run_to_done(submission.job_id, manager)
    status = api.get_job_status(submission.job_id)

    assert status.state == jobs.DONE
    assert status.result is not None
    # Headline fused to HIGH (a substantive HIGH stage originates the verdict).
    assert status.result.tier == ConfidenceTier.HIGH
    # Per-stage progress is reported for every canonical stage row.
    assert [s.stage for s in status.stages] == list(jobs.STAGE_ORDER)


def test_served_result_carries_no_raw_text(stub_pipeline):
    """The PHI boundary: the served (scrubbed) result must not leak before/after."""
    manager = api.get_manager()
    submission = api.submit_document(_MINIMAL_PDF, filename="claim.pdf")
    _run_to_done(submission.job_id, manager)
    status = api.get_job_status(submission.job_id)

    served = json.dumps(server._advisory_input_dict(status.result))
    assert "37004.49" not in served
    assert "374.49" not in served
    # The token *class* is allowed (kind, not value).
    assert '"token_class": "amount"' in served


def test_job_status_exposes_non_phi_page_count(stub_pipeline):
    pytest.importorskip("pypdfium2")
    manager = api.get_manager()
    submission = api.submit_document(_blank_pdf(2), filename="two-pages.pdf")
    _run_to_done(submission.job_id, manager)
    status = json.loads(server.get_job(submission.job_id).body)

    assert status["page_count"] == 2
    assert isinstance(status["page_count"], int)
    assert "page_count" not in status["result"]  # response metadata, not PHI payload


def test_page_count_failure_is_graceful(stub_pipeline):
    manager = api.get_manager()
    submission = api.submit_document(_MINIMAL_PDF, filename="truncated.pdf")
    _run_to_done(submission.job_id, manager)
    assert api.get_job_status(submission.job_id).page_count == 0


def test_unknown_job_raises_keyerror(stub_pipeline):
    with pytest.raises(KeyError):
        api.get_job_status("does-not-exist")


def test_advisory_stream_cites_only_known_ids(stub_pipeline):
    manager = api.get_manager()
    submission = api.submit_document(_MINIMAL_PDF, filename="claim.pdf")
    _run_to_done(submission.job_id, manager)

    events = list(api.stream_advisory(submission.job_id))
    assert events[-1].event == "done"
    out = events[-1].output
    valid = {f.finding_id for f in api.get_job_status(submission.job_id).result.findings}
    # Primary: group_explanations cite only valid ids.
    all_cited = {fid for g in out.group_explanations for fid in g.finding_ids}
    assert all_cited.issubset(valid)
    assert any(e.event == "chunk" for e in events)


def test_advisory_stream_unknown_job_yields_error(stub_pipeline):
    events = list(api.stream_advisory("nope"))
    assert len(events) == 1 and events[0].event == "error"


# ---------------------------------------------------------------------------
# FastAPI server (transport + validation)
# ---------------------------------------------------------------------------

def test_server_rejects_non_pdf(stub_pipeline):
    from fastapi.testclient import TestClient

    client = TestClient(server.app)
    res = client.post(
        "/v1/documents", files={"file": ("x.pdf", b"not a pdf", "application/pdf")}
    )
    assert res.status_code == 415


def test_server_rejects_empty(stub_pipeline):
    from fastapi.testclient import TestClient

    client = TestClient(server.app)
    res = client.post("/v1/documents", files={"file": ("x.pdf", b"", "application/pdf")})
    assert res.status_code == 400


def test_server_full_flow_and_sse(stub_pipeline):
    from fastapi.testclient import TestClient

    client = TestClient(server.app)
    res = client.post(
        "/v1/documents", files={"file": ("claim.pdf", _MINIMAL_PDF, "application/pdf")}
    )
    assert res.status_code == 202
    job_id = res.json()["job_id"]

    status = {}
    for _ in range(200):
        status = client.get(f"/v1/jobs/{job_id}").json()
        if status["state"] in ("done", "error"):
            break
        time.sleep(0.01)
    assert status["state"] == "done"
    assert status["result"]["tier"] == "high"
    # bbox is part of the contract even though it is None this slice.
    assert all("bbox" in f for f in status["result"]["findings"])

    with client.stream("GET", f"/v1/jobs/{job_id}/advisory") as resp:
        assert resp.headers["content-type"].startswith("text/event-stream")
        body = "".join(resp.iter_text())
    assert "event: done" in body
    assert "event: chunk" in body
    # The done payload must carry group_explanations.
    import re as _re
    done_data = _re.search(r"event: done\ndata: (\{.*\})", body)
    assert done_data is not None
    done_json = json.loads(done_data.group(1))
    assert "group_explanations" in done_json


def test_server_unknown_job_404(stub_pipeline):
    from fastapi.testclient import TestClient

    client = TestClient(server.app)
    assert client.get("/v1/jobs/missing").status_code == 404


# ---------------------------------------------------------------------------
# Gated evidence: the annotated-overlay PNG (real revision_recovery stage)
# ---------------------------------------------------------------------------

@pytest.fixture
def real_revision_pipeline(monkeypatch):
    """Run ONLY the real revision_recovery stage, so findings carry geometry."""
    from pdf_forgery.revision_recovery import RevisionRecoveryStage

    monkeypatch.setattr(jobs, "build_default_stages", lambda: [RevisionRecoveryStage()])
    api.configure_manager(AggregateConfig())
    yield


def _forged_amount_pdf() -> bytes:
    import sys
    from pathlib import Path

    scripts = str(Path(__file__).resolve().parent.parent / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    import make_localization_fixtures as F

    return F.amount_pair()[1]


def _await_done(client, job_id: str) -> dict:
    status: dict = {}
    for _ in range(400):
        status = client.get(f"/v1/jobs/{job_id}").json()
        if status["state"] in ("done", "error"):
            return status
        time.sleep(0.01)
    raise AssertionError("job did not finish in time")


def test_bbox_crosses_boundary_but_document_pixels_are_gated(real_revision_pipeline):
    """The scrubbed descriptor carries the bbox (coordinates only); the document
    pixels (the annotated PNG) come ONLY from the separate gated endpoint."""
    pytest.importorskip("pypdfium2")
    from fastapi.testclient import TestClient

    client = TestClient(server.app)
    res = client.post(
        "/v1/documents",
        files={"file": ("claim.pdf", _forged_amount_pdf(), "application/pdf")},
    )
    assert res.status_code == 202
    job_id = res.json()["job_id"]
    status = _await_done(client, job_id)
    assert status["state"] == "done"

    boxed = [f for f in status["result"]["findings"] if f["bbox"] is not None]
    assert boxed, "expected a localized revision_recovery finding"
    # No document text crosses the scrub boundary, even though the bbox does.
    assert "50,000" not in json.dumps(status["result"])

    finding_id = boxed[0]["finding_id"]
    png = client.get(f"/v1/jobs/{job_id}/findings/{finding_id}/overlay.png")
    assert png.status_code == 200
    assert png.headers["content-type"] == "image/png"
    assert png.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_gated_page_image_serves_png(real_revision_pipeline):
    """The document viewer's plain page image (boxes drawn client-side from bbox)."""
    pytest.importorskip("pypdfium2")
    from fastapi.testclient import TestClient

    client = TestClient(server.app)
    job_id = client.post(
        "/v1/documents",
        files={"file": ("c.pdf", _forged_amount_pdf(), "application/pdf")},
    ).json()["job_id"]
    _await_done(client, job_id)

    res = client.get(f"/v1/jobs/{job_id}/pages/0/image.png")
    assert res.status_code == 200
    assert res.headers["content-type"] == "image/png"
    assert res.content[:8] == b"\x89PNG\r\n\x1a\n"
    # Unknown job -> 404.
    assert client.get("/v1/jobs/missing/pages/0/image.png").status_code == 404


def test_gated_overlay_unknown_finding_and_job_404(real_revision_pipeline):
    from fastapi.testclient import TestClient

    client = TestClient(server.app)
    job_id = client.post(
        "/v1/documents",
        files={"file": ("c.pdf", _forged_amount_pdf(), "application/pdf")},
    ).json()["job_id"]
    _await_done(client, job_id)

    assert (
        client.get(f"/v1/jobs/{job_id}/findings/revision_recovery-999/overlay.png").status_code
        == 404
    )
    assert (
        client.get("/v1/jobs/missing/findings/revision_recovery-0/overlay.png").status_code
        == 404
    )


# ---------------------------------------------------------------------------
# Image upload (JPEG / PNG) — wrapped into a minimal PDF for the pipeline
# ---------------------------------------------------------------------------

def _make_jpeg_bytes() -> bytes:
    """Minimal valid JPEG for upload tests."""
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (16, 12), color=(128, 64, 32)).save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def _make_png_bytes() -> bytes:
    """Minimal valid PNG for upload tests."""
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (16, 12), color=(32, 64, 128)).save(buf, format="PNG")
    return buf.getvalue()


def test_jpeg_upload_short_circuits_coming_soon(stub_pipeline):
    """A JPEG is recognised but not analysed: image forgery detection is not
    implemented yet, so the server short-circuits with a placeholder message
    rather than creating a job."""
    from fastapi.testclient import TestClient

    client = TestClient(server.app)
    res = client.post(
        "/v1/documents",
        files={"file": ("scan.jpg", _make_jpeg_bytes(), "image/jpeg")},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "unsupported"
    assert "coming soon" in body["message"].lower()
    assert "job_id" not in body


def test_png_upload_short_circuits_coming_soon(stub_pipeline):
    """A PNG short-circuits with the same 'coming soon' placeholder."""
    from fastapi.testclient import TestClient

    client = TestClient(server.app)
    res = client.post(
        "/v1/documents",
        files={"file": ("scan.png", _make_png_bytes(), "image/png")},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "unsupported"
    assert "coming soon" in body["message"].lower()
    assert "job_id" not in body


def test_unsupported_format_still_rejected(stub_pipeline):
    """A TIFF or random bytes are still rejected with 415."""
    from fastapi.testclient import TestClient

    client = TestClient(server.app)
    res = client.post(
        "/v1/documents",
        files={"file": ("doc.tiff", b"II\x2a\x00random", "image/tiff")},
    )
    assert res.status_code == 415


def test_jpeg_magic_bytes_detected_regardless_of_extension(stub_pipeline):
    """Magic-byte routing is independent of the filename extension (invariant #4)."""
    from fastapi.testclient import TestClient

    client = TestClient(server.app)
    jpeg = _make_jpeg_bytes()
    res = client.post(
        "/v1/documents",
        files={"file": ("renamed.pdf", jpeg, "application/pdf")},
    )
    # JPEG masquerading as PDF: magic bytes are JPEG, so it's routed as an image
    # (short-circuited to the 'coming soon' placeholder), never run as a PDF.
    assert res.status_code == 200
    assert res.json()["status"] == "unsupported"
