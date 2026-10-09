"""Value grammars (SPEC §5.6). Every pattern uses explicit ASCII classes: ``\\d`` would accept Unicode digits."""

from __future__ import annotations

import re
import unicodedata
from datetime import UTC, date, datetime, timedelta

from inspection_reconcile.vocab import ENUMS

ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
REV_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,31}")
KIND_RE = re.compile(r"[a-z][a-z0-9_]{0,63}")
DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")
TS_RE = re.compile(
    r"([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\.([0-9]{1,6}))?(Z|[+-][0-9]{2}:[0-9]{2})"
)
DATE_RE = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})")

FORBIDDEN_PATH_CHARS = frozenset('\\<>:"|?*')
RESERVED_NAMES = frozenset(
    ["CON", "PRN", "AUX", "NUL"] + [f"COM{i}" for i in range(1, 10)] + [f"LPT{i}" for i in range(1, 10)]
)
MAX_PATH = 240
MAX_SEGMENT = 100


def is_id(value: str) -> bool:
    return ID_RE.fullmatch(value) is not None


def is_rev(value: str) -> bool:
    return REV_RE.fullmatch(value) is not None


def is_kind(value: str) -> bool:
    return KIND_RE.fullmatch(value) is not None


def is_digest(value: str) -> bool:
    return DIGEST_RE.fullmatch(value) is not None


def _is_control(ch: str) -> bool:
    return unicodedata.category(ch) == "Cc"


def _is_surrogate(ch: str) -> bool:
    return 0xD800 <= ord(ch) <= 0xDFFF


def is_text(value: str) -> bool:
    if not 1 <= len(value) <= 200:
        return False
    return not any(_is_control(ch) or _is_surrogate(ch) for ch in value)


def parse_bool(value: str) -> bool | None:
    if value == "true":
        return True
    if value == "false":
        return False
    return None


def parse_ts(value: str) -> datetime | None:
    """Strict RFC 3339 subset: uppercase T/Z, at most 6 fractional digits, mandatory offset, no leap seconds."""
    m = TS_RE.fullmatch(value)
    if m is None:
        return None
    year, month, day, hour, minute, second = (int(m.group(i)) for i in range(1, 7))
    frac, offset = m.group(7), m.group(8)
    if hour > 23 or minute > 59 or second > 59:
        return None
    try:
        naive = datetime(year, month, day, hour, minute, second, int((frac or "0").ljust(6, "0")))
    except ValueError:
        return None
    if offset == "Z":
        delta = timedelta(0)
    else:
        sign = 1 if offset[0] == "+" else -1
        oh, om = int(offset[1:3]), int(offset[4:6])
        if oh > 23 or om > 59:
            return None
        delta = sign * timedelta(hours=oh, minutes=om)
    try:
        return (naive - delta).replace(tzinfo=UTC)
    except OverflowError:
        return None


def format_ts(value: datetime) -> str:
    """Canonical UTC form ``YYYY-MM-DDTHH:MM:SS.ffffffZ`` (SPEC §5.6)."""
    v = value.astimezone(UTC)
    return f"{v.year:04d}-{v.month:02d}-{v.day:02d}T{v.hour:02d}:{v.minute:02d}:{v.second:02d}.{v.microsecond:06d}Z"


def parse_date_midnight_utc(value: str) -> datetime | None:
    """``effective_from``: a YYYY-MM-DD string meaning midnight UTC (SPEC §6.1)."""
    m = DATE_RE.fullmatch(value)
    if m is None:
        return None
    try:
        d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None
    return datetime(d.year, d.month, d.day, tzinfo=UTC)


def path_violation(value: str) -> str | None:
    """Return the first violated PATH rule (``path:<rule>``), or None when the path is acceptable."""
    if value.startswith("/"):
        return "path:absolute"
    if len(value) > MAX_PATH:
        return "path:too-long"
    for ch in value:
        if _is_control(ch):
            return "path:control-character"
        if ch in FORBIDDEN_PATH_CHARS:
            return "path:forbidden-character"
    for segment in value.split("/"):
        if segment == "":
            return "path:empty-segment"
        if segment in (".", ".."):
            return "path:dot-segment"
        if segment.endswith((".", " ")):
            return "path:trailing-dot-or-space"
        if len(segment) > MAX_SEGMENT:
            return "path:segment-too-long"
        base = segment.split(".", 1)[0].rstrip(" ").upper()
        if base in RESERVED_NAMES:
            return "path:reserved-name"
    return None


CellValue = str | bool | None


def check_cell(type_name: str, raw: str | None, required: bool) -> tuple[CellValue, str | None]:
    """Validate one CSV cell. Returns (normalized value, violated rule or None)."""
    if raw is None:
        return None, ("required" if required else None)
    if type_name == "ID":
        return (raw, None) if is_id(raw) else (None, "grammar:ID")
    if type_name == "REV":
        return (raw, None) if is_rev(raw) else (None, "grammar:REV")
    if type_name == "KIND":
        return (raw, None) if is_kind(raw) else (None, "grammar:KIND")
    if type_name == "BOOL":
        b = parse_bool(raw)
        return (b, None) if b is not None else (None, "grammar:BOOL")
    if type_name == "TS":
        ts = parse_ts(raw)
        return (format_ts(ts), None) if ts is not None else (None, "grammar:TS")
    if type_name == "DIGEST":
        return (raw, None) if is_digest(raw) else (None, "grammar:DIGEST")
    if type_name == "TEXT":
        return (raw, None) if is_text(raw) else (None, "grammar:TEXT")
    if type_name == "PATH":
        rule = path_violation(raw)
        return (raw, None) if rule is None else (None, rule)
    if type_name.startswith("ENUM:"):
        values = ENUMS[type_name[5:]]
        return (raw, None) if raw in values else (None, "enum:" + "|".join(values))
    raise ValueError(f"unknown type {type_name}")
