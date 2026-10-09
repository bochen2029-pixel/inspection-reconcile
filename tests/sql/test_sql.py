"""A6 · the SQL cross-check (SPEC Appendix G, §15.1 "sql").

The Appendix G queries in ``sql/`` are a second, independent implementation of R2, R3, R4, R7 and the
duplicate-key part of R0. Over every R0-clean, complete-coverage scenario they must agree with the engine,
which is the system under test; the SQL is its differential partner. The row-selection, encoding and schema
tests derive their expectations from the fixture CSVs, the policy file and the oracle only, never from src/.
"""

import csv
import sqlite3
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

import oracle_util
import pytest
import scenario_support as ss
import yaml
from builder import mf, write
from conftest import POLICIES, REPO

from inspection_reconcile import sqlexport
from inspection_reconcile.errors import RunError
from inspection_reconcile.io.snapshot import load_snapshot
from inspection_reconcile.policy import load_policy
from inspection_reconcile.sqlexport import export_sqlite

SQL_DIR = REPO / "sql"
ORACLE = ss.ORACLE["scenarios"]
DEMO_POLICY = POLICIES / "north-creek-demo.yml"

# Appendix G: "every R0-clean, complete-coverage scenario (S01–S04, S06–S10, S14, S15, S18, S22)".
DIFFERENTIAL_IDS = ("S01", "S02", "S03", "S04", "S06", "S07", "S08", "S09", "S10", "S14", "S15", "S18", "S22")
DIFFERENTIAL = sorted(sid for sid in ORACLE if sid.split("-", 1)[0] in DIFFERENTIAL_IDS)
S01 = next(sid for sid in ORACLE if sid.startswith("S01-"))
S02 = next(sid for sid in ORACLE if sid.startswith("S02-"))
S21 = next(sid for sid in ORACLE if sid.startswith("S21-"))
S23 = next(sid for sid in ORACLE if sid.startswith("S23-"))

# Appendix G's schema, restated here independently of src/.
APPENDIX_G = {
    "obligations": [
        ("obligation_id", "TEXT"),
        ("project_id", "TEXT"),
        ("scope_revision", "TEXT"),
        ("asset_id", "TEXT"),
        ("activity_kind", "TEXT"),
    ],
    "inspections": [
        ("inspection_id", "TEXT"),
        ("revision", "TEXT"),
        ("is_current", "INTEGER"),
        ("obligation_id", "TEXT"),
        ("project_id", "TEXT"),
        ("asset_id", "TEXT"),
        ("activity_kind", "TEXT"),
        ("completion_status", "TEXT"),
        ("completed_at", "TEXT"),
    ],
    "artifacts": [
        ("artifact_id", "TEXT"),
        ("revision", "TEXT"),
        ("is_current", "INTEGER"),
        ("inspection_id", "TEXT"),
        ("project_id", "TEXT"),
        ("asset_id", "TEXT"),
        ("document_kind", "TEXT"),
        ("relative_path", "TEXT"),
    ],
    "approvals": [
        ("approval_id", "TEXT"),
        ("inspection_id", "TEXT"),
        ("inspection_revision", "TEXT"),
        ("evidence_digest", "TEXT"),
        ("decision", "TEXT"),
        ("decided_at", "TEXT"),
        ("decided_by", "TEXT"),
    ],
    "approval_items": [("approval_id", "TEXT"), ("artifact_id", "TEXT"), ("artifact_revision", "TEXT")],
    "required_kinds": [("activity_kind", "TEXT"), ("document_kind", "TEXT")],
}
CSV_FOR_TABLE = {
    "obligations": "scope.csv",
    "inspections": "inspections.csv",
    "artifacts": "artifacts.csv",
    "approvals": "approvals.csv",
}


# --------------------------------------------------------------------------------------------- helpers


def export_dir(snapshot_dir: Path, policy_path: Path, out: Path) -> Path:
    export_sqlite(load_snapshot(snapshot_dir), load_policy(policy_path), out)
    return out


def export(sid: str, tmp_path: Path, name: str | None = None) -> Path:
    snapshot_dir, policy_path = ss.scenario_paths(sid)
    return export_dir(snapshot_dir, policy_path, tmp_path / (name or f"{sid}.sqlite"))


def select(db: Path, sql: str) -> list[tuple]:
    connection = sqlite3.connect(db)
    try:
        return connection.execute(sql).fetchall()
    finally:
        connection.close()


def query(db: Path, name: str, text: str | None = None) -> list[tuple]:
    """Run ``sql/<name>``, or ``text`` in its place."""
    return select(db, text if text is not None else (SQL_DIR / name).read_text(encoding="utf-8"))


def csv_rows(snapshot_dir: Path, file_name: str) -> list[dict[str, str]]:
    with (snapshot_dir / file_name).open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def canonical_utc(text: str) -> str:
    """SPEC §5.6 canonical timestamp, restated for the test: UTC, six fractional digits, a Z suffix."""
    moment = datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(UTC)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def entity_of(subject: str) -> tuple[str, str]:
    """``inspection:INS-001@1`` -> ("inspection", "INS-001"), the shape r3_identity.sql reports."""
    kind, rest = subject.split(":", 1)
    return kind, rest.rsplit("@", 1)[0]


def disagreements(db: Path, assessment, sql_text: dict[str, str] | None = None) -> list[str]:
    """Every way the SQL departs from the engine (empty list = agreement). The rules are Appendix G's.
    ``sql_text`` substitutes a query's text (used by the mutation tests)."""
    problems: list[str] = []
    findings = assessment.findings

    overrides = sql_text or {}

    def run(name: str) -> list[tuple]:
        return query(db, name, overrides.get(name))

    def per_obligation(check: str) -> dict:
        return {f.subject_id: f for f in findings if f.check_id == check and f.subject_kind == "obligation"}

    r2 = per_obligation("R2")
    r2_sql = {ob: reason for ob, _count, reason in run("r2_cardinality.sql")}
    if set(r2_sql) != set(r2):
        problems.append(f"obligations: sql {sorted(set(r2_sql) ^ set(r2))} differ from the engine's scope")
    for ob, f in r2.items():
        if f.outcome != "NOT_EVALUATED" and r2_sql.get(ob) != f.reason:
            problems.append(f"R2 {ob}: sql {r2_sql.get(ob)} engine {f.reason}")

    mismatches: dict[str, set[tuple[str, str, str]]] = defaultdict(set)
    for ob, subject, subject_id, field in run("r3_identity.sql"):
        mismatches[ob].add((subject, subject_id, field))
    for ob, f in per_obligation("R3").items():
        sql = mismatches.get(ob, set())
        if f.outcome == "FAIL":
            engine = {(*entity_of(m["subject"]), m["field"]) for m in f.observed["mismatches"]}
            if sql != engine:
                problems.append(f"R3 {ob}: sql {sorted(sql)} engine {sorted(engine)}")
        elif f.outcome == "NOT_EVALUATED" and r2[ob].reason == "NOT_COMPLETED":
            # r3_identity.sql reads every single-candidate obligation and does not gate on completion,
            # whereas R3 is not evaluated after R2 NOT_COMPLETED: no claim either way (see the pinning test).
            continue
        elif sql:
            problems.append(f"R3 {ob}: sql lists {sorted(sql)} but the engine's R3 is {f.outcome}")
    unknown = set(mismatches) - set(r2)
    if unknown:
        problems.append(f"R3: sql lists obligations outside the scope {sorted(unknown)}")

    missing: dict[str, list[str]] = defaultdict(list)
    for ob, kind in run("r4_missing_kinds.sql"):
        missing[ob].append(kind)
    for ob, f in per_obligation("R4").items():
        if f.outcome != "NOT_EVALUATED" and missing.get(ob, []) != f.observed["missing_kinds"]:
            problems.append(f"R4 {ob}: sql {missing.get(ob, [])} engine {f.observed['missing_kinds']}")
    # r4_missing_kinds.sql reads only obligations with exactly one current, completed inspection.
    single_completed = {"SINGLE_CURRENT_COMPLETED", "CARDINALITY_UNCONFIRMED"}
    outside = sorted(ob for ob in missing if ob not in r2 or r2[ob].reason not in single_completed)
    if outside:
        problems.append(f"R4: sql lists obligations without a single completed candidate {outside}")

    unmatched = [row[0] for row in run("r7_unmatched.sql")]
    engine_r7 = sorted(f.subject_id for f in findings if f.check_id == "R7" and f.outcome == "FAIL")
    if unmatched != engine_r7:
        problems.append(f"R7: sql {unmatched} engine {engine_r7}")
    return problems


# ------------------------------------------------------------------------------- the differential test


def test_differential_set_is_r0_clean_and_complete_per_the_oracle():
    assert len(DIFFERENTIAL) == len(DIFFERENTIAL_IDS)
    for sid in DIFFERENTIAL:
        keys = [e["key"] for e in oracle_util.expand(ORACLE[sid]["non_pass"])]
        assert [k for k in keys if k.startswith(("R0:", "R1:"))] == [], sid


@pytest.mark.parametrize("sid", DIFFERENTIAL)
def test_sql_agrees_with_the_engine(sid, tmp_path):
    db = export(sid, tmp_path)
    assert disagreements(db, ss.run_scenario(sid)) == []
    assert query(db, "r0_duplicate_keys.sql") == []


def test_the_differential_exercises_every_query(tmp_path):
    """Guard against a vacuous pass: across the scenario set, each query returns rows somewhere."""
    reasons: set[str] = set()
    r3 = r4 = r7 = 0
    for sid in DIFFERENTIAL:
        db = export(sid, tmp_path)
        reasons |= {row[2] for row in query(db, "r2_cardinality.sql")}
        r3 += len(query(db, "r3_identity.sql"))
        r4 += len(query(db, "r4_missing_kinds.sql"))
        r7 += len(query(db, "r7_unmatched.sql"))
    assert {"SINGLE_CURRENT_COMPLETED", "NO_CURRENT_INSPECTION", "MULTIPLE_CURRENT"} <= reasons
    assert r3 > 0 and r4 > 0 and r7 > 0


def test_duplicate_keys_are_exported_and_found_by_sql(tmp_path):
    db = export(S23, tmp_path)
    assert query(db, "r0_duplicate_keys.sql") == [("inspection", "INS-004@1", 2)]
    assert select(
        db, "SELECT COUNT(*) FROM inspections WHERE inspection_id = 'INS-004' AND revision = '1'"
    ) == [(2,)]


# ------------------------------------------------------------- generated variants (complete coverage)


def variant(snap) -> None:
    """R0-clean mutations of the baseline that reach SQL branches no canonical scenario reaches."""
    s = snap
    ins005 = s.find("inspections", inspection_id="INS-005")  # one current row, not completed, photo missing
    ins005["completion_status"], ins005["completed_at"] = "in_progress", ""
    s.drop("artifacts", artifact_id="ART-005-P")
    del s.files["O-005/photo.png"]
    ins006 = s.find("inspections", inspection_id="INS-006")  # not completed AND an identity mismatch
    ins006["completion_status"], ins006["completed_at"], ins006["asset_id"] = "not_started", "", "A-906"
    s.find("inspections", inspection_id="INS-007")["project_id"] = "NC-002"
    s.find("inspections", inspection_id="INS-008")["asset_id"] = "A-908"
    s.find("inspections", inspection_id="INS-010")["activity_kind"] = "leak_survey"
    s.find("artifacts", artifact_id="ART-011-R")["asset_id"] = "A-911"
    for suffix, path in (("R", "O-013/report.pdf"), ("P", "O-013/photo.png")):  # both kinds missing
        s.drop("artifacts", artifact_id=f"ART-013-{suffix}")
        del s.files[path]
    s.row(  # a non-current earlier revision that disagrees: ignored by R2/R3 and by the SQL
        "inspections",
        inspection_id="INS-015",
        revision="0",
        is_current="false",
        obligation_id="O-015",
        project_id="NC-001",
        asset_id="A-915",
        activity_kind="visual_inspection",
        completion_status="completed",
        completed_at="2026-09-01T09:30:00-05:00",
    )
    s.find("scope", obligation_id="O-016")["activity_kind"] = "leak_survey"  # no requirement for this kind
    s.find("inspections", inspection_id="INS-016")["activity_kind"] = "leak_survey"
    for ins, ob, current in (("INS-098", "", "true"), ("INS-097", "O-097", "false")):  # unlinked / stale
        s.row(
            "inspections",
            inspection_id=ins,
            revision="1",
            is_current=current,
            obligation_id=ob,
            project_id="NC-001",
            asset_id="A-098",
            activity_kind="visual_inspection",
            completion_status="completed",
            completed_at="2026-09-20T15:00:00Z",
        )


@pytest.fixture(scope="module")
def variant_db(tmp_path_factory):
    root = tmp_path_factory.mktemp("variant")
    snap = mf.baseline("T00-sql")
    variant(snap)
    snapshot_dir = write(snap, root / "snapshot")
    db = export_dir(snapshot_dir, DEMO_POLICY, root / "variant.sqlite")
    return db, ss.run_snapshot(snapshot_dir, DEMO_POLICY)


def test_variant_is_r0_clean(variant_db):
    _, assessment = variant_db
    assert [f.key for f in assessment.findings if f.check_id == "R0"] == ["R0:snapshot:all"]


def test_sql_agrees_with_the_engine_on_variants(variant_db):
    db, assessment = variant_db
    assert disagreements(db, assessment) == []
    assert query(db, "r0_duplicate_keys.sql") == []


def test_variant_reaches_the_extra_branches(variant_db):
    db, _ = variant_db
    r2 = {ob: reason for ob, _count, reason in query(db, "r2_cardinality.sql")}
    assert r2["O-005"] == r2["O-006"] == "NOT_COMPLETED"
    fields = {(ob, subject, field) for ob, subject, _sid, field in query(db, "r3_identity.sql")}
    assert {
        ("O-007", "inspection", "project_id"),
        ("O-008", "inspection", "asset_id"),
        ("O-010", "inspection", "activity_kind"),
        ("O-011", "artifact", "asset_id"),
    } <= fields
    assert ("O-013", "inspection_report") in query(db, "r4_missing_kinds.sql")
    assert ("O-013", "photo") in query(db, "r4_missing_kinds.sql")
    assert all(ob != "O-016" for ob, _kind in query(db, "r4_missing_kinds.sql"))
    assert query(db, "r7_unmatched.sql") == [("INS-098",)]


MUTATIONS = [  # (query file, text to find, replacement): each breaks one rule the differential must catch
    ("r2_cardinality.sql", " AND i.is_current = 1", ""),
    ("r3_identity.sql", "WHERE i_asset <> o_asset", "WHERE i_asset = o_asset"),
    ("r3_identity.sql", "WHERE a.project_id <> c.o_project", "WHERE a.project_id = c.o_project"),
    ("r4_missing_kinds.sql", "WHERE NOT EXISTS", "WHERE EXISTS"),
    ("r4_missing_kinds.sql", "AND i.completion_status = 'completed')", ")"),
    ("r7_unmatched.sql", "WHERE i.is_current = 1\n  AND", "WHERE"),
]


@pytest.mark.parametrize(("name", "find", "replace"), MUTATIONS)
def test_a_broken_query_is_caught(variant_db, name, find, replace):
    db, assessment = variant_db
    text = (SQL_DIR / name).read_text(encoding="utf-8")
    assert text.count(find) == 1, (name, find)
    assert disagreements(db, assessment, {name: text.replace(find, replace)}) != []


def test_r3_sql_does_not_gate_on_completion(variant_db):
    """Pinned difference: the Appendix G r3 query reads every single-candidate obligation, so it reports
    O-006's identity mismatch even though R2 FAILs NOT_COMPLETED there and R3 is not evaluated."""
    db, assessment = variant_db
    assert ("O-006", "inspection", "INS-006", "asset_id") in query(db, "r3_identity.sql")
    r3 = {f.subject_id: f for f in assessment.findings if f.check_id == "R3"}
    assert r3["O-006"].outcome == "NOT_EVALUATED"


# ------------------------------------------------------------------------ rows, values and the schema


def appendix_g_blocks() -> dict[str, str]:
    """The ```sql blocks of SPEC Appendix G, keyed by the sql/ file named before each (the schema first)."""
    text = (REPO / "docs" / "SPEC.md").read_text(encoding="utf-8")
    section = text.split("## Appendix G", 1)[1].split("\n## ", 1)[0]
    blocks: dict[str, str] = {}
    name, inside, lines = "schema", False, []
    for line in section.splitlines():
        if inside and line.strip() == "```":
            blocks[name], inside = "\n".join(lines) + "\n", False
        elif inside:
            lines.append(line)
        elif line.strip() == "```sql":
            inside, lines = True, []
        elif "`sql/" in line:
            name = line.split("`sql/", 1)[1].split("`", 1)[0]
    return blocks


def test_queries_and_schema_are_appendix_g_verbatim():
    blocks = appendix_g_blocks()
    files = sorted(p.name for p in SQL_DIR.glob("*.sql"))
    assert sorted(blocks) == sorted([*files, "schema"])
    assert sqlexport.SCHEMA_SQL == blocks.pop("schema")
    for name, body in blocks.items():
        assert (SQL_DIR / name).read_text(encoding="utf-8") == body, name


def test_schema_is_appendix_g(tmp_path):
    db = export(S01, tmp_path)
    tables = [
        row[0] for row in select(db, "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")
    ]
    assert tables == sorted(APPENDIX_G)
    for table, columns in APPENDIX_G.items():
        info = select(db, f"PRAGMA table_info({table})")
        assert [(row[1], row[2]) for row in info] == columns, table


def test_clean_snapshot_rows_and_values_round_trip(tmp_path):
    """S01 is clean, so every CSV row is exported; booleans become 0/1, empty cells NULL, timestamps canonical."""
    snapshot_dir, _ = ss.scenario_paths(S01)
    db = export(S01, tmp_path)
    for table, file_name in CSV_FOR_TABLE.items():
        names = [name for name, _type in APPENDIX_G[table]]
        exported = sorted(select(db, f"SELECT {', '.join(names)} FROM {table}"), key=repr)
        expected = []
        for row in csv_rows(snapshot_dir, file_name):
            values: list = []
            for name, sql_type in APPENDIX_G[table]:
                cell = row[name]
                if cell == "":
                    values.append(None)
                elif sql_type == "INTEGER":
                    values.append({"true": 1, "false": 0}[cell])
                elif name in ("completed_at", "decided_at"):
                    values.append(canonical_utc(cell))
                else:
                    values.append(cell)
            expected.append(tuple(values))
        assert exported == sorted(expected, key=repr), table
    assert select(db, "SELECT COUNT(*) FROM approval_items") == [(0,)]


def test_timestamps_are_normalized_to_utc(variant_db):
    db, _ = variant_db
    rows = select(
        db, "SELECT completed_at FROM inspections WHERE inspection_id = 'INS-015' AND revision = '0'"
    )
    assert rows == [("2026-09-01T14:30:00.000000Z",)]


def test_required_kinds_come_from_the_policy(tmp_path):
    db = export(S01, tmp_path)
    policy = yaml.safe_load(DEMO_POLICY.read_text(encoding="utf-8"))
    expected = sorted(
        (activity, kind)
        for activity, spec in policy["requirements"].items()
        for kind in spec["document_kinds"]
    )
    assert select(db, "SELECT activity_kind, document_kind FROM required_kinds ORDER BY 1, 2") == expected


def test_rows_with_cell_violations_are_left_out(tmp_path):
    """S21: the oracle names exactly one defective row; every other artifact row is exported."""
    r0 = [e["key"] for e in oracle_util.expand(ORACLE[S21]["non_pass"]) if e["key"].startswith("R0:")]
    assert r0 == ["R0:artifact:ART-005-R@1#INVALID_PATH"]
    snapshot_dir, _ = ss.scenario_paths(S21)
    db = export(S21, tmp_path)
    exported = set(select(db, "SELECT artifact_id, revision FROM artifacts"))
    source = {(row["artifact_id"], row["revision"]) for row in csv_rows(snapshot_dir, "artifacts.csv")}
    assert ("ART-005-R", "1") in source
    assert exported == source - {("ART-005-R", "1")}


def test_malformed_rows_are_left_out(tmp_path):
    snap = mf.baseline("T00-sql-malformed")
    snapshot_dir = write(snap, tmp_path / "snapshot")
    inspections = snapshot_dir / "inspections.csv"
    inspections.write_bytes(inspections.read_bytes() + b"INS-900,1,true\n")
    db = export_dir(snapshot_dir, DEMO_POLICY, tmp_path / "m.sqlite")
    assert select(db, "SELECT COUNT(*) FROM inspections") == [
        (len(csv_rows(snapshot_dir, "inspections.csv")) - 1,)
    ]
    assert select(db, "SELECT COUNT(*) FROM inspections WHERE inspection_id = 'INS-900'") == [(0,)]


def test_snapshot_without_scope_exports_no_obligations(tmp_path):
    snapshot_dir, policy_path = ss.scenario_paths(next(sid for sid in ORACLE if sid.startswith("S11-")))
    db = export_dir(snapshot_dir, policy_path, tmp_path / "s11.sqlite")
    assert select(db, "SELECT COUNT(*) FROM obligations") == [(0,)]
    assert query(db, "r2_cardinality.sql") == []


# ------------------------------------------------------------------------------------ the atomic write


def test_export_leaves_only_the_database(tmp_path):
    out = tmp_path / "nested" / "x.sqlite"
    snapshot_dir, policy_path = ss.scenario_paths(S01)
    export_dir(snapshot_dir, policy_path, out)
    export_dir(snapshot_dir, policy_path, out)  # replacing an existing file is fine
    assert sorted(p.name for p in out.parent.iterdir()) == ["x.sqlite"]
    assert select(out, "SELECT COUNT(*) FROM obligations") == [(40,)]
    assert select(out, "PRAGMA integrity_check") == [("ok",)]


def test_failed_replace_keeps_the_previous_file(tmp_path, monkeypatch):
    out = export(S01, tmp_path, "x.sqlite")
    before = out.read_bytes()

    def refuse(src, dst):
        raise PermissionError(13, "simulated: the file is in use")

    monkeypatch.setattr(sqlexport.os, "replace", refuse)
    with pytest.raises(RunError) as info:
        export(S02, tmp_path, "x.sqlite")
    assert info.value.code == "WRITE_FAILED"
    assert out.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["x.sqlite"]


def test_database_error_cleans_up(tmp_path, monkeypatch):
    monkeypatch.setattr(sqlexport, "SCHEMA_SQL", "CREATE TABLE broken (")
    with pytest.raises(RunError) as info:
        export(S01, tmp_path, "x.sqlite")
    assert info.value.code == "WRITE_FAILED"
    assert list(tmp_path.iterdir()) == []
