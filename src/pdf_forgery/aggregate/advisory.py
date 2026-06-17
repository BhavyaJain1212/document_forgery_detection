"""The advisory model boundary.

The advisory LLM sits behind a swappable :class:`AdvisoryEngine` protocol —
exactly like Stage 3's ``OCREngine`` — so the GPU backend can be swapped,
queued, or replaced by the deterministic :class:`StubAdvisoryEngine` (no GPU, no
network) without touching callers.

GPU note: the local advisory model is the only GPU-bound part of this layer. It
contends with PaddleOCR (Stage 3) ONLY under concurrent load; in the normal
sequential pipeline OCR has released the GPU before advisory runs. When the
model/GPU is absent, ``is_available()`` is ``False`` and
:func:`generate_advisory` degrades to a templated fallback (never raises),
mirroring the project-wide "report and continue" rule. Never download model
weights in the sandbox.

``StubAdvisoryEngine`` and :func:`generate_advisory` are implemented (CPU only).
``LocalLLMAdvisoryEngine`` wraps Ollama — selected via the
``FDP_ADVISORY_ENGINE`` env var (see ``server.py::main``), absent by default so
no weights are ever pulled in the sandbox.
See ``docs/STAGE6_DESIGN.md`` §3 / §5.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from ..core.types import ConfidenceTier
from .config import AggregateConfig
from .glossary import get_glossary_entry
from .models import (
    AdvisoryInput,
    AdvisoryOutput,
    FindingGroup,
    FindingRationale,
    GroupExplanation,
)
from .prompts import build_advisory_messages


@dataclass(frozen=True)
class Message:
    """One chat message in the advisory prompt (``role`` = system / user)."""

    role: str
    content: str


@runtime_checkable
class AdvisoryEngine(Protocol):
    """A pluggable advisory backend that turns prompt messages into output."""

    name: str

    def generate(self, messages: list[Message]) -> AdvisoryOutput:
        """Produce an :class:`AdvisoryOutput` from prompt ``messages``.

        Must ground its output only in the supplied descriptors and never
        re-judge the verdict (the system prompt enforces this). Returns parseable
        output; a malformed model response degrades to a templated fallback
        rather than raising.
        """
        ...

    def is_available(self) -> bool:
        """Whether this engine can run here (model present, GPU reachable)."""
        ...


class StubAdvisoryEngine:
    """Deterministic, templated advisory — NO GPU, NO network.

    The default engine so the pipe runs end-to-end on any machine. Produces
    grouped explanations directly from the :class:`AdvisoryInput` descriptors
    (no prompt round-trip, no fragile text parsing).

    :func:`generate_advisory` detects this engine and calls
    :func:`_fallback_output` directly, bypassing the prompt path entirely so
    there is one single grouped templating path for both the stub and the
    fallback.
    """

    name = "stub"

    def is_available(self) -> bool:
        return True

    def generate(self, messages: list[Message]) -> AdvisoryOutput:
        # This path is not called by generate_advisory (which shortcuts to
        # _fallback_output for the stub).  Kept to satisfy the Protocol.
        raise NotImplementedError("stub engine bypasses the prompt in generate_advisory")


class LocalLLMAdvisoryEngine:
    """A local LLM behind the same interface, served by Ollama (swappable).

    Wraps Ollama's local HTTP API (default ``http://localhost:11434``):
    ``GET /api/tags`` backs ``is_available()`` (server reachable AND the
    configured model tag present), and ``POST /api/chat`` (non-streaming,
    ``format: json``) backs ``generate()``, parsing the JSON body per
    :data:`~pdf_forgery.aggregate.prompts.SYSTEM_PROMPT`'s schema into an
    :class:`AdvisoryOutput`. Chosen over vLLM for this project: already
    installed, and an 8B model server alongside Stage 3's PaddleOCR is tight on
    an 8GB GPU.

    Graceful absence (project-wide "report and continue"): when the server is
    unreachable or the model tag is not pulled, ``is_available()`` returns
    ``False`` and :func:`generate_advisory` falls back to the templated stub.
    **Never download model weights in the sandbox** — pull the tag out of band.
    """

    name = "local_llm"

    def __init__(
        self,
        model_name: str = "llama3.1",
        *,
        base_url: str = "http://localhost:11434",
        timeout: float = 60.0,
    ) -> None:
        self._model_name = model_name
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def is_available(self) -> bool:
        try:
            tags = self._get_json("/api/tags", timeout=3.0)
        except Exception:
            return False
        models = {m.get("name", "") for m in tags.get("models", [])}
        return any(
            name == self._model_name or name.split(":", 1)[0] == self._model_name
            for name in models
        )

    def generate(self, messages: list[Message]) -> AdvisoryOutput:
        body = json.dumps(
            {
                "model": self._model_name,
                "messages": [{"role": m.role, "content": m.content} for m in messages],
                "stream": False,
                "format": "json",
                "options": {"temperature": 0.0},
            }
        ).encode("utf-8")
        response = self._post_json("/api/chat", body, timeout=self._timeout)
        content = response.get("message", {}).get("content", "")
        return _parse_model_json(content, model=f"{self.name}:{self._model_name}")

    # -- HTTP helpers (stdlib only; fully local) ----------------------------

    def _get_json(self, path: str, *, timeout: float) -> dict:
        import urllib.request

        with urllib.request.urlopen(self._base_url + path, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _post_json(self, path: str, body: bytes, *, timeout: float) -> dict:
        import urllib.request

        req = urllib.request.Request(
            self._base_url + path,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))


def generate_advisory(
    advisory_input: AdvisoryInput,
    engine: AdvisoryEngine | None = None,
    config: AggregateConfig | None = None,
) -> AdvisoryOutput:
    """Build the prompt, run ``engine``, validate, and return the advisory.

    For :class:`StubAdvisoryEngine` (the default), bypasses the prompt
    round-trip entirely and calls :func:`_fallback_output` directly — one
    grouped templating path, no fragile text parsing.

    For other engines, validates that every cited ``finding_id`` inside
    ``group_explanations`` exists in ``advisory_input`` and degrades to a
    templated fallback (never raises) when the engine is unavailable or returns
    malformed output.
    """
    cfg = config or AggregateConfig()

    if not cfg.advisory_enabled:
        return _fallback_output(advisory_input, model="disabled")

    eng = engine if engine is not None else _default_engine(cfg)

    if not eng.is_available():
        return _fallback_output(advisory_input, model=f"{eng.name} (unavailable)")

    # Stub takes the direct path — no prompt round-trip, no re-parsing.
    if isinstance(eng, StubAdvisoryEngine):
        return _fallback_output(advisory_input, model=eng.name)

    messages = build_advisory_messages(advisory_input, cfg)
    try:
        output = eng.generate(messages)
    except Exception:
        return _fallback_output(advisory_input, model=f"{eng.name} (error)")

    # Validate: every cited finding_id must exist in the input.
    valid_ids = {f.finding_id for f in advisory_input.findings}
    cited_ids = {fid for g in output.group_explanations for fid in g.finding_ids}
    cited_ids |= {r.finding_id for r in output.finding_rationales}  # legacy
    if not isinstance(output, AdvisoryOutput) or not cited_ids.issubset(valid_ids):
        return _fallback_output(advisory_input, model=f"{eng.name} (malformed)")

    return output


def _default_engine(cfg: AggregateConfig) -> "AdvisoryEngine":
    if cfg.advisory_engine == "local_llm":
        return LocalLLMAdvisoryEngine(
            cfg.advisory_model, base_url=cfg.advisory_base_url
        )
    return StubAdvisoryEngine()


def _parse_model_json(content: str, model: str) -> AdvisoryOutput:
    """Parse an advisory model's JSON response into an :class:`AdvisoryOutput`.

    Raises on malformed JSON / missing keys; :func:`generate_advisory` catches
    that and degrades to the templated fallback.
    """
    data = json.loads(content)
    group_explanations = tuple(
        GroupExplanation(
            finding_ids=tuple(str(fid) for fid in g["finding_ids"]),
            label=str(g.get("label", "")),
            what_we_found=str(g.get("what_we_found", "")),
            why_it_matters=str(g.get("why_it_matters", "")),
            what_to_check=str(g.get("what_to_check", "")),
        )
        for g in data.get("group_explanations", [])
    )
    # Legacy field: may be present from older model versions.
    rationales = tuple(
        FindingRationale(
            finding_id=str(r["finding_id"]),
            rationale=str(r["rationale"]),
        )
        for r in data.get("finding_rationales", [])
    )
    return AdvisoryOutput(
        summary=str(data["summary"]),
        tier_statement=str(data["tier_statement"]),
        finding_rationales=rationales,
        group_explanations=group_explanations,
        model=model,
    )


def _fallback_output(advisory_input: AdvisoryInput, model: str) -> AdvisoryOutput:
    """Build grouped advisory directly from ``advisory_input`` (no prompt round-trip).

    This is the single templating path used by the stub engine AND by the
    fallback on unavailable/error/malformed LLM responses.
    """
    groups = _group_findings(advisory_input)
    return _render_advisory(advisory_input, groups, model)


# ---------------------------------------------------------------------------
# Finding grouping (deterministic, PHI-safe)
# ---------------------------------------------------------------------------

_TIER_SEVERITY: dict[ConfidenceTier, int] = {
    ConfidenceTier.INCONCLUSIVE: 0,
    ConfidenceTier.LOW: 1,
    ConfidenceTier.MEDIUM: 2,
    ConfidenceTier.HIGH: 3,
}


def _group_findings(advisory_input: AdvisoryInput) -> list[FindingGroup]:
    """Collapse ``advisory_input.findings`` by ``(stage, type, token_class)`` key.

    Each group carries the worst-case tier (escalation, not averaging) and
    the sorted unique set of pages across its members.
    """
    buckets: dict[tuple, list] = defaultdict(list)
    for f in advisory_input.findings:
        key = (f.stage, f.type, f.token_class)
        buckets[key].append(f)

    groups: list[FindingGroup] = []
    for (stage, type_, token_class), members in buckets.items():
        max_tier = max(members, key=lambda f: _TIER_SEVERITY.get(f.tier, 0)).tier
        pages = tuple(sorted({f.page for f in members if f.page is not None}))
        finding_ids = tuple(f.finding_id for f in members)
        groups.append(
            FindingGroup(
                stage=stage,
                type=type_,
                token_class=token_class,
                tier=max_tier,
                count=len(members),
                pages=pages,
                finding_ids=finding_ids,
            )
        )
    return groups


# ---------------------------------------------------------------------------
# Group-based templating (stub + fallback share the same path)
# ---------------------------------------------------------------------------

_TIER_STATEMENTS = {
    "inconclusive": (
        "These automated methods could not assess this document (INCONCLUSIVE)"
        " — that is not the same as clean; a reviewer should examine it manually."
    ),
    "low": (
        "Overall confidence is LOW (score {score}) — no substantive evidence of"
        " tampering was found by these methods."
    ),
    "medium": (
        "Overall confidence is MEDIUM (score {score}) — some signals warrant a"
        " closer review, but are not conclusive on their own."
    ),
    "high": (
        "Overall confidence is HIGH (score {score}) — these methods found"
        " strong evidence of an edit; a reviewer should confirm."
    ),
}


def _render_advisory(
    advisory_input: AdvisoryInput,
    groups: list[FindingGroup],
    model: str,
) -> AdvisoryOutput:
    """Produce a grouped :class:`AdvisoryOutput` from ``advisory_input`` and ``groups``."""
    tier_value = advisory_input.tier.value
    score_text = "n/a" if advisory_input.score is None else str(advisory_input.score)

    tier_statement = _TIER_STATEMENTS.get(
        tier_value, _TIER_STATEMENTS["inconclusive"]
    ).format(score=score_text)

    if not groups:
        summary = (
            f"No findings were flagged. {tier_statement}"
            " A reviewer makes the final decision."
        )
        return AdvisoryOutput(
            summary=summary,
            tier_statement=tier_statement,
            finding_rationales=(),
            group_explanations=(),
            model=model,
        )

    # Summary: synthesize across all groups into a 2-4 sentence narrative.
    stages_seen = sorted({g.stage for g in groups})
    group_desc_parts = []
    for g in groups:
        meaning, _ = get_glossary_entry(g.type)
        token_desc = f" in {g.token_class} fields" if g.token_class else ""
        count_desc = f"{g.count}×" if g.count > 1 else "once"
        pages_str = _format_pages(g.pages)
        loc = f", {pages_str}" if pages_str else ""
        group_desc_parts.append(
            f"{_humanize_type(g.type)}{token_desc} ({count_desc}{loc})"
        )

    n = len(groups)
    noun = "group" if n == 1 else "groups"
    summary = (
        f"{n} finding {noun} across {', '.join(stages_seen)}: "
        f"{'; '.join(group_desc_parts)}. "
        f"{tier_statement} "
        "A reviewer should examine the cited findings before deciding."
    )

    # Per-group explanations.
    group_explanations: list[GroupExplanation] = []
    for g in groups:
        meaning, implication = get_glossary_entry(g.type)
        token_desc = f" ({g.token_class} fields)" if g.token_class else ""
        pages_str = _format_pages(g.pages)
        count_str = str(g.count) if g.count > 1 else "one"

        label = (
            f"{_humanize_type(g.type)}{token_desc}, {g.count}×"
            + (f", {pages_str}" if pages_str else "")
        )
        what_we_found = (
            f"{meaning} Found {count_str} instance{'s' if g.count != 1 else ''}"
            + (f" across {pages_str}" if pages_str else "")
            + "."
        )
        group_explanations.append(
            GroupExplanation(
                finding_ids=g.finding_ids,
                label=label,
                what_we_found=what_we_found,
                why_it_matters=implication,
                what_to_check=_what_to_check(g),
            )
        )

    return AdvisoryOutput(
        summary=summary,
        tier_statement=tier_statement,
        finding_rationales=(),  # deprecated; groups are the primary output
        group_explanations=tuple(group_explanations),
        model=model,
    )


def _format_pages(pages: tuple[int, ...]) -> str:
    if not pages:
        return ""
    if len(pages) == 1:
        return f"page {pages[0] + 1}"
    return f"pages {pages[0] + 1}–{pages[-1] + 1}"


def _humanize_type(type_token: str) -> str:
    return type_token.replace("_", " ").capitalize()


def _what_to_check(group: FindingGroup) -> str:
    stage = group.stage
    type_ = group.type
    pages_str = _format_pages(group.pages)
    page_ref = f" on {pages_str}" if pages_str else ""

    if stage == "ocr_crosscheck":
        if type_ == "embedded_only":
            return (
                f"Inspect the rendered page{page_ref} visually and compare against"
                " the PDF text layer; look for white-on-white text or overlay elements."
            )
        if type_ == "ocr_only":
            return (
                f"Compare the image content{page_ref} against the PDF text layer;"
                " check whether an image patch covers original text."
            )
        return (
            f"Compare the PDF text layer and the rendered image{page_ref};"
            " confirm which version reflects the true document content."
        )
    if stage == "revision_recovery":
        return (
            f"Review the before/after evidence for revision changes{page_ref};"
            " confirm the change is authorized."
        )
    if stage == "font_forensics":
        return (
            f"Inspect the flagged tokens{page_ref} for font inconsistencies;"
            " compare with the surrounding text."
        )
    if stage == "invoice_arithmetic":
        return (
            f"Re-verify the arithmetic{page_ref} manually; confirm against"
            " source documents (purchase orders, receipts)."
        )
    if stage == "provenance_metadata":
        return (
            "Check the document's metadata (creation date, modification date,"
            " producer) against the expected workflow."
        )
    return "Review this finding in the context of the other detector results."


__all__ = [
    "Message",
    "AdvisoryEngine",
    "StubAdvisoryEngine",
    "LocalLLMAdvisoryEngine",
    "generate_advisory",
    "_group_findings",
    "_render_advisory",
]
