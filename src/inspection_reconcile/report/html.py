"""report.html (SPEC §9.4): self-contained, escaped, deterministic, no scripts, no external requests."""

from __future__ import annotations

import json
from importlib import resources
from typing import Any

import jinja2

from inspection_reconcile.engine.assess import Assessment
from inspection_reconcile.engine.model import Finding
from inspection_reconcile.vocab import COVERAGE_DATASETS, PER_OBLIGATION_CHECKS

LABEL = {"PASS": "PASS", "FAIL": "FAIL", "UNKNOWN": "UNK", "NOT_EVALUATED": "N/E"}


def _environment() -> jinja2.Environment:
    return jinja2.Environment(
        autoescape=True,
        trim_blocks=False,
        lstrip_blocks=False,
        keep_trailing_newline=True,
        undefined=jinja2.StrictUndefined,
    )


def _template_text() -> str:
    return (
        resources.files("inspection_reconcile.report").joinpath("report.html.j2").read_text(encoding="utf-8")
    )


def _pretty(value: Any) -> str:
    if value is None:
        return "-"
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=1)


def _blocks(findings: list[Finding]) -> dict[str, int]:
    """For each finding, how many findings it blocks, transitively through blocked_by."""
    children: dict[str, list[str]] = {}
    for f in findings:
        for parent in f.blocked_by:
            children.setdefault(parent, []).append(f.key)
    counts: dict[str, int] = {}
    for f in findings:
        seen: set[str] = set()
        stack = list(children.get(f.key, []))
        while stack:
            k = stack.pop()
            if k in seen:
                continue
            seen.add(k)
            stack.extend(children.get(k, []))
        counts[f.key] = len(seen)
    return counts


def render_html(assessment: Assessment, provenance_id: str, inputs: list[tuple[str, str]]) -> bytes:
    findings = assessment.findings
    blocks = _blocks(findings)
    required = [f for f in findings if f.required]
    roots = [f for f in required if f.outcome in ("FAIL", "UNKNOWN") and not f.caused_by]
    roots.sort(key=lambda f: (0 if f.outcome == "FAIL" else 1, f.sort_key()))
    root_items = []
    for f in roots:
        names = sorted(set(f.expected) | set(f.observed))
        root_items.append(
            {
                "key": f.key,
                "outcome": f.outcome,
                "reason": f.reason,
                "explanation": f.explanation,
                "resolution": f.resolution,
                "blocks": blocks.get(f.key, 0),
                "value_rows": [
                    {
                        "name": n,
                        "expected": _pretty(f.expected.get(n)),
                        "observed": _pretty(f.observed.get(n)),
                    }
                    for n in names
                ],
                "evidence": [e["locator"] for e in f.evidence],
            }
        )
    by_key = assessment.by_key()
    obligations = sorted({f.subject_id for f in findings if f.subject_kind == "obligation"})
    grid = []
    for ob in obligations:
        cells = []
        for check in PER_OBLIGATION_CHECKS:
            f = by_key[f"{check}:obligation:{ob}"]
            cells.append({"outcome": f.outcome, "reason": f.reason, "label": LABEL[f.outcome]})
        grid.append({"obligation": ob, "cells": cells})
    coverage = []
    for name in COVERAGE_DATASETS:
        c = assessment.coverage[name]
        coverage.append(
            {
                "dataset": name,
                "declared": c.declared or "omitted",
                "effective": c.effective,
                "basis": ", ".join(sorted(c.basis)) or "-",
                "reasons": ", ".join(c.reasons) or "-",
            }
        )
    counts = assessment.counts()
    summary = {
        "root_fail": counts["roots"]["FAIL"],
        "root_unknown": counts["roots"]["UNKNOWN"],
        "dependent_unknown": sum(1 for f in required if f.outcome == "UNKNOWN" and f.caused_by),
        "not_evaluated": counts["required"]["NOT_EVALUATED"],
        "advisory": sum(1 for f in findings if not f.required and f.outcome != "PASS"),
        "total": len(findings),
        "passed": counts["by_outcome"]["PASS"],
    }
    snapshot = assessment.snapshot
    scope = snapshot.scope
    context = {
        "synthetic": bool(
            snapshot.synthetic or snapshot.project.get("synthetic") or assessment.policy.synthetic
        ),
        "project": snapshot.project,
        "status": assessment.status,
        "as_of": assessment.as_of,
        "snapshot_id": snapshot.snapshot_id,
        "scope_revision": scope.scope_revision if scope is not None else "none",
        "pack_id": assessment.policy.pack_id,
        "pack_version": assessment.policy.version,
        "evaluation_id": assessment.evaluation_id,
        "evaluation_short": assessment.evaluation_id[:19] + "…",
        "coverage": coverage,
        "summary": summary,
        "roots": root_items,
        "grid": grid,
        "findings": [
            {
                "key": f.key,
                "outcome": f.outcome,
                "reason": f.reason,
                "required": "yes" if f.required else "advisory",
            }
            for f in findings
        ],
        "provenance_id": provenance_id,
        "snapshot_semantic": assessment.snapshot_semantic_sha256,
        "assessment_semantic": assessment.assessment_semantic_sha256,
        "policy_sha": assessment.policy.sha256,
        "inputs": [{"role": role, "sha256": sha} for role, sha in sorted(inputs)],
        "engine_version": assessment.engine_version,
    }
    text = _environment().from_string(_template_text()).render(**context)
    text = text.replace("\r\n", "\n")
    if not text.endswith("\n"):
        text += "\n"
    return text.encode("utf-8")
