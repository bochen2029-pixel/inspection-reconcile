"""Evaluation context: indexes over validated rows (SPEC §7.1 step 6)."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from inspection_reconcile.engine.coverage import Coverage
from inspection_reconcile.engine.integrity import IntegrityResult
from inspection_reconcile.engine.model import Row
from inspection_reconcile.io.evidence import EvidenceStore
from inspection_reconcile.io.snapshot import LoadedSnapshot
from inspection_reconcile.policy import Policy


@dataclass
class Context:
    snapshot: LoadedSnapshot
    policy: Policy
    evidence: EvidenceStore
    as_of: datetime
    integrity: IntegrityResult
    coverage: dict[str, Coverage]
    current_by_obligation: dict[str, list[Row]] = field(default_factory=dict)
    superseded_by_obligation: dict[str, list[Row]] = field(default_factory=dict)
    current_artifacts_by_inspection: dict[str, list[Row]] = field(default_factory=dict)
    approvals_by_inspection: dict[str, list[Row]] = field(default_factory=dict)
    items_by_approval: dict[str, list[Row]] = field(default_factory=dict)
    scope_rows_by_obligation: dict[str, list[Row]] = field(default_factory=dict)

    @property
    def system(self) -> str:
        return self.snapshot.source["system"]

    @property
    def scope_revision(self) -> str:
        scope = self.snapshot.scope
        return scope.scope_revision if scope is not None else ""

    def complete(self, dataset: str) -> bool:
        return self.coverage[dataset].complete

    def cov_word(self, dataset: str) -> str:
        return self.coverage[dataset].effective

    def build_indexes(self) -> None:
        rows = self.integrity.rows
        current: dict[str, list[Row]] = defaultdict(list)
        superseded: dict[str, list[Row]] = defaultdict(list)
        for row in rows["inspections"]:
            if row.quarantined:
                continue
            ob = row.values.get("obligation_id")
            if not isinstance(ob, str):
                continue
            (current if row.is_current else superseded)[ob].append(row)
        artifacts: dict[str, list[Row]] = defaultdict(list)
        for row in rows["artifacts"]:
            if not row.quarantined and row.is_current:
                artifacts[row.text("inspection_id")].append(row)
        approvals: dict[str, list[Row]] = defaultdict(list)
        for row in rows["approvals"]:
            if not row.quarantined:
                approvals[row.text("inspection_id")].append(row)
        items: dict[str, list[Row]] = defaultdict(list)
        for row in rows["approval_items"]:
            if not row.quarantined:
                items[row.text("approval_id")].append(row)
        scope_rows: dict[str, list[Row]] = defaultdict(list)
        for row in rows["scope"]:
            if row.key is not None:
                scope_rows[row.key[0]].append(row)
        self.current_by_obligation = dict(current)
        self.superseded_by_obligation = dict(superseded)
        self.current_artifacts_by_inspection = dict(artifacts)
        self.approvals_by_inspection = dict(approvals)
        self.items_by_approval = dict(items)
        self.scope_rows_by_obligation = dict(scope_rows)

    def scope_row(self, obligation: str) -> Row:
        """The obligation's live scope row. R2 only runs when no R0 finding concerns the obligation."""
        live = [r for r in self.scope_rows_by_obligation[obligation] if not r.quarantined]
        assert len(live) == 1, obligation
        return live[0]

    def locators(self, rows: list[Row]) -> list[dict[str, str]]:
        pairs = sorted({(r.dataset, r.raw.locator) for r in rows})
        return [{"system": self.system, "dataset": d, "locator": loc} for d, loc in pairs]
