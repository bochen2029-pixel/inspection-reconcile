"""SPEC Appendix B vectors and the §8.1 canonical-domain guards."""

import hashlib

import pytest

from inspection_reconcile.canonical import CanonicalError, canonical, evidence_set_digest

REPORT_V2 = hashlib.sha256(b"report bytes v2").hexdigest()
PHOTO_V1 = hashlib.sha256(b"photo bytes v1").hexdigest()
PHOTO_V2 = hashlib.sha256(b"photo bytes v2").hexdigest()

V1 = [
    {"artifact_id": "ART-T-01", "document_kind": "inspection_report", "revision": "2", "sha256": REPORT_V2},
    {"artifact_id": "ART-T-02", "document_kind": "photo", "revision": "1", "sha256": PHOTO_V1},
]
V1_BYTES = (
    '{"artifacts":[{"artifact_id":"ART-T-01","document_kind":"inspection_report","revision":"2",'
    '"sha256":"b2a0ee266b0d91a8f336f2670788660f9af02f7786731f8e48cdd8f1c91a2f75"},'
    '{"artifact_id":"ART-T-02","document_kind":"photo","revision":"1",'
    '"sha256":"bef35f09d275a850ccf60737b083c23dcc2f92bc29e269f0fad972e490dedbed"}],'
    '"scheme":"inspection-reconcile/evidence-set/v1"}'
)


def test_content_digests():
    assert REPORT_V2 == "b2a0ee266b0d91a8f336f2670788660f9af02f7786731f8e48cdd8f1c91a2f75"
    assert PHOTO_V1 == "bef35f09d275a850ccf60737b083c23dcc2f92bc29e269f0fad972e490dedbed"
    assert PHOTO_V2 == "328ddc0fd972786ab7db817f25a87074ceca8f03117594a01ab456d234915d2f"


def test_v1_bytes_and_digest():
    assert canonical({"artifacts": V1, "scheme": "inspection-reconcile/evidence-set/v1"}) == V1_BYTES.encode()
    assert len(V1_BYTES.encode()) == 359
    assert (
        evidence_set_digest(V1) == "sha256:e1c7a825b339db9678f73f6c0bfd9139daaca6fab91d74d366ff0d8b3a4c9d2a"
    )


def test_v2_order_invariant():
    assert evidence_set_digest(list(reversed(V1))) == evidence_set_digest(V1)


def test_v3_changed_bytes():
    v3 = [V1[0], dict(V1[1], sha256=PHOTO_V2)]
    assert (
        evidence_set_digest(v3) == "sha256:331e31150872399fa8304a7f051deb6d095e58267e2cc65f290cf682b96cfdad"
    )


def test_v4_empty_set():
    assert (
        canonical({"artifacts": [], "scheme": "inspection-reconcile/evidence-set/v1"})
        == b'{"artifacts":[],"scheme":"inspection-reconcile/evidence-set/v1"}'
    )
    assert (
        evidence_set_digest([]) == "sha256:9266ce7ec70da3bf0e8cafb3931d0aab65a4d43e1df48de2791afbb844a812dc"
    )


@pytest.mark.parametrize(
    "value",
    [{"x": 1.5}, {"x": 2**53}, {"x": -(2**53)}, {"é": 1}, {"x": "lone\ud800"}, {"x": {1, 2}}, {1: "a"}],
)
def test_guards(value):
    with pytest.raises(CanonicalError):
        canonical(value)


def test_non_ascii_values_raw_utf8_and_control_escapes():
    assert canonical({"a": "é\x01"}) == '{"a":"é\\u0001"}'.encode()
    assert canonical({"n": 2**53 - 1, "b": True, "z": None}) == b'{"b":true,"n":9007199254740991,"z":null}'
