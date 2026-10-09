"""The check registry: the single seam that fault-injection tests replace (SPEC §13.2, §15.1)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from inspection_reconcile.engine import checks, coverage

# Tests monkeypatch entries of this dict to inject faults (FI-1 … FI-5).
CHECKS: dict[str, Callable[..., Any]] = {
    "R2": checks.r2_cardinality,
    "R3": checks.r3_identity,
    "R4": checks.r4_required_artifacts,
    "R5": checks.r5_availability,
    "R6": checks.r6_approval,
    "R7": checks.r7_unmatched,
    "EFFECTIVE_COVERAGE": coverage.effective_coverage,
}

# Integrity switches used only by fault injection.
OPTIONS: dict[str, bool] = {"detect_duplicates": True}
