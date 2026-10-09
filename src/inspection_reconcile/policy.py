"""Requirement pack loading and validation (SPEC §6)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from inspection_reconcile import validate as v
from inspection_reconcile.canonical import digest_of
from inspection_reconcile.errors import RunError
from inspection_reconcile.grammar import is_kind, parse_date_midnight_utc
from inspection_reconcile.vocab import BINDINGS, CHECKS
from inspection_reconcile.yamlsafe import loads_yaml

POLICY_SCHEMA = "inspection-reconcile/policy/v1"


@dataclass(frozen=True)
class Policy:
    pack_id: str
    version: str
    effective_from: str
    effective_from_dt: datetime
    synthetic: bool
    project_id: str
    scope_revision: str
    accepted_bases: tuple[frozenset[str], ...]
    requirements: dict[str, tuple[str, ...]]
    required_binding: str
    max_file_bytes: int
    sha256: str
    raw_bytes_sha256: str
    raw_bytes_size: int = 0


def _basis_token(value: Any, where: str) -> str:
    s = v.string(value, where)
    if not is_kind(s):
        raise v.fail(where, f"{s!r} is not a basis token")
    return s


def _requirements(value: Any, where: str) -> dict[str, tuple[str, ...]]:
    if not isinstance(value, dict):
        raise v.fail(where, "expected an object")
    out: dict[str, tuple[str, ...]] = {}
    for activity, spec in value.items():
        if not isinstance(activity, str) or not is_kind(activity):
            raise v.fail(where, f"requirement key {activity!r} must be a KIND string")
        body = v.obj(
            spec,
            f"{where}.{activity}",
            {"document_kinds": v.listof(v.kind, min_len=1, unique=True)},
        )
        out[activity] = tuple(body["document_kinds"])
    return out


def _checks(value: Any, where: str) -> list[dict[str, Any]]:
    expected = [{"id": cid, "type": ctype, "required": req} for cid, ctype, req in CHECKS]
    if value != expected or any(type(c.get("required")) is not bool for c in value if isinstance(c, dict)):
        raise v.fail(where, "checks must list exactly R0..R7 with the types and required values of SPEC §6.1")
    return expected


def parse_policy(raw: Any, source: str, raw_bytes_sha256: str = "", raw_bytes_size: int = 0) -> Policy:
    body = v.obj(
        raw,
        source,
        {
            "schema": v.exact(POLICY_SCHEMA),
            "pack_id": v.ident,
            "version": v.string,
            "effective_from": v.date_string,
            "synthetic": v.boolean,
            "project_id": v.ident,
            "scope_revision": v.rev,
            "coverage": lambda x, w: v.obj(
                x,
                w,
                {"accepted_bases": v.listof(v.listof(_basis_token, min_len=1, unique=True), min_len=1)},
            ),
            "requirements": _requirements,
            "approval": lambda x, w: v.obj(x, w, {"required_binding": v.enum(BINDINGS)}),
            "limits": lambda x, w: v.obj(x, w, {"max_file_bytes": v.integer(1, 2**31 - 1)}),
            "checks": _checks,
        },
    )
    effective = parse_date_midnight_utc(body["effective_from"])
    assert effective is not None  # validated above
    return Policy(
        pack_id=body["pack_id"],
        version=body["version"],
        effective_from=body["effective_from"],
        effective_from_dt=effective,
        synthetic=body["synthetic"],
        project_id=body["project_id"],
        scope_revision=body["scope_revision"],
        accepted_bases=tuple(frozenset(s) for s in body["coverage"]["accepted_bases"]),
        requirements=body["requirements"],
        required_binding=body["approval"]["required_binding"],
        max_file_bytes=body["limits"]["max_file_bytes"],
        sha256=digest_of(raw),
        raw_bytes_sha256=raw_bytes_sha256,
        raw_bytes_size=raw_bytes_size,
    )


def load_policy(path: Path) -> Policy:
    from inspection_reconcile.canonical import sha256_hex

    try:
        raw_bytes = path.read_bytes()
    except OSError as exc:
        raise RunError("POLICY_UNREADABLE", f"{path.as_posix()}: {exc.strerror or exc}") from exc
    # One read: the digest and size recorded in the run manifest are those of the bytes that were parsed.
    try:
        text = raw_bytes.decode("utf-8-sig")  # strips a leading byte-order mark
    except UnicodeDecodeError as exc:
        raise RunError("CONFIG_INVALID", f"{path.as_posix()}: not valid UTF-8") from exc
    raw = loads_yaml(text, path.as_posix())
    try:
        return parse_policy(raw, path.name, sha256_hex(raw_bytes), len(raw_bytes))
    except ValueError as exc:  # CanonicalError from digest_of: e.g. floats in the document
        raise RunError("CONFIG_INVALID", f"{path.name}: {exc}") from exc
