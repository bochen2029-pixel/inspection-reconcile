import pytest

from inspection_reconcile import validate as v
from inspection_reconcile.errors import RunError
from inspection_reconcile.yamlsafe import loads_yaml


def test_plain_document_loads():
    assert loads_yaml("a: 1\nb: [x, y]\nc: {d: true}\n", "t") == {"a": 1, "b": ["x", "y"], "c": {"d": True}}


@pytest.mark.parametrize(
    "doc, fragment",
    [
        ("a: 1\na: 2\n", "duplicate key"),
        ("x: &anchor 1\ny: *anchor\n", "anchors are not allowed"),
        ("base: {a: 1}\nother: *base\n", "aliases are not allowed"),
        ("a: !!str 1\n", "explicit tags are not allowed"),
        ("a: !custom 1\n", "explicit tags are not allowed"),
        ("a:\n  <<: {b: 1}\n", "merge keys are not allowed"),
        ("outer:\n  inner: 1\n  inner: 2\n", "duplicate key"),
    ],
)
def test_rejected_features(doc, fragment):
    with pytest.raises(RunError) as err:
        loads_yaml(doc, "t")
    assert fragment in str(err.value)


def test_unquoted_yes_key_is_not_a_string():
    data = loads_yaml("values: {Yes: completed}\n", "t")
    with pytest.raises(RunError) as err:
        v.obj(data["values"], "values", {}, {"Yes": v.string})
    assert "quote it" in str(err.value)


def test_unquoted_date_is_not_a_string():
    data = loads_yaml("effective_from: 2026-09-01\n", "t")
    with pytest.raises(RunError):
        v.date_string(data["effective_from"], "effective_from")
