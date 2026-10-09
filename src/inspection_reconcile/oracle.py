"""Oracle loading and comparison for the demo's self-check (SPEC §11, Appendix A.1).

The test suite has its own, separate reader (tests/oracle_util.py), so a defect here cannot hide a defect there.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from inspection_reconcile.engine.assess import Assessment
from inspection_reconcile.yamlsafe import load_yaml

_RANGE = re.compile(r"^(?P<prefix>.*O-)(?P<lo>[0-9]{3})\.\.(?P<hi>[0-9]{3})$")


def load_oracle(path: Path) -> dict[str, Any]:
    doc = load_yaml(path)
    if not isinstance(doc, dict) or not isinstance(doc.get("scenarios"), dict):
        raise ValueError(f"{path}: not an oracle file")
    return doc


def _expand(entries: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for e in entries:
        m = _RANGE.match(e["key"])
        subjects = (
            [(e["key"], None)]
            if m is None
            else [(f"{m['prefix']}{n:03d}", f"O-{n:03d}") for n in range(int(m["lo"]), int(m["hi"]) + 1)]
        )
        for key, sid in subjects:

            def sub(values: list[str], sid: str | None = sid) -> list[str]:
                return [v.replace("{id}", sid) if sid else v for v in values]

            out[key] = {
                "outcome": e["outcome"],
                "reason": e.get("reason") or "BLOCKED_BY_UPSTREAM",
                "blocked_by": sub(e.get("blocked_by", [])),
                "caused_by": sub(e.get("caused_by", [])),
                "required": e.get("required", not key.startswith("R7:")),
            }
    return out


def check(spec: dict[str, Any], assessment: Assessment) -> list[str]:
    problems: list[str] = []
    if assessment.status != spec["status"]:
        problems.append(f"status {assessment.status}, oracle {spec['status']}")
    expected = _expand(spec["non_pass"])
    actual = assessment.by_key()
    for key, want in expected.items():
        f = actual.get(key)
        if f is None:
            problems.append(f"missing {key}")
            continue
        got = {
            "outcome": f.outcome,
            "reason": f.reason,
            "blocked_by": f.blocked_by,
            "caused_by": f.caused_by,
            "required": f.required,
        }
        if got != want:
            problems.append(f"{key}: {got} != {want}")
    for key, f in actual.items():
        if key not in expected and f.outcome != "PASS":
            problems.append(f"unexpected {key} {f.outcome} {f.reason}")
    counts = assessment.counts()["by_outcome"]
    if counts != spec["counts"]:
        problems.append(f"counts {counts} != {spec['counts']}")
    return problems
