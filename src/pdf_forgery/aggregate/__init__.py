"""Stage 7 — aggregation + PHI-scrub boundary + advisory + UI (thin slice).

Assembly / presentation layer that runs AFTER the detection pipeline. It rolls
the per-stage :class:`~pdf_forgery.core.types.StageResult`\\s up into one
:class:`AggregateResult` (headline via the existing :func:`pdf_forgery.fusion.fuse`,
plus a flat, overlay-ready finding list carrying ``bbox``), scrubs that down to a
descriptor-only :class:`AdvisoryInput` at the single PHI trust boundary, and runs
a swappable advisory LLM to explain the result as decision support.

7.1 (this slice): ``aggregate()``, the PHI-scrub boundary, prompt assembly, and
``StubAdvisoryEngine`` are implemented and CPU-only. 7.2 (not yet implemented):
``LocalLLMAdvisoryEngine``, the FastAPI handlers in ``api.py``, and pipeline
wiring. Full design: ``docs/STAGE7_DESIGN.md``.
"""

from __future__ import annotations

from .advisory import (
    AdvisoryEngine,
    LocalLLMAdvisoryEngine,
    Message,
    StubAdvisoryEngine,
    generate_advisory,
)
from .aggregate import aggregate
from .config import AggregateConfig
from .models import (
    ADVISORY_FINDING_ALLOWLIST,
    AdvisoryFinding,
    AdvisoryInput,
    AdvisoryOutput,
    AdvisoryStage,
    AggregateFinding,
    AggregateResult,
    BBox,
    FindingGroup,
    FindingRationale,
    GroupExplanation,
)
from .phi_scrub import assert_advisory_safe, to_advisory_input
from .prompts import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE, build_advisory_messages
from .safe_log import finding_log_record, salted_hash

__all__ = [
    # aggregate
    "aggregate",
    "AggregateConfig",
    "AggregateResult",
    "AggregateFinding",
    "BBox",
    # PHI boundary
    "to_advisory_input",
    "assert_advisory_safe",
    "ADVISORY_FINDING_ALLOWLIST",
    "AdvisoryInput",
    "AdvisoryFinding",
    "AdvisoryStage",
    # advisory
    "AdvisoryEngine",
    "StubAdvisoryEngine",
    "LocalLLMAdvisoryEngine",
    "Message",
    "generate_advisory",
    "AdvisoryOutput",
    "FindingGroup",
    "FindingRationale",
    "GroupExplanation",
    "SYSTEM_PROMPT",
    "USER_PROMPT_TEMPLATE",
    "build_advisory_messages",
    # logging
    "finding_log_record",
    "salted_hash",
]
