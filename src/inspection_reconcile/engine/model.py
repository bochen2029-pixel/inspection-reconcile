"""Engine data structures: validated rows and findings (SPEC §7.5, §9.2)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from inspection_reconcile.grammar import CellValue
from inspection_reconcile.io.snapshot import RawRow
from inspection_reconcile.vocab import CHECK_ORDER, CHECK_TYPES, SUBJECT_KIND_RANK


@dataclass(frozen=True)
class Violation:
    field: str
    value: str | None
    rule: str
    code: str


@dataclass
class Row:
    """One CSV record after §7.5.1 validation. ``values`` holds only the cells that satisfy their grammar."""

    dataset: str
    raw: RawRow
    values: dict[str, CellValue]
    violations: list[Violation]
    key: tuple[str, ...] | None
    link_readable: bool
    quarantine: set[str] = field(default_factory=set)

    @property
    def quarantined(self) -> bool:
        return bool(self.quarantine)

    @property
    def malformed(self) -> bool:
        return self.raw.malformed

    def get(self, column: str) -> CellValue:
        return self.values.get(column)

    def text(self, column: str) -> str:
        """A cell that is known to be a valid string (used only where validity has been established)."""
        value = self.values.get(column)
        assert isinstance(value, str), (column, value)
        return value

    @property
    def is_current(self) -> bool:
        return self.values.get("is_current") is True


@dataclass
class Finding:
    check_id: str
    subject_kind: str
    subject_id: str
    outcome: str
    reason: str
    required: bool
    expected: dict[str, Any]
    observed: dict[str, Any]
    explanation: str
    resolution: str
    blocked_by: list[str] = field(default_factory=list)
    caused_by: list[str] = field(default_factory=list)
    evidence: list[dict[str, str]] = field(default_factory=list)
    code_suffix: str | None = None

    @property
    def key(self) -> str:
        base = f"{self.check_id}:{self.subject_kind}:{self.subject_id}"
        return f"{base}#{self.code_suffix}" if self.code_suffix else base

    @property
    def check_type(self) -> str:
        return CHECK_TYPES[self.check_id]

    def sort_key(self) -> tuple[int, int, str, str]:
        return (
            CHECK_ORDER[self.check_id],
            SUBJECT_KIND_RANK[self.subject_kind],
            self.subject_id,
            self.code_suffix or "",
        )

    def semantic(self) -> dict[str, Any]:
        """The reduction used by assessment_semantic_sha256 and compare (SPEC §8.5)."""
        return {
            "key": self.key,
            "outcome": self.outcome,
            "reason": self.reason,
            "expected": self.expected,
            "observed": self.observed,
            "blocked_by": self.blocked_by,
            "caused_by": self.caused_by,
            "required": self.required,
        }

    def to_json(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "check_id": self.check_id,
            "check_type": self.check_type,
            "required": self.required,
            "subject": {"kind": self.subject_kind, "id": self.subject_id},
            "outcome": self.outcome,
            "reason": self.reason,
            "expected": self.expected,
            "observed": self.observed,
            "explanation": self.explanation,
            "resolution": self.resolution,
            "blocked_by": self.blocked_by,
            "caused_by": self.caused_by,
            "evidence": self.evidence,
        }
