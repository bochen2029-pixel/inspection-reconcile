"""Foundations review: configuration integers are plain decimal (SPEC §6.2, the YAML 1.1 traps).

YAML 1.1 reads 0100 as octal 64, 1:30 as sexagesimal 90 and 0x64 as hexadecimal 100. Those forms must stay strings,
so that the closed-world validators reject them where an integer is expected, instead of silently using another number.
"""

from pathlib import Path

import pytest
from conftest import POLICIES, REPO

from inspection_reconcile.adapters.mapping import parse_mapping
from inspection_reconcile.errors import RunError
from inspection_reconcile.policy import load_policy
from inspection_reconcile.yamlsafe import loads_yaml


@pytest.mark.parametrize(
    ("text", "value"),
    [("0", 0), ("7", 7), ("-5", -5), ("+7", 7), ("104857600", 104857600)],
)
def test_plain_decimal_integers_load_as_integers(text: str, value: int) -> None:
    assert loads_yaml(f"a: {text}", "t") == {"a": value}


@pytest.mark.parametrize("text", ["0100", "010", "07", "1:30", "0x64", "0b11", "1_000"])
def test_other_yaml_1_1_integer_forms_stay_strings(text: str) -> None:
    assert loads_yaml(f"a: {text}", "t") == {"a": text}


def test_an_octal_looking_policy_limit_is_rejected_not_read_as_64(tmp_path: Path) -> None:
    text = (POLICIES / "north-creek-demo.yml").read_text(encoding="utf-8")
    assert "max_file_bytes: 104857600" in text
    path = tmp_path / "policy.yml"
    path.write_text(text.replace("max_file_bytes: 104857600", "max_file_bytes: 0100"), encoding="utf-8")
    with pytest.raises(RunError) as info:
        load_policy(path)
    assert info.value.code == "CONFIG_INVALID"
    assert "expected an integer" in info.value.message


def test_a_leading_zero_fid_is_rejected_not_read_as_another_field() -> None:
    text = (REPO / "mappings" / "quickbase-demo.yml").read_text(encoding="utf-8")
    assert "fid: 10," in text
    with pytest.raises(RunError) as info:
        parse_mapping(loads_yaml(text.replace("fid: 10,", "fid: 010,", 1), "m"), "m")
    assert info.value.code == "CONFIG_INVALID"
    assert "expected an integer" in info.value.message  # not some later rule tripping over field 8
