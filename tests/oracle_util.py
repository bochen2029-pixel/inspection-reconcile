"""Test-side oracle reader (independent of src/; SPEC Appendix A.1)."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

RANGE = re.compile(r"^(?P<prefix>.*O-)(?P<lo>[0-9]{3})\.\.(?P<hi>[0-9]{3})$")


def load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _entry(raw: dict, key: str, subject_id: str | None) -> dict:
    def sub(values):
        return [v.replace("{id}", subject_id) if subject_id else v for v in values]

    check_id = key.split(":", 1)[0]
    return {
        "key": key,
        "outcome": raw["outcome"],
        "reason": raw.get("reason"),
        "blocked_by": sub(raw.get("blocked_by", [])),
        "caused_by": sub(raw.get("caused_by", [])),
        "required": raw.get("required", check_id != "R7"),
    }


def expand(non_pass: list[dict]) -> list[dict]:
    out: list[dict] = []
    for raw in non_pass:
        m = RANGE.match(raw["key"])
        if m is None:
            out.append(_entry(raw, raw["key"], None))
            continue
        for n in range(int(m["lo"]), int(m["hi"]) + 1):
            out.append(_entry(raw, f"{m['prefix']}{n:03d}", f"O-{n:03d}"))
    return out


def expected_total(scenario: dict) -> int:
    entries = expand(scenario["non_pass"])
    r0 = sum(1 for e in entries if e["key"].startswith("R0:")) or 1
    r7 = sum(1 for e in entries if e["key"].startswith("R7:")) or 1
    return r0 + 5 + 5 * scenario["obligations"] + r7
