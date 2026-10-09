"""Closed-world validators for JSON and YAML configuration (SPEC §5.2, §6.1, §12). Every defect is a RunError."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from datetime import datetime
from typing import Any

from inspection_reconcile import grammar
from inspection_reconcile.errors import RunError

Check = Callable[[Any, str], Any]


def fail(where: str, message: str) -> RunError:
    return RunError("CONFIG_INVALID", f"{where}: {message}")


def obj(
    value: Any,
    where: str,
    required: Mapping[str, Check],
    optional: Mapping[str, Check] | None = None,
) -> dict[str, Any]:
    optional = optional or {}
    if not isinstance(value, dict):
        raise fail(where, "expected an object")
    for key in value:
        if not isinstance(key, str):
            raise fail(where, f"member name {key!r} must be a string (quote it)")
        if key not in required and key not in optional:
            raise fail(where, f"unknown member {key!r}")
    out: dict[str, Any] = {}
    for key, check in required.items():
        if key not in value:
            raise fail(where, f"missing member {key!r}")
        out[key] = check(value[key], f"{where}.{key}")
    for key, check in optional.items():
        if key in value:
            out[key] = check(value[key], f"{where}.{key}")
    return out


def string(value: Any, where: str) -> str:
    if not isinstance(value, str):
        raise fail(where, f"expected a string, got {type(value).__name__} (quote the value)")
    return value


def boolean(value: Any, where: str) -> bool:
    if not isinstance(value, bool):
        raise fail(where, "expected true or false")
    return value


def integer(lo: int, hi: int) -> Check:
    def check(value: Any, where: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise fail(where, "expected an integer")
        if not lo <= value <= hi:
            raise fail(where, f"expected an integer in [{lo}, {hi}]")
        return value

    return check


def _grammar(pred: Callable[[str], bool], label: str) -> Check:
    def check(value: Any, where: str) -> str:
        s = string(value, where)
        if not pred(s):
            raise fail(where, f"{s!r} violates grammar {label}")
        return s

    return check


ident = _grammar(grammar.is_id, "ID")
rev = _grammar(grammar.is_rev, "REV")
kind = _grammar(grammar.is_kind, "KIND")
text = _grammar(grammar.is_text, "TEXT")
digest = _grammar(grammar.is_digest, "DIGEST")


def timestamp(value: Any, where: str) -> datetime:
    s = string(value, where)
    ts = grammar.parse_ts(s)
    if ts is None:
        raise fail(where, f"{s!r} is not an RFC 3339 timestamp with an offset (SPEC §5.6)")
    return ts


def date_string(value: Any, where: str) -> str:
    s = string(value, where)
    if grammar.parse_date_midnight_utc(s) is None:
        raise fail(where, f"{s!r} is not a valid YYYY-MM-DD date")
    return s


def enum(values: Iterable[str]) -> Check:
    allowed = tuple(values)

    def check(value: Any, where: str) -> str:
        s = string(value, where)
        if s not in allowed:
            raise fail(where, f"{s!r} is not one of {', '.join(allowed)}")
        return s

    return check


def exact(expected: Any) -> Check:
    def check(value: Any, where: str) -> Any:
        if value != expected or type(value) is not type(expected):
            raise fail(where, f"expected {expected!r}")
        return value

    return check


def nullable(check: Check) -> Check:
    def wrapped(value: Any, where: str) -> Any:
        return None if value is None else check(value, where)

    return wrapped


def listof(item: Check, *, min_len: int = 0, unique: bool = False) -> Check:
    def check(value: Any, where: str) -> list[Any]:
        if not isinstance(value, list):
            raise fail(where, "expected a list")
        if len(value) < min_len:
            raise fail(where, f"expected at least {min_len} item(s)")
        out = [item(v, f"{where}[{i}]") for i, v in enumerate(value)]
        if unique:
            seen: set[Any] = set()
            for v in out:
                if v in seen:
                    raise fail(where, f"duplicate item {v!r}")
                seen.add(v)
        return out

    return check


def anything(value: Any, where: str) -> Any:
    return value


def single_segment_path(value: Any, where: str) -> str:
    s = string(value, where)
    if "/" in s or grammar.path_violation(s) is not None:
        raise fail(where, f"{s!r} must be a single safe path segment")
    return s
