"""comparison.json (SPEC §9.5): findings matched by key."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from inspection_reconcile.errors import RunError

COMPARISON_SCHEMA = "inspection-reconcile/comparison/v1"
COMPARED = ("outcome", "reason", "expected", "observed", "blocked_by", "caused_by", "required")


def load_assessment(path: Path) -> dict[str, Any]:
    target = path / "assessment.json" if path.is_dir() else path
    try:
        doc = json.loads(target.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RunError("COMPARE_INPUT_INVALID", f"{target.as_posix()}: {exc}") from exc
    if not isinstance(doc, dict) or doc.get("schema") != "inspection-reconcile/assessment/v1":
        raise RunError(
            "COMPARE_INPUT_INVALID", f"{target.as_posix()}: not an inspection-reconcile assessment"
        )
    problem = _shape_problem(doc)
    if problem:
        raise RunError("COMPARE_INPUT_INVALID", f"{target.as_posix()}: {problem}")
    return doc


def _shape_problem(doc: dict[str, Any]) -> str | None:
    """The first member compare() needs that is missing or of the wrong type (SPEC §9.1, §9.2)."""
    for member in ("evaluation_id", "status"):
        if not isinstance(doc.get(member), str):
            return f"{member} is missing or not a string"
    findings = doc.get("findings")
    if not isinstance(findings, list):
        return "findings is missing or not a list"
    keys = set()
    for index, finding in enumerate(findings):
        if not isinstance(finding, dict) or not isinstance(finding.get("key"), str):
            return f"findings[{index}] has no key"
        missing = [field for field in COMPARED if field not in finding]
        if missing:
            return f"finding {finding['key']} lacks {', '.join(missing)}"
        if finding["key"] in keys:
            return f"finding {finding['key']} appears twice"
        keys.add(finding["key"])
    return None


def compare(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    b = {f["key"]: f for f in before["findings"]}
    a = {f["key"]: f for f in after["findings"]}
    changed = []
    unchanged = 0
    for key in sorted(set(b) & set(a)):
        if any(b[key][field] != a[key][field] for field in COMPARED):
            changed.append(
                {
                    "key": key,
                    "before": {"outcome": b[key]["outcome"], "reason": b[key]["reason"]},
                    "after": {"outcome": a[key]["outcome"], "reason": a[key]["reason"]},
                }
            )
        else:
            unchanged += 1
    return {
        "schema": COMPARISON_SCHEMA,
        "before": {"evaluation_id": before["evaluation_id"], "status": before["status"]},
        "after": {"evaluation_id": after["evaluation_id"], "status": after["status"]},
        "added": sorted(set(a) - set(b)),
        "removed": sorted(set(b) - set(a)),
        "changed": changed,
        "unchanged_count": unchanged,
    }


def has_differences(comparison: dict[str, Any]) -> bool:
    return bool(comparison["added"] or comparison["removed"] or comparison["changed"])
