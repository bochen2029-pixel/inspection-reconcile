import pytest

from inspection_reconcile.errors import RunError
from inspection_reconcile.io.writer import prepare_out, write_files
from inspection_reconcile.policy import parse_policy
from inspection_reconcile.yamlsafe import loads_yaml

POLICY = """
schema: inspection-reconcile/policy/v1
pack_id: north-creek-demo
version: "3.0.0"
effective_from: "2026-09-01"
synthetic: true
project_id: NC-001
scope_revision: S1
coverage:
  accepted_bases:
    - [synthetic_universe]
    - [query_total_matched, two_pass_stable, operator_attestation]
requirements:
  visual_inspection:
    document_kinds: [inspection_report, photo]
approval:
  required_binding: digest
limits:
  max_file_bytes: 104857600
checks:
  - {id: R0, type: input_integrity,        required: true}
  - {id: R1, type: scope_and_coverage,     required: true}
  - {id: R2, type: inspection_cardinality, required: true}
  - {id: R3, type: identity_consistency,   required: true}
  - {id: R4, type: required_artifacts,     required: true}
  - {id: R5, type: artifact_availability,  required: true}
  - {id: R6, type: approval_binding,       required: true}
  - {id: R7, type: unmatched_records,      required: false}
"""


def test_policy_parses():
    p = parse_policy(loads_yaml(POLICY, "p"), "p")
    assert p.pack_id == "north-creek-demo"
    assert p.requirements == {"visual_inspection": ("inspection_report", "photo")}
    assert p.accepted_bases[1] == frozenset(
        {"query_total_matched", "two_pass_stable", "operator_attestation"}
    )
    assert p.sha256.startswith("sha256:")


def test_policy_digest_ignores_formatting():
    a = parse_policy(loads_yaml(POLICY, "p"), "p")
    b = parse_policy(
        loads_yaml(
            POLICY.replace("[inspection_report, photo]", "\n      - inspection_report\n      - photo"), "p"
        ),
        "p",
    )
    assert a.sha256 == b.sha256


@pytest.mark.parametrize(
    "old, new",
    [
        ('version: "3.0.0"', "version: 3.0"),
        ('effective_from: "2026-09-01"', "effective_from: 2026-09-01"),
        ("required: false}", "required: true}"),
        ("{id: R7, type: unmatched_records,      required: false}", "{id: R7, type: other, required: false}"),
        ("required_binding: digest", "required_binding: bytes"),
        ("max_file_bytes: 104857600", "max_file_bytes: 0"),
        ("synthetic: true", "synthetic: true\nextra: 1"),
        ("[synthetic_universe]", "[synthetic_universe, synthetic_universe]"),
        ("document_kinds: [inspection_report, photo]", "document_kinds: [Inspection_Report]"),
    ],
)
def test_policy_rejections(old, new):
    assert old in POLICY
    with pytest.raises(RunError):
        parse_policy(loads_yaml(POLICY.replace(old, new, 1), "p"), "p")


def test_prepare_out_protocol(tmp_path):
    out = tmp_path / "out"
    prepare_out(out, force=False)  # absent is fine
    out.mkdir()
    prepare_out(out, force=False)  # empty is fine
    (out / "assessment.json").write_text("{}")
    (out / "keep.txt").write_text("user file")
    with pytest.raises(RunError) as err:
        prepare_out(out, force=False)
    assert err.value.code == "OUT_NOT_EMPTY"
    prepare_out(out, force=True)
    assert not (out / "assessment.json").exists()
    assert (out / "keep.txt").exists()


def test_write_files_bytes_exact(tmp_path):
    out = tmp_path / "o"
    write_files(out, {"a.json": b'{"x": 1}\n', "sub/b.html": "é\n".encode()})
    assert (out / "a.json").read_bytes() == b'{"x": 1}\n'
    assert (out / "sub" / "b.html").read_bytes() == "é\n".encode()
    assert not list(out.glob("*.tmp.*"))


def test_a_failed_write_leaves_neither_partial_outputs_nor_temporary_files(tmp_path, monkeypatch):
    import os

    real_replace = os.replace
    calls = []

    def failing_replace(src, dst):
        calls.append(dst)
        if len(calls) == 2:
            raise OSError(28, "No space left on device")
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", failing_replace)
    out = tmp_path / "o"
    with pytest.raises(RunError) as err:
        write_files(out, {"a.json": b"{}\n", "b.json": b"{}\n", "c.json": b"{}\n"})
    assert err.value.code == "WRITE_FAILED"
    # a.json was undone, no .tmp file is left, and the directory this run created is removed too
    assert not out.exists()
