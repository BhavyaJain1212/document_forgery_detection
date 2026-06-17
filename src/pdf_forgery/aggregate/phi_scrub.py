"""The PHI-scrub trust boundary.

:func:`to_advisory_input` projects a server-side :class:`AggregateResult` down to
an :class:`AdvisoryInput` of finding DESCRIPTORS only — the single object allowed
to cross toward the advisory LLM or the frontend. The projection is defined as an
explicit ALLOW-LIST (:data:`ADVISORY_FINDING_ALLOWLIST`), never a deny-list: a
field reaches the far side only because it was affirmatively cleared as non-PHI.

:func:`assert_advisory_safe` is the defensive post-check run before any egress
(and in tests): it raises if anything outside the allow-list is present, or any
descriptor string looks like leaked free-text content rather than a short
canonical token. See ``docs/STAGE6_DESIGN.md`` §2.
"""

from __future__ import annotations

import dataclasses

from .config import AggregateConfig
from .models import AdvisoryFinding, AdvisoryInput, AdvisoryStage, AggregateResult

#: Descriptor fields that must hold a short canonical token, never free text —
#: a leaked ``before``/``after``/``reason`` string would show up here as an
#: over-long or space-containing value.
_TOKEN_FIELDS = ("finding_id", "stage", "type", "token_class")
_MAX_TOKEN_LEN = 64


def to_advisory_input(
    aggregate: AggregateResult,
    config: AggregateConfig | None = None,
) -> AdvisoryInput:
    """Scrub an :class:`AggregateResult` into a boundary-crossing
    :class:`AdvisoryInput`.

    Copies ONLY the allow-listed descriptor fields from each finding into a fresh
    :class:`~pdf_forgery.aggregate.models.AdvisoryFinding`, and the headline +
    per-stage tiers/scores. Raw ``before``/``after`` text, ``reason``/``summary``
    strings, identifiers, and the rich ``payload`` are never carried.
    """
    stages = tuple(
        AdvisoryStage(stage=r.stage, tier=r.tier, score=r.score, ok=r.ok)
        for r in aggregate.stage_results
    )
    findings = tuple(
        AdvisoryFinding(
            finding_id=f.finding_id,
            stage=f.stage,
            type=f.type,
            tier=f.tier,
            score=f.score,
            token_class=f.token_class,
            page=f.page,
            bbox=f.bbox,
        )
        for f in aggregate.findings
    )
    advisory_input = AdvisoryInput(
        tier=aggregate.tier,
        score=aggregate.score,
        stages=stages,
        findings=findings,
        notes=aggregate.notes,
    )
    assert_advisory_safe(advisory_input)
    return advisory_input


def assert_advisory_safe(advisory_input: AdvisoryInput) -> None:
    """Raise if ``advisory_input`` carries anything outside the allow-list.

    Defensive egress check: walks the object and rejects any field not in
    :data:`ADVISORY_FINDING_ALLOWLIST` (plus the headline/per-stage descriptors)
    or any string that looks like leaked document content. Returns ``None`` when
    safe.
    """
    if not isinstance(advisory_input, AdvisoryInput):
        raise TypeError(f"expected AdvisoryInput, got {type(advisory_input).__name__}")

    _assert_no_extra_attributes(advisory_input, "AdvisoryInput")
    for stage in advisory_input.stages:
        _assert_no_extra_attributes(stage, "AdvisoryStage")
    for finding in advisory_input.findings:
        _assert_no_extra_attributes(finding, "AdvisoryFinding")
        _assert_field_set(finding, AdvisoryFinding, "AdvisoryFinding")
        for name in _TOKEN_FIELDS:
            value = getattr(finding, name)
            if value is not None:
                _assert_looks_like_token(value, f"AdvisoryFinding.{name}")


def _assert_no_extra_attributes(obj: object, label: str) -> None:
    """Reject any attribute on ``obj`` that is not a declared dataclass field.

    A frozen dataclass can still gain attributes via ``object.__setattr__``
    (it has no ``__slots__``); this catches a raw field smuggled in that way.
    """
    declared = {f.name for f in dataclasses.fields(obj)}
    actual = set(vars(obj).keys())
    extra = actual - declared
    if extra:
        raise ValueError(
            f"{label} carries field(s) outside the allow-list: {sorted(extra)}"
        )


def _assert_field_set(obj: object, expected_type: type, label: str) -> None:
    declared = {f.name for f in dataclasses.fields(obj)}
    expected = {f.name for f in dataclasses.fields(expected_type)}
    if declared != expected:
        raise ValueError(
            f"{label} field set {sorted(declared)} does not match the "
            f"allow-list {sorted(expected)}"
        )


def _assert_looks_like_token(value: str, label: str) -> None:
    if not isinstance(value, str):
        return
    if len(value) > _MAX_TOKEN_LEN or " " in value:
        raise ValueError(
            f"{label} value looks like leaked free-text content, not a "
            f"short canonical token: {value!r}"
        )


__all__ = ["to_advisory_input", "assert_advisory_safe"]
