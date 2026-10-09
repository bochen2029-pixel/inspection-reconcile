"""Deterministic fixture generator for the North Creek scenarios (SPEC §14).

Independence (SPEC I-12): this script never imports ``src/``. It has its own canonical JSON, SHA-256 and
evidence-set digest implementation, so approval digests in the fixtures are not produced by the code under test.

Usage:
    python tools/make_fixtures.py                    regenerate fixtures/scenarios
    python tools/make_fixtures.py --check            regenerate into a temporary directory and compare bytes
    python tools/make_fixtures.py --quickbase-import DIR [--decided-by EMAIL]
                                                     write S01 import CSVs for a fresh Quickbase test app (Appendix D)
"""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import io
import json
import shutil
import struct
import sys
import tempfile
import zlib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCENARIOS_DIR = REPO / "fixtures" / "scenarios"
N = 40

# --------------------------------------------------------------------------------------------- digests


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def evidence_set_digest(entries) -> str:
    items = sorted(
        ({"artifact_id": a, "document_kind": k, "revision": r, "sha256": h} for a, k, r, h in entries),
        key=lambda e: (e["document_kind"], e["artifact_id"], e["revision"]),
    )
    return "sha256:" + sha(canonical({"artifacts": items, "scheme": "inspection-reconcile/evidence-set/v1"}))


# ------------------------------------------------------------------------------------------ file bytes


def make_pdf(lines: list[str]) -> bytes:
    """A minimal single-page PDF 1.4, Helvetica, ASCII only, correct xref offsets, no dates or /ID."""
    ops = ["BT", "/F1 12 Tf", "14 TL", "72 720 Td"]
    for i, line in enumerate(lines):
        escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        ops.append(f"({escaped}) Tj" if i == 0 else f"T* ({escaped}) Tj")
    ops.append("ET")
    stream = ("\n".join(ops) + "\n").encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"endstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n".encode()
    return bytes(out)


def make_png(rgb: tuple[int, int, int], width: int = 16, height: int = 12) -> bytes:
    """A solid-color RGB PNG; IDAT is zlib level 0 (stored blocks); no ancillary chunks."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    raw = b"".join(b"\x00" + bytes(rgb) * width for _ in range(height))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw, 0))
        + chunk(b"IEND", b"")
    )


def report_pdf(n: int, revision: str) -> bytes:
    return make_pdf(
        [
            "SYNTHETIC DEMO DATA",
            "North Creek NC-001",
            f"Obligation O-{n:03d} / Asset A-{n:03d}",
            f"Visual inspection report - revision {revision}",
        ]
    )


def photo_color(n: int) -> tuple[int, int, int]:
    return ((37 * n) % 256, (73 * n) % 256, (109 * n) % 256)


# ------------------------------------------------------------------------------------------- baseline

COLUMNS = {
    "scope": ["obligation_id", "project_id", "scope_revision", "asset_id", "activity_kind"],
    "inspections": [
        "inspection_id",
        "revision",
        "is_current",
        "obligation_id",
        "project_id",
        "asset_id",
        "activity_kind",
        "completion_status",
        "completed_at",
    ],
    "artifacts": [
        "artifact_id",
        "revision",
        "is_current",
        "inspection_id",
        "project_id",
        "asset_id",
        "document_kind",
        "relative_path",
    ],
    "approvals": [
        "approval_id",
        "inspection_id",
        "inspection_revision",
        "evidence_digest",
        "decision",
        "decided_at",
        "decided_by",
    ],
    "approval_items": ["approval_id", "artifact_id", "artifact_revision"],
}
KEYS = {
    "scope": ("obligation_id",),
    "inspections": ("inspection_id", "revision"),
    "artifacts": ("artifact_id", "revision"),
    "approvals": ("approval_id",),
    "approval_items": ("approval_id", "artifact_id"),
}
ACCEPTED = {"by": "Synthetic Client Records Dept", "at": "2026-08-15T14:00:00Z", "reference": "SYN-SCOPE-S1"}
REVIEWER = "reviewer@example.invalid"


def day(n: int) -> int:
    return 1 + ((n - 1) % 25)


def completed_at(n: int) -> str:
    return f"2026-09-{day(n):02d}T15:00:00Z"


def decided_at(n: int) -> str:
    return f"2026-09-{day(n) + 2:02d}T17:00:00Z"


class Snapshot:
    """Mutable in-memory scenario: tables of string cells plus evidence files keyed by relative path."""

    def __init__(self) -> None:
        self.tables: dict[str, list[dict[str, str]]] = {k: [] for k in COLUMNS}
        self.files: dict[str, bytes] = {}
        self.manifest: dict = {}
        self.project = {
            "project_id": "NC-001",
            "client_id": "CL-SYN-01",
            "name": "North Creek (synthetic)",
            "synthetic": True,
        }
        self.items_declared = False
        self.expected: dict = {}

    def row(self, table: str, **cells: str) -> None:
        self.tables[table].append({c: cells.get(c, "") for c in COLUMNS[table]})

    def find(self, table: str, **match: str) -> dict[str, str]:
        hits = [r for r in self.tables[table] if all(r[k] == v for k, v in match.items())]
        assert len(hits) == 1, (table, match, len(hits))
        return hits[0]

    def drop(self, table: str, **match: str) -> None:
        before = len(self.tables[table])
        self.tables[table] = [r for r in self.tables[table] if not all(r[k] == v for k, v in match.items())]
        assert len(self.tables[table]) == before - 1, (table, match)


def artifact_digest(snap: Snapshot, inspection_id: str) -> str:
    """Digest of the current required artifacts of one inspection, from the generator's own data."""
    entries = []
    for a in snap.tables["artifacts"]:
        if a["inspection_id"] == inspection_id and a["is_current"] == "true":
            entries.append(
                (a["artifact_id"], a["document_kind"], a["revision"], sha(snap.files[a["relative_path"]]))
            )
    return evidence_set_digest(entries)


def baseline(scenario_id: str) -> Snapshot:
    snap = Snapshot()
    for n in range(1, N + 1):
        o, a, ins = f"O-{n:03d}", f"A-{n:03d}", f"INS-{n:03d}"
        snap.row(
            "scope",
            obligation_id=o,
            project_id="NC-001",
            scope_revision="S1",
            asset_id=a,
            activity_kind="visual_inspection",
        )
        snap.row(
            "inspections",
            inspection_id=ins,
            revision="1",
            is_current="true",
            obligation_id=o,
            project_id="NC-001",
            asset_id=a,
            activity_kind="visual_inspection",
            completion_status="completed",
            completed_at=completed_at(n),
        )
        report, photo = f"{o}/report.pdf", f"{o}/photo.png"
        snap.files[report] = report_pdf(n, "1")
        snap.files[photo] = make_png(photo_color(n))
        for suffix, kind, path in (("R", "inspection_report", report), ("P", "photo", photo)):
            snap.row(
                "artifacts",
                artifact_id=f"ART-{n:03d}-{suffix}",
                revision="1",
                is_current="true",
                inspection_id=ins,
                project_id="NC-001",
                asset_id=a,
                document_kind=kind,
                relative_path=path,
            )
        snap.row(
            "approvals",
            approval_id=f"APR-{n:03d}",
            inspection_id=ins,
            inspection_revision="1",
            evidence_digest="",
            decision="approved",
            decided_at=decided_at(n),
            decided_by=REVIEWER,
        )
    for apr in snap.tables["approvals"]:
        apr["evidence_digest"] = artifact_digest(snap, apr["inspection_id"])
    complete = {
        "coverage": "complete_for_declared_scope",
        "basis": ["synthetic_universe"],
        "consistency": "not_applicable",
    }
    snap.manifest = {
        "format": "inspection-reconcile/snapshot/v1",
        "snapshot_id": f"NC-001-{scenario_id}",
        "synthetic": True,
        "source": {"system": "fixture", "description": "North Creek synthetic baseline"},
        "capture": {"started_at": "2026-10-01T17:55:00Z", "ended_at": "2026-10-01T18:00:00Z"},
        "scope": {"file": "scope.csv", "scope_revision": "S1", "accepted": dict(ACCEPTED)},
        "datasets": {
            "inspections": {"file": "inspections.csv", **copy.deepcopy(complete)},
            "artifacts": {"file": "artifacts.csv", **copy.deepcopy(complete)},
            "approvals": {"file": "approvals.csv", **copy.deepcopy(complete)},
            "evidence_files": {"dir": "evidence", **copy.deepcopy(complete)},
        },
        "optional_datasets": {"approval_items": None},
        "normalization": None,
    }
    return snap


# ------------------------------------------------------------------------------------------ scenarios


def revise_o031(snap: Snapshot) -> None:
    snap.find("artifacts", artifact_id="ART-031-R", revision="1")["is_current"] = "false"
    snap.files["O-031/report-r2.pdf"] = report_pdf(31, "2")
    snap.row(
        "artifacts",
        artifact_id="ART-031-R",
        revision="2",
        is_current="true",
        inspection_id="INS-031",
        project_id="NC-001",
        asset_id="A-031",
        document_kind="inspection_report",
        relative_path="O-031/report-r2.pdf",
    )


def change_o009_photo(snap: Snapshot) -> None:
    snap.files["O-009/photo.png"] = make_png((1, 2, 3))


def s01(s):
    s.expected = {"apr_001_digest": s.find("approvals", approval_id="APR-001")["evidence_digest"]}


def s02(s):
    s.drop("inspections", inspection_id="INS-017")
    s.drop("artifacts", artifact_id="ART-017-R")
    s.drop("artifacts", artifact_id="ART-017-P")
    s.drop("approvals", approval_id="APR-017")
    del s.files["O-017/report.pdf"], s.files["O-017/photo.png"]


def s03(s):
    s.find("artifacts", artifact_id="ART-023-P")["project_id"] = "NC-002"


def s04(s):
    approved = s.find("approvals", approval_id="APR-031")["evidence_digest"]
    revise_o031(s)
    s.expected = {"approved_digest": approved, "current_digest": artifact_digest(s, "INS-031")}


def s05(s):
    for n in range(37, 41):
        s.drop("inspections", inspection_id=f"INS-{n:03d}")
        s.drop("artifacts", artifact_id=f"ART-{n:03d}-R")
        s.drop("artifacts", artifact_id=f"ART-{n:03d}-P")
        s.drop("approvals", approval_id=f"APR-{n:03d}")
        del s.files[f"O-{n:03d}/report.pdf"], s.files[f"O-{n:03d}/photo.png"]
    for name in ("inspections", "artifacts", "approvals", "evidence_files"):
        s.manifest["datasets"][name]["coverage"] = "partial"
        s.manifest["datasets"][name]["basis"] = ["extraction_interrupted"]


def s06(s):
    s.manifest["capture"] = {"started_at": "2026-10-02T17:55:00Z", "ended_at": "2026-10-02T18:00:00Z"}


def s07(s):
    s.row(
        "inspections",
        inspection_id="INS-012B",
        revision="1",
        is_current="true",
        obligation_id="O-012",
        project_id="NC-001",
        asset_id="A-012",
        activity_kind="visual_inspection",
        completion_status="completed",
        completed_at="2026-09-13T15:00:00Z",
    )


def s08(s):
    approved = s.find("approvals", approval_id="APR-009")["evidence_digest"]
    change_o009_photo(s)
    s.expected = {"approved_digest": approved, "current_digest": artifact_digest(s, "INS-009")}


def s09(s):
    del s.files["O-014/photo.png"]


def s10(s):
    s.drop("artifacts", artifact_id="ART-027-P")
    del s.files["O-027/photo.png"]


def s11(s):
    s.tables["scope"] = None
    del s.manifest["scope"]


def s12(s):
    del s.files["O-019/report.pdf"]
    s.manifest["datasets"]["evidence_files"]["coverage"] = "partial"
    s.manifest["datasets"]["evidence_files"]["basis"] = ["attachment_capture_skipped"]


def s13(s):
    s.files["O-033/photo-r2.png"] = make_png((9, 9, 9))
    s.row(
        "artifacts",
        artifact_id="ART-033-P",
        revision="2",
        is_current="true",
        inspection_id="INS-033",
        project_id="NC-001",
        asset_id="A-033",
        document_kind="photo",
        relative_path="O-033/photo-r2.png",
    )


def s14(s):
    original = s.find("approvals", approval_id="APR-021")
    s.row(
        "approvals",
        approval_id="APR-021-X",
        inspection_id="INS-021",
        inspection_revision="1",
        evidence_digest=original["evidence_digest"],
        decision="revoked",
        decided_at="2026-09-24T17:00:00Z",
        decided_by=REVIEWER,
    )
    assert original["decided_at"] == "2026-09-23T17:00:00Z"


def s15(s):
    s.row(
        "inspections",
        inspection_id="INS-099",
        revision="1",
        is_current="true",
        obligation_id="O-099",
        project_id="NC-001",
        asset_id="A-099",
        activity_kind="visual_inspection",
        completion_status="completed",
        completed_at="2026-09-20T15:00:00Z",
    )


def s18(s):
    for apr in s.tables["approvals"]:
        apr["evidence_digest"] = ""
        n = int(apr["approval_id"][4:])
        s.row(
            "approval_items",
            approval_id=apr["approval_id"],
            artifact_id=f"ART-{n:03d}-P",
            artifact_revision="1",
        )
        s.row(
            "approval_items",
            approval_id=apr["approval_id"],
            artifact_id=f"ART-{n:03d}-R",
            artifact_revision="1",
        )
    s.items_declared = True
    s.manifest["optional_datasets"]["approval_items"] = {"file": "approval_items.csv"}
    revise_o031(s)
    change_o009_photo(s)


def s20(s):
    s.row(
        "approvals",
        approval_id="APR-777",
        inspection_id="INS-777",
        inspection_revision="1",
        evidence_digest=s.find("approvals", approval_id="APR-001")["evidence_digest"],
        decision="approved",
        decided_at="2026-09-30T17:00:00Z",
        decided_by=REVIEWER,
    )


def s21(s):
    s.find("artifacts", artifact_id="ART-005-R")["relative_path"] = "../O-005/report.pdf"


def s22(s):
    s.find("artifacts", artifact_id="ART-008-P")["relative_path"] = "O-008/Photo.png"


def s23(s):
    s.tables["inspections"].append(dict(s.find("inspections", inspection_id="INS-004")))


def s24(s):
    s.manifest["scope"]["accepted"] = None


CANONICAL_SCENARIOS = {
    "S01-clean": s01,
    "S02-missing-inspection": s02,
    "S03-wrong-project-evidence": s03,
    "S04-revised-evidence": s04,
    "S05-incomplete-capture": s05,
    "S06-corrected": s06,
    "S07-duplicate-current": s07,
    "S08-silent-byte-change": s08,
    "S09-missing-file": s09,
    "S10-missing-document": s10,
    "S11-no-scope": s11,
    "S12-attachment-not-captured": s12,
    "S13-conflicting-current-revisions": s13,
    "S14-revoked-approval": s14,
    "S15-out-of-scope-record": s15,
    "S18-revision-binding": s18,
    "S20-dangling-reference": s20,
    "S21-invalid-path": s21,
    "S22-case-mismatch": s22,
    "S23-duplicate-key": s23,
    "S24-scope-not-accepted": s24,
}

# ------------------------------------------------------------------------------------------- writing


def csv_bytes(table: str, rows: list[dict[str, str]]) -> bytes:
    buf = io.StringIO(newline="")
    writer = csv.writer(buf, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(COLUMNS[table])
    ordered = sorted(rows, key=lambda r: tuple(r[k] for k in KEYS[table]))
    for row in ordered:
        writer.writerow([row[c] for c in COLUMNS[table]])
    return buf.getvalue().encode("utf-8")


def json_bytes(value) -> bytes:
    return (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def write_snapshot(snap: Snapshot, root: Path) -> dict[str, bytes]:
    out: dict[str, bytes] = {
        "manifest.json": json_bytes(snap.manifest),
        "project.json": json_bytes(snap.project),
    }
    if snap.tables["scope"] is not None:
        out["scope.csv"] = csv_bytes("scope", snap.tables["scope"])
    for name in ("inspections", "artifacts", "approvals"):
        out[f"{name}.csv"] = csv_bytes(name, snap.tables[name])
    if snap.items_declared:
        out["approval_items.csv"] = csv_bytes("approval_items", snap.tables["approval_items"])
    for rel, data in snap.files.items():
        out[f"evidence/{rel}"] = data
    return out


# -------------------------------------------------------------------------------- Quickbase-shaped export

TABLE_IDS = {
    "obligations": "bsyn00001",
    "inspections": "bsyn00002",
    "artifacts": "bsyn00003",
    "approvals": "bsyn00004",
}
FIELD_DEFS = {
    "obligations": [
        (6, "Obligation ID", "text"),
        (7, "Project ID", "text"),
        (8, "Scope Revision", "text"),
        (9, "Asset ID", "text"),
        (10, "Activity", "text-multiple-choice"),
    ],
    "inspections": [
        (6, "Inspection ID", "text"),
        (7, "Revision", "numeric"),
        (8, "Is Current", "checkbox"),
        (9, "Related Obligation", "numeric"),
        (10, "Project ID", "text"),
        (11, "Asset ID", "text"),
        (12, "Activity", "text-multiple-choice"),
        (13, "Status", "text-multiple-choice"),
        (14, "Completed At", "timestamp"),
    ],
    "artifacts": [
        (6, "Artifact ID", "text"),
        (7, "Revision", "numeric"),
        (8, "Is Current", "checkbox"),
        (9, "Related Inspection", "numeric"),
        (10, "Project ID", "text"),
        (11, "Asset ID", "text"),
        (12, "Document Kind", "text-multiple-choice"),
        (13, "File", "file"),
    ],
    "approvals": [
        (6, "Approval ID", "text"),
        (7, "Related Inspection", "numeric"),
        (8, "Inspection Revision", "numeric"),
        (9, "Evidence Digest", "text"),
        (10, "Decision", "text-multiple-choice"),
        (11, "Decided At", "timestamp"),
        (12, "Decided By", "user"),
    ],
}
DISPLAY_TYPE = {
    "text": "text",
    "numeric": "numeric",
    "checkbox": "checkbox",
    "timestamp": "date time",
    "text-multiple-choice": "text",
    "user": "user",
    "file": "file attachment",
    "recordid": "recordid",
}
USER = {"email": REVIEWER, "id": "000001.syn", "name": "Synthetic Reviewer", "userName": "reviewer"}
MODIFIED = "2026-10-01T12:00:00Z"
LABELS = {
    "visual_inspection": "Visual Inspection",
    "completed": "Complete",
    "inspection_report": "Inspection Report",
    "photo": "Photo",
    "approved": "Approved",
}


def fields_json(role: str) -> list[dict]:
    builtin = [
        {"id": 2, "label": "Date Modified", "fieldType": "timestamp"},
        {"id": 3, "label": "Record ID#", "fieldType": "recordid"},
    ]
    defs = builtin + [
        {"id": fid, "label": label, "fieldType": ftype} for fid, label, ftype in FIELD_DEFS[role]
    ]
    out = []
    for d in defs:
        out.append(
            {
                "id": d["id"],
                "label": d["label"],
                "fieldType": d["fieldType"],
                "mode": "",
                "required": d["id"] == 6,
                "unique": d["id"] in (3, 6),
                "properties": {},
            }
        )
    return out


def page_json(role: str, records: list[dict]) -> dict:
    fids = [2, 3] + [fid for fid, _, _ in FIELD_DEFS[role]]
    labels = {2: ("Date Modified", "timestamp"), 3: ("Record ID#", "recordid")}
    labels.update({fid: (label, ftype) for fid, label, ftype in FIELD_DEFS[role]})
    return {
        "data": records,
        "fields": [
            {"id": fid, "label": labels[fid][0], "type": DISPLAY_TYPE[labels[fid][1]]} for fid in fids
        ],
        "metadata": {
            "totalRecords": len(records),
            "numRecords": len(records),
            "numFields": len(fids),
            "skip": 0,
            "top": 1000,
        },
    }


def record(values: dict[int, object]) -> dict:
    return {str(fid): {"value": values[fid]} for fid in sorted(values)}


def build_export(
    snap: Snapshot, export_id: str, status_overrides: dict[int, str] | None = None
) -> dict[str, bytes]:
    """S01 as a Quickbase-shaped export (SPEC §14.3)."""
    status_overrides = status_overrides or {}
    out: dict[str, bytes] = {}
    obligations, inspections, artifacts, approvals, files = [], [], [], [], []
    for n in range(1, N + 1):
        o_rid, i_rid, r_rid, p_rid, a_rid = n, 100 + n, 200 + 2 * n - 1, 200 + 2 * n, 300 + n
        scope = snap.find("scope", obligation_id=f"O-{n:03d}")
        obligations.append(
            record(
                {
                    2: MODIFIED,
                    3: o_rid,
                    6: scope["obligation_id"],
                    7: scope["project_id"],
                    8: scope["scope_revision"],
                    9: scope["asset_id"],
                    10: LABELS[scope["activity_kind"]],
                }
            )
        )
        ins = snap.find("inspections", inspection_id=f"INS-{n:03d}")
        inspections.append(
            record(
                {
                    2: MODIFIED,
                    3: i_rid,
                    6: ins["inspection_id"],
                    7: int(ins["revision"]),
                    8: ins["is_current"] == "true",
                    9: o_rid,
                    10: ins["project_id"],
                    11: ins["asset_id"],
                    12: LABELS[ins["activity_kind"]],
                    13: status_overrides.get(i_rid, LABELS[ins["completion_status"]]),
                    14: ins["completed_at"],
                }
            )
        )
        for rid, suffix in ((r_rid, "R"), (p_rid, "P")):
            art = snap.find("artifacts", artifact_id=f"ART-{n:03d}-{suffix}", revision="1")
            file_name = art["relative_path"].split("/")[-1]
            path = f"files/{TABLE_IDS['artifacts']}/{rid}/13/v1/{file_name}"
            data = snap.files[art["relative_path"]]
            out[path] = data
            files.append(
                {
                    "table_id": TABLE_IDS["artifacts"],
                    "record_id": rid,
                    "field_id": 13,
                    "version": 1,
                    "file_name": file_name,
                    "path": path,
                    "bytes": len(data),
                    "sha256": sha(data),
                    "status": "captured",
                }
            )
            artifacts.append(
                record(
                    {
                        2: MODIFIED,
                        3: rid,
                        6: art["artifact_id"],
                        7: int(art["revision"]),
                        8: art["is_current"] == "true",
                        9: i_rid,
                        10: art["project_id"],
                        11: art["asset_id"],
                        12: LABELS[art["document_kind"]],
                        13: {
                            "url": f"/files/{TABLE_IDS['artifacts']}/{rid}/13",
                            "versions": [
                                {
                                    "versionNumber": 1,
                                    "fileName": file_name,
                                    "uploaded": f"2026-09-{day(n):02d}T16:00:00Z",
                                    "creator": USER,
                                }
                            ],
                        },
                    }
                )
            )
        apr = snap.find("approvals", approval_id=f"APR-{n:03d}")
        approvals.append(
            record(
                {
                    2: MODIFIED,
                    3: a_rid,
                    6: apr["approval_id"],
                    7: i_rid,
                    8: int(apr["inspection_revision"]),
                    9: apr["evidence_digest"],
                    10: LABELS[apr["decision"]],
                    11: apr["decided_at"],
                    12: USER,
                }
            )
        )
    tables = {
        "obligations": obligations,
        "inspections": inspections,
        "artifacts": artifacts,
        "approvals": approvals,
    }
    for role, records in tables.items():
        out[f"tables/{role}/fields.json"] = json_bytes(fields_json(role))
        out[f"tables/{role}/page-0001.json"] = json_bytes(page_json(role, records))
    datasets = {
        name: {k: v for k, v in decl.items() if k not in ("file", "dir")}
        for name, decl in snap.manifest["datasets"].items()
    }
    manifest = {
        "format": "inspection-reconcile/qb-export/v1",
        "export_id": export_id,
        "synthetic": True,
        "source": {
            "system": "quickbase",
            "realm_hostname": "synthetic.quickbase.invalid",
            "app_id": "bsyn00000",
            "description": "North Creek synthetic Quickbase-shaped export",
        },
        "capture": dict(snap.manifest["capture"]),
        "scope": {"scope_revision": "S1", "accepted": dict(ACCEPTED)},
        "tables": {
            role: {
                "table_id": TABLE_IDS[role],
                "select": [2, 3] + [fid for fid, _, _ in FIELD_DEFS[role]],
                "where": "{7.EX.'NC-001'}" if role == "obligations" else None,
                "pages": 1,
                "total_records": len(records),
                "retrieved": len(records),
                "two_pass": "not_run",
            }
            for role, records in tables.items()
        },
        "datasets": datasets,
        "files": files,
    }
    out["capture-manifest.json"] = json_bytes(manifest)
    return out


# --------------------------------------------------------------------------------------------- driver


def generate() -> dict[str, bytes]:
    """Every scenario file, keyed by its path relative to fixtures/scenarios."""
    files: dict[str, bytes] = {}
    for scenario_id, mutate in CANONICAL_SCENARIOS.items():
        snap = baseline(scenario_id)
        mutate(snap)
        for rel, data in write_snapshot(snap, Path()).items():
            files[f"{scenario_id}/snapshot/{rel}"] = data
        if snap.expected:
            files[f"{scenario_id}/expected.json"] = json_bytes(snap.expected)
    base = baseline("S01-clean")
    for scenario_id, overrides in (
        ("S16-quickbase-clean", {}),
        ("S17-quickbase-unmapped-value", {106: "Completed - pending QA"}),
    ):
        for rel, data in build_export(base, f"NC-001-{scenario_id}", overrides).items():
            files[f"{scenario_id}/export/{rel}"] = data
    return files


def write_tree(files: dict[str, bytes], root: Path) -> None:
    for rel, data in sorted(files.items()):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def existing_tree(root: Path) -> dict[str, bytes]:
    if not root.is_dir():
        return {}
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def check() -> int:
    expected = generate()
    actual = existing_tree(SCENARIOS_DIR)
    problems = []
    for rel in sorted(set(expected) | set(actual)):
        if rel not in actual:
            problems.append(f"missing: {rel}")
        elif rel not in expected:
            problems.append(f"unexpected: {rel}")
        elif expected[rel] != actual[rel]:
            problems.append(f"differs: {rel}")
    for p in problems[:50]:
        print(p)
    print(f"{len(expected)} generated files; {len(problems)} problem(s)")
    return 1 if problems else 0


def quickbase_import(dest: Path, decided_by: str | None = None) -> None:
    """Import CSVs for a fresh Quickbase test app (SPEC Appendix D).

    A new, empty table numbers its records 1, 2, … in import order, so each reference column holds the parent's
    1-based row position in the parent's own CSV (not the 101+/201+ record ids of the synthetic S16 export).
    A User field accepts only users of the realm: ``decided_by`` replaces the synthetic reviewer address.
    """
    snap = baseline("S01-clean")
    dest.mkdir(parents=True, exist_ok=True)
    obligation_rid = {r["obligation_id"]: n for n, r in enumerate(snap.tables["scope"], start=1)}
    inspection_rid = {r["inspection_id"]: n for n, r in enumerate(snap.tables["inspections"], start=1)}
    assert len(inspection_rid) == len(snap.tables["inspections"]), "S01 has one row per inspection"
    sheets = {
        "obligations.csv": (
            ["Obligation ID", "Project ID", "Scope Revision", "Asset ID", "Activity"],
            [
                [
                    r["obligation_id"],
                    r["project_id"],
                    r["scope_revision"],
                    r["asset_id"],
                    LABELS[r["activity_kind"]],
                ]
                for r in snap.tables["scope"]
            ],
        ),
        "inspections.csv": (
            [
                "Inspection ID",
                "Revision",
                "Is Current",
                "Related Obligation",
                "Project ID",
                "Asset ID",
                "Activity",
                "Status",
                "Completed At",
            ],
            [
                [
                    r["inspection_id"],
                    r["revision"],
                    "Yes",
                    str(obligation_rid[r["obligation_id"]]),
                    r["project_id"],
                    r["asset_id"],
                    LABELS[r["activity_kind"]],
                    LABELS[r["completion_status"]],
                    r["completed_at"],
                ]
                for r in snap.tables["inspections"]
            ],
        ),
        "artifacts.csv": (
            [
                "Artifact ID",
                "Revision",
                "Is Current",
                "Related Inspection",
                "Project ID",
                "Asset ID",
                "Document Kind",
            ],
            [
                [
                    r["artifact_id"],
                    r["revision"],
                    "Yes",
                    str(inspection_rid[r["inspection_id"]]),
                    r["project_id"],
                    r["asset_id"],
                    LABELS[r["document_kind"]],
                ]
                for r in snap.tables["artifacts"]
            ],
        ),
        "approvals.csv": (
            [
                "Approval ID",
                "Related Inspection",
                "Inspection Revision",
                "Evidence Digest",
                "Decision",
                "Decided At",
                "Decided By",
            ],
            [
                [
                    r["approval_id"],
                    str(inspection_rid[r["inspection_id"]]),
                    r["inspection_revision"],
                    r["evidence_digest"],
                    LABELS[r["decision"]],
                    r["decided_at"],
                    decided_by or r["decided_by"],
                ]
                for r in snap.tables["approvals"]
            ],
        ),
    }
    for name, (header, rows) in sheets.items():
        buf = io.StringIO(newline="")
        w = csv.writer(buf, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)
        (dest / name).write_bytes(buf.getvalue().encode("utf-8"))
    files_dir = dest / "files"
    for rel, data in snap.files.items():
        (files_dir / rel).parent.mkdir(parents=True, exist_ok=True)
        (files_dir / rel).write_bytes(data)
    print(f"wrote Quickbase import CSVs and {len(snap.files)} evidence files to {dest.as_posix()}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--check", action="store_true", help="compare a fresh generation with the committed fixtures"
    )
    parser.add_argument("--quickbase-import", metavar="DIR", help="write Quickbase import CSVs for S01")
    parser.add_argument(
        "--decided-by",
        metavar="EMAIL",
        help="with --quickbase-import: the realm user written to Decided By (default: the synthetic reviewer)",
    )
    args = parser.parse_args(argv)
    if args.check:
        return check()
    if args.quickbase_import:
        quickbase_import(Path(args.quickbase_import), args.decided_by)
        return 0
    if args.decided_by:
        parser.error("--decided-by needs --quickbase-import")
    files = generate()
    with tempfile.TemporaryDirectory(dir=REPO / "fixtures") as tmp:
        staging = Path(tmp) / "scenarios"
        write_tree(files, staging)
        if SCENARIOS_DIR.exists():
            shutil.rmtree(SCENARIOS_DIR)
        shutil.move(str(staging), str(SCENARIOS_DIR))
    print(f"wrote {len(files)} files under {SCENARIOS_DIR.relative_to(REPO).as_posix()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
