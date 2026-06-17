"""PHI-safe logging helpers for the aggregate / advisory layer.

These are the ONLY sanctioned way to put a finding into a structured log. They
emit descriptor fields only — never ``before`` / ``after`` text, ``reason``
strings (which may quote content), or the rich ``payload``. This realises
project invariant #10 ("Logging is PHI-safe").

Small, pure, and real (a safety primitive, not detection logic).
"""

from __future__ import annotations

import hashlib
from typing import Any

from .models import ADVISORY_FINDING_ALLOWLIST, AdvisoryFinding, AggregateFinding


def finding_log_record(finding: AggregateFinding | AdvisoryFinding) -> dict[str, Any]:
    """Return the allow-listed descriptor dict for ``finding``, safe to log.

    Includes ONLY the fields in :data:`ADVISORY_FINDING_ALLOWLIST`; ``bbox`` is
    flattened to a tuple. Enums are rendered to their ``.value``.
    """
    record: dict[str, Any] = {}
    for name in ADVISORY_FINDING_ALLOWLIST:
        value = getattr(finding, name, None)
        if name == "tier" and value is not None:
            value = getattr(value, "value", value)
        elif name == "bbox" and value is not None:
            value = (value.x0, value.y0, value.x1, value.y1)
        record[name] = value
    return record


def salted_hash(value: str, salt: str, *, length: int = 12) -> str:
    """Return ``sha256(salt + value)`` truncated to ``length`` hex chars.

    For the rare case a token reference must be correlated across logs without
    exposing it. Never log the raw ``value`` — log this instead.
    """
    digest = hashlib.sha256((salt + value).encode("utf-8")).hexdigest()
    return digest[:length]


__all__ = ["finding_log_record", "salted_hash"]
