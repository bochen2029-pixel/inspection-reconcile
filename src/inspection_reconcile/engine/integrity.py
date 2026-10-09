"""R0 · input integrity (SPEC §7.5 and §22): ordered steps, quarantine, attribution, requiredness."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from inspection_reconcile.canonical import canonical, sha256_hex, sort_canonical
from inspection_reconcile.engine.model import Finding, Row, Violation
from inspection_reconcile.grammar import check_cell, format_ts, parse_ts
from inspection_reconcile.io.snapshot import LoadedSnapshot, RawRow
from inspection_reconcile.report.templates import render
from inspection_reconcile.vocab import (
    COMPLETE,
    COVERAGE_DATASET_OF,
    KEY_FIELDS,
    LINK_FIELD,
    SCHEMAS,
    SUBJECT_KIND_FOR_DATASET,
)

DATASET_ORDER = ("scope", "inspections", "artifacts", "approvals", "approval_items")
ENTITY_LABEL = {"inspections": "Inspection", "artifacts": "Artifact"}
TARGET_SINGULAR = {"inspections": "inspection", "approvals": "approval", "artifacts": "artifact"}
STEP1_CODE_ORDER = ("INVALID_VALUE", "UNMAPPED_VALUE", "INVALID_PATH")


# ------------------------------------------------------------------------------------------- step 1


def validate_row(raw: RawRow, unmapped: frozenset[tuple[str, str, str]]) -> Row:
    """§7.5.1: validate every schema cell; classify violations; decide key and link readability."""
    dataset = raw.dataset
    if raw.cells is None:
        return Row(dataset, raw, {}, [], None, False, {"MALFORMED_ROW"})
    values: dict[str, Any] = {}
    violations: list[Violation] = []
    bad_columns: set[str] = set()
    for column, type_name, required in SCHEMAS[dataset]:
        cell = raw.cells[column]
        value, rule = check_cell(type_name, cell, required)
        # §12.3: a label missing from the map is never accepted, even when it happens to satisfy the grammar.
        listed = (dataset, raw.source or "", column) in unmapped
        if rule is None and not listed:
            values[column] = value
            continue
        bad_columns.add(column)
        if listed:
            violations.append(Violation(column, cell, "unmapped", "UNMAPPED_VALUE"))
        elif rule is not None and rule.startswith("path:"):
            violations.append(Violation(column, cell, rule, "INVALID_PATH"))
        else:
            assert rule is not None  # not listed as unmapped, so the grammar failed
            violations.append(Violation(column, cell, rule, "INVALID_VALUE"))
    key_fields = KEY_FIELDS[dataset]
    key: tuple[str, ...] | None = None
    if not any(c in bad_columns for c in key_fields):
        key = tuple(str(values[c]) for c in key_fields)
    link = LINK_FIELD[dataset]
    link_readable = link is None or link not in bad_columns
    quarantine = {v.code for v in violations}
    return Row(dataset, raw, values, violations, key, link_readable, quarantine)


def schema_cells(row: Row) -> tuple[str | None, ...]:
    """The row's schema cells; `x_` provenance columns are excluded (§5.4, §7.5.2, AM-5)."""
    cells = row.raw.cells
    assert cells is not None  # a row with a readable key is never malformed
    return tuple(cells[c] for c, _, _ in SCHEMAS[row.dataset])


def subject_id_for_key(dataset: str, key: tuple[str, ...]) -> str:
    if dataset in ("inspections", "artifacts"):
        return f"{key[0]}@{key[1]}"
    if dataset == "approval_items":
        return f"{key[0]}/{key[1]}"
    return key[0]


def unattributable_id(row: Row) -> str:
    raw = row.raw
    if raw.cells is None:
        cells: Any = list(raw.raw_cells)
    else:
        cells = {c: raw.cells[c] for c, _, _ in SCHEMAS[row.dataset]}
    digest = sha256_hex(canonical({"dataset": row.dataset, "cells": cells}))
    return f"{row.dataset}.{digest[:16]}"


def row_subject(row: Row) -> tuple[str, str]:
    if row.key is None:
        return "unattributable_row", unattributable_id(row)
    return SUBJECT_KIND_FOR_DATASET[row.dataset], subject_id_for_key(row.dataset, row.key)


def human_subject(kind: str, subject_id: str) -> str:
    if kind == "inspection":
        return _human_revisioned("Inspection", subject_id)
    if kind == "artifact":
        return _human_revisioned("Artifact", subject_id)
    if kind == "approval":
        return f"Approval {subject_id}"
    if kind == "approval_item":
        return f"Approval item {subject_id}"
    if kind == "scope_row":
        return f"Scope row {subject_id}"
    if kind == "unattributable_row":
        return f"An unattributable row in {subject_id.split('.', 1)[0]}"
    return subject_id


def _human_revisioned(label: str, subject_id: str) -> str:
    if "@" in subject_id:
        ident, rev = subject_id.split("@", 1)
        return f"{label} {ident} revision {rev}"
    return f"{label} {subject_id}"


def key_human(dataset: str, key: tuple[str, ...]) -> str:
    if dataset in ("inspections", "artifacts"):
        return f"{key[0]} revision {key[1]}"
    if dataset == "approval_items":
        return f"{key[0]} / {key[1]}"
    return key[0]


def violations_text(violations: Iterable[dict[str, Any]]) -> str:
    parts = []
    for v in violations:
        value = "" if v["value"] is None else v["value"]
        parts.append(f'field {v["field"]} value "{value}" violates {v["rule"]}')
    return "; ".join(parts)


# -------------------------------------------------------------------------------------------- result


@dataclass
class IntegrityResult:
    rows: dict[str, list[Row]]
    findings: list[Finding]
    attributed: dict[str, list[str]]
    contradicted: dict[str, int]
    unattributable: dict[str, int]
    scope_unattributable: bool
    obligations: set[str]


@dataclass
class _Acc:
    kind: str
    subject_id: str
    code: str
    rows: list[Row] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)


# ------------------------------------------------------------------------------------------- the pass


class Integrity:
    """Runs the six ordered steps of §7.5 over the snapshot's rows."""

    def __init__(
        self,
        snapshot: LoadedSnapshot,
        as_of: datetime,
        detect_duplicates: bool = True,
    ) -> None:
        self.snapshot = snapshot
        self.as_of = as_of
        self.detect_duplicates = detect_duplicates
        self.system = snapshot.source["system"]
        unmapped = snapshot.normalization.unmapped if snapshot.normalization else frozenset()
        self.rows: dict[str, list[Row]] = {d: [] for d in DATASET_ORDER}
        if snapshot.scope is not None and snapshot.scope.rows is not None:
            self.rows["scope"] = [validate_row(r, unmapped) for r in snapshot.scope.rows]
        for d in ("inspections", "artifacts", "approvals", "approval_items"):
            self.rows[d] = [validate_row(r, unmapped) for r in snapshot.rows[d]]
        self.accs: dict[tuple[str, str, str], _Acc] = {}

    # -- helpers ---------------------------------------------------------------------------------
    def _acc(self, kind: str, subject_id: str, code: str) -> _Acc:
        k = (kind, subject_id, code)
        if k not in self.accs:
            self.accs[k] = _Acc(kind, subject_id, code)
        return self.accs[k]

    def _declared_complete(self, dataset: str) -> bool:
        decl = self.snapshot.datasets.get(dataset)
        return decl is not None and decl.coverage == COMPLETE

    def _live(self, dataset: str) -> list[Row]:
        return [r for r in self.rows[dataset] if not r.quarantined]

    # -- steps -----------------------------------------------------------------------------------
    def step1(self) -> None:
        for d in DATASET_ORDER:
            for row in self.rows[d]:
                if row.malformed:
                    acc = self._acc("unattributable_row", unattributable_id(row), "MALFORMED_ROW")
                    acc.rows.append(row)
                    continue
                if not row.violations:
                    continue
                kind, sid = row_subject(row)
                for code in STEP1_CODE_ORDER:
                    vs = [v for v in row.violations if v.code == code]
                    if vs:
                        self._acc(kind, sid, code).rows.append(row)

    def step2(self) -> None:
        if not self.detect_duplicates:
            return
        for d in DATASET_ORDER:
            groups: dict[tuple[str, ...], list[Row]] = defaultdict(list)
            for row in self.rows[d]:
                if row.key is not None:
                    groups[row.key].append(row)
            for key, members in groups.items():
                if len(members) < 2:
                    continue
                acc = self._acc(SUBJECT_KIND_FOR_DATASET[d], subject_id_for_key(d, key), "DUPLICATE_KEY")
                acc.rows.extend(members)
                acc.extra = {
                    "identical": len({schema_cells(m) for m in members}) == 1,
                    "key": key,
                    "dataset": d,
                }
                for m in members:
                    m.quarantine.add("DUPLICATE_KEY")

    def step3(self) -> None:
        for d, id_col in (("inspections", "inspection_id"), ("artifacts", "artifact_id")):
            current: dict[str, list[Row]] = defaultdict(list)
            for row in self._live(d):
                if row.is_current:
                    current[row.text(id_col)].append(row)
            for entity, members in current.items():
                if len(members) >= 2:
                    acc = self._acc(SUBJECT_KIND_FOR_DATASET[d], entity, "MULTIPLE_CURRENT_REVISIONS")
                    acc.rows.extend(members)
                    acc.extra = {"dataset": d}

    def step4(self) -> None:
        for row in self._live("inspections"):
            if (
                row.is_current
                and row.get("completion_status") == "completed"
                and row.get("completed_at") is None
            ):
                assert row.key is not None
                acc = self._acc(
                    "inspection", subject_id_for_key("inspections", row.key), "COMPLETED_WITHOUT_TIMESTAMP"
                )
                acc.rows.append(row)

    def step5(self) -> None:
        for d, column in (("inspections", "completed_at"), ("approvals", "decided_at")):
            for row in self._live(d):
                value = row.get(column)
                if not isinstance(value, str):
                    continue
                ts = parse_ts(value)
                if ts is not None and ts > self.as_of:
                    assert row.key is not None
                    acc = self._acc(
                        SUBJECT_KIND_FOR_DATASET[d], subject_id_for_key(d, row.key), "TIMESTAMP_AFTER_AS_OF"
                    )
                    acc.rows.append(row)
                    acc.extra = {"field": column, "value": value}

    def step6(self) -> dict[str, int]:
        contradicted: dict[str, int] = defaultdict(int)
        # AM-2: an entity reference resolves to any row whose entity-id cell is readable, whatever its revision.
        inspection_ids = {
            str(r.values["inspection_id"]) for r in self.rows["inspections"] if "inspection_id" in r.values
        }
        approval_ids = {r.key[0] for r in self.rows["approvals"] if r.key is not None}
        artifact_keys = {r.key for r in self.rows["artifacts"] if r.key is not None}
        checks = (
            ("artifacts", [("inspection_id", "inspections")]),
            ("approvals", [("inspection_id", "inspections")]),
            ("approval_items", [("approval_id", "approvals"), ("artifact_id", "artifacts")]),
        )
        for d, refs in checks:
            for row in self._live(d):
                missing: list[dict[str, str]] = []
                for column, target in refs:
                    if not self._declared_complete(target):
                        continue
                    if target == "inspections":
                        target_key = row.text(column)
                        found = target_key in inspection_ids
                        human = target_key
                    elif target == "approvals":
                        target_key = row.text(column)
                        found = target_key in approval_ids
                        human = target_key
                    else:
                        art_key = (row.text("artifact_id"), row.text("artifact_revision"))
                        found = art_key in artifact_keys
                        human = f"{art_key[0]}@{art_key[1]}"
                    if not found:
                        missing.append({"field": column, "target": target, "target_key": human})
                        contradicted[target] += 1
                if missing:
                    assert row.key is not None
                    kind, sid = SUBJECT_KIND_FOR_DATASET[d], subject_id_for_key(d, row.key)
                    acc = self._acc(kind, sid, "DANGLING_REFERENCE")
                    acc.rows.append(row)
                    acc.extra.setdefault("missing", []).extend(missing)
        return dict(contradicted)

    # -- attribution -----------------------------------------------------------------------------
    def attribution_maps(self) -> tuple[dict[str, set[str]], dict[str, list[str | None]]]:
        entity: dict[str, set[str]] = defaultdict(set)
        for row in self.rows["inspections"]:
            ident = row.values.get("inspection_id")  # the entity id cell, whatever the revision cell (AM-2)
            if not isinstance(ident, str):
                continue
            ob = row.values.get("obligation_id")
            if isinstance(ob, str):
                entity[ident].add(ob)
        approval_links: dict[str, list[str | None]] = defaultdict(list)
        for row in self.rows["approvals"]:
            if row.key is None:
                continue
            link = row.values.get("inspection_id") if row.link_readable else None
            approval_links[row.key[0]].append(link if isinstance(link, str) else None)
        return entity, approval_links

    def attribute(
        self, row: Row, entity: dict[str, set[str]], approval_links: dict[str, list[str | None]]
    ) -> tuple[set[str], bool]:
        if row.key is None:
            return set(), True
        d = row.dataset
        if d == "scope":
            return {row.key[0]}, False
        if not row.link_readable:
            return set(), True
        if d == "inspections":
            ob = row.values.get("obligation_id")
            return ({ob} if isinstance(ob, str) else set()), False
        if d in ("artifacts", "approvals"):
            return set(entity.get(row.text("inspection_id"), set())), False
        obligations: set[str] = set()
        unattributable = False
        for link in approval_links.get(row.key[0], []):
            if link is None:
                unattributable = True
            else:
                obligations |= entity.get(link, set())
        return obligations, unattributable

    # -- run -------------------------------------------------------------------------------------
    def run(self) -> IntegrityResult:
        self.step1()
        self.step2()
        self.step3()
        self.step4()
        self.step5()
        contradicted = self.step6()

        obligations = {r.key[0] for r in self.rows["scope"] if r.key is not None}
        entity, approval_links = self.attribution_maps()

        unattributable_counts: dict[str, int] = defaultdict(int)
        scope_unattributable = False
        for d in DATASET_ORDER:
            for row in self.rows[d]:
                if not row.quarantined:
                    continue
                _, unattr = self.attribute(row, entity, approval_links)
                if not unattr:
                    continue
                if d == "scope":
                    scope_unattributable = True
                else:
                    unattributable_counts[COVERAGE_DATASET_OF[d]] += 1

        findings: list[Finding] = []
        attributed: dict[str, list[str]] = defaultdict(list)
        for acc in self.accs.values():
            finding, attr, unattr = self._finding(acc, entity, approval_links, obligations)
            findings.append(finding)
            if finding.outcome != "PASS" and not unattr:
                for ob in sorted(attr & obligations):
                    attributed[ob].append(finding.key)
        if not findings:
            text, res = render("INTEGRITY_OK")
            findings.append(Finding("R0", "snapshot", "all", "PASS", "INTEGRITY_OK", True, {}, {}, text, res))
        for ob in attributed:
            attributed[ob].sort()
        return IntegrityResult(
            rows=self.rows,
            findings=findings,
            attributed=dict(attributed),
            contradicted=contradicted,
            unattributable=dict(unattributable_counts),
            scope_unattributable=scope_unattributable,
            obligations=obligations,
        )

    def _finding(
        self,
        acc: _Acc,
        entity: dict[str, set[str]],
        approval_links: dict[str, list[str | None]],
        obligations: set[str],
    ) -> tuple[Finding, set[str], bool]:
        attr: set[str] = set()
        unattr = False
        for row in acc.rows:
            a, u = self.attribute(row, entity, approval_links)
            attr |= a
            unattr = unattr or u
        required = unattr or acc.kind == "scope_row" or bool(attr & obligations)
        code = acc.code
        rows = acc.rows
        count = len(rows)
        subject = human_subject(acc.kind, acc.subject_id)
        observed: dict[str, Any]
        if code in ("INVALID_VALUE", "UNMAPPED_VALUE", "INVALID_PATH"):
            vs = sort_canonical(
                {"field": v.field, "value": v.value, "rule": v.rule}
                for r in rows
                for v in r.violations
                if v.code == code
            )
            deduped: list[dict[str, Any]] = []
            for v in vs:
                if not deduped or deduped[-1] != v:
                    deduped.append(v)
            observed = {"violations": deduped, "count": count}
            outcome = "UNKNOWN"
            text, res = render(code, subject=subject, violations=violations_text(deduped))
        elif code == "MALFORMED_ROW":
            raw = rows[0].raw
            observed = {
                "dataset": rows[0].dataset,
                "field_count": len(raw.raw_cells),
                "header_count": raw.header_count,
                "count": count,
            }
            outcome = "UNKNOWN"
            text, res = render(
                code,
                dataset=rows[0].dataset,
                field_count=len(raw.raw_cells),
                header_count=raw.header_count,
                count=count,
            )
        elif code == "DUPLICATE_KEY":
            identical = bool(acc.extra["identical"])
            observed = {"count": count, "identical": identical}
            outcome = "FAIL"
            text, res = render(
                code,
                dataset=acc.extra["dataset"],
                key=key_human(acc.extra["dataset"], acc.extra["key"]),
                count=count,
                identical_note="the rows are identical" if identical else "the rows differ",
            )
        elif code == "MULTIPLE_CURRENT_REVISIONS":
            revisions = sorted(r.text("revision") for r in rows)
            observed = {"current_revisions": revisions}
            outcome = "FAIL"
            text, res = render(
                code,
                entity=ENTITY_LABEL[acc.extra["dataset"]],
                id=acc.subject_id,
                count=len(revisions),
                revisions=", ".join(revisions),
            )
        elif code == "COMPLETED_WITHOUT_TIMESTAMP":
            observed = {"count": count}
            outcome = "FAIL"
            ident, rev = acc.subject_id.split("@", 1)
            text, res = render(code, id=ident, revision=rev)
        elif code == "TIMESTAMP_AFTER_AS_OF":
            observed = {"field": acc.extra["field"], "value": acc.extra["value"], "count": count}
            outcome = "UNKNOWN"
            text, res = render(
                code,
                subject=subject,
                field=acc.extra["field"],
                value=acc.extra["value"],
                as_of=format_ts(self.as_of),
            )
        elif code == "DANGLING_REFERENCE":
            missing = sort_canonical(acc.extra["missing"])
            observed = {"missing": missing, "count": count}
            outcome = "UNKNOWN"
            described = ", ".join(f"{TARGET_SINGULAR[m['target']]} {m['target_key']}" for m in missing)
            text, res = render(code, subject=subject, missing=described)
        else:  # pragma: no cover - every code is handled above
            raise AssertionError(code)
        evidence = sorted(
            {(r.dataset, r.raw.locator) for r in rows},
        )
        finding = Finding(
            check_id="R0",
            subject_kind=acc.kind,
            subject_id=acc.subject_id,
            outcome=outcome,
            reason=code,
            required=required,
            expected={},
            observed=observed,
            explanation=text,
            resolution=res,
            evidence=[{"system": self.system, "dataset": d, "locator": loc} for d, loc in evidence],
            code_suffix=code,
        )
        return finding, attr, unattr
