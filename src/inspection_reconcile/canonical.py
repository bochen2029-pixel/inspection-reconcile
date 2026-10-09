"""Canonical JSON (RFC 8785 for a restricted value domain), digests and identities (SPEC §8)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from typing import Any

MAX_SAFE_INTEGER = 2**53 - 1
EVIDENCE_SET_SCHEME = "inspection-reconcile/evidence-set/v1"


class CanonicalError(ValueError):
    """A value outside the canonical domain of SPEC §8.1."""


def _check(value: Any, where: str) -> None:
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, int):
        if abs(value) > MAX_SAFE_INTEGER:
            raise CanonicalError(f"{where}: integer outside ±(2^53 - 1)")
        return
    if isinstance(value, str):
        if any(0xD800 <= ord(ch) <= 0xDFFF for ch in value):
            raise CanonicalError(f"{where}: lone surrogate in string")
        return
    if isinstance(value, list | tuple):
        for i, item in enumerate(value):
            _check(item, f"{where}[{i}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str) or not key.isascii():
                raise CanonicalError(f"{where}: object keys must be ASCII strings")
            _check(item, f"{where}.{key}")
        return
    raise CanonicalError(f"{where}: {type(value).__name__} is not allowed in a digested object")


def canonical(value: Any) -> bytes:
    """Canonical JSON bytes. Rejects floats, out-of-range integers, non-ASCII keys and lone surrogates."""
    _check(value, "$")
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def digest_ref(data: bytes) -> str:
    return "sha256:" + sha256_hex(data)


def digest_of(value: Any) -> str:
    return digest_ref(canonical(value))


def evidence_set_entries(entries: Iterable[Mapping[str, str]]) -> list[dict[str, str]]:
    items = [
        {
            "artifact_id": e["artifact_id"],
            "document_kind": e["document_kind"],
            "revision": e["revision"],
            "sha256": e["sha256"],
        }
        for e in entries
    ]
    items.sort(key=lambda e: (e["document_kind"], e["artifact_id"], e["revision"]))
    return items


def evidence_set_digest(entries: Iterable[Mapping[str, str]]) -> str:
    """SPEC §8.3 (scheme v1)."""
    return digest_of({"artifacts": evidence_set_entries(entries), "scheme": EVIDENCE_SET_SCHEME})


def sort_canonical(items: Iterable[Any]) -> list[Any]:
    """Sort a list by the canonical JSON of its elements (SPEC §8.4)."""
    keyed = [(canonical(item), item) for item in items]
    keyed.sort(key=lambda pair: pair[0])
    return [item for _, item in keyed]


def short(digest: str) -> str:
    """``sha256:`` plus 12 hex characters (SPEC §10)."""
    if digest.startswith("sha256:"):
        return digest[:19]
    return digest
