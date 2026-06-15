"""Parse and compare PDF date strings (read-only, shallow).

PDF dates look like ``D:YYYYMMDDHHmmSSOHH'mm'`` (PDF 32000-1 §7.9.4). We parse to
an aware/naive ``datetime`` for comparison; partial dates (only a year, etc.) are
tolerated. Anything unparseable yields ``None`` and is simply not compared.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

_DATE_RE = re.compile(
    r"""^D?:?\s*
        (?P<year>\d{4})
        (?P<month>\d{2})?
        (?P<day>\d{2})?
        (?P<hour>\d{2})?
        (?P<minute>\d{2})?
        (?P<second>\d{2})?
        (?P<tz>[Zz]|[+\-]\d{2}'?\d{0,2}'?)?
    """,
    re.VERBOSE,
)


def parse_pdf_date(value: str | None) -> datetime | None:
    """Parse a PDF date string to a ``datetime`` (UTC-normalised), or ``None``."""
    if not value:
        return None
    m = _DATE_RE.match(value.strip())
    if not m:
        return None
    try:
        year = int(m.group("year"))
        month = int(m.group("month") or 1)
        day = int(m.group("day") or 1)
        hour = int(m.group("hour") or 0)
        minute = int(m.group("minute") or 0)
        second = int(m.group("second") or 0)
        dt = datetime(year, month, day, hour, minute, second)
    except (ValueError, TypeError):
        return None

    tz = m.group("tz")
    offset = _parse_offset(tz)
    if offset is not None:
        dt = dt.replace(tzinfo=timezone(offset))
        return dt.astimezone(timezone.utc)
    # Naive: treat as UTC for a stable, comparable instant.
    return dt.replace(tzinfo=timezone.utc)


def _parse_offset(tz: str | None) -> timedelta | None:
    if not tz:
        return None
    if tz in ("Z", "z"):
        return timedelta(0)
    sign = 1 if tz[0] == "+" else -1
    digits = re.sub(r"[^\d]", "", tz[1:])
    if len(digits) < 2:
        return None
    hours = int(digits[:2])
    minutes = int(digits[2:4]) if len(digits) >= 4 else 0
    return sign * timedelta(hours=hours, minutes=minutes)


def mod_after_creation(creation: str | None, mod: str | None) -> bool:
    """True when both dates parse and ModDate is strictly after CreationDate."""
    c = parse_pdf_date(creation)
    m = parse_pdf_date(mod)
    if c is None or m is None:
        return False
    return m > c
