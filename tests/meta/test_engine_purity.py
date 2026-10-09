"""I-5: the engine is pure. It reads no clock, network, randomness, environment or file system; evidence arrives only
through the EvidenceStore it is given (SPEC §3, CLAUDE.md codebase rules)."""

import ast

import pytest
from conftest import REPO

ENGINE = sorted((REPO / "src" / "inspection_reconcile" / "engine").glob("*.py"))
FORBIDDEN_MODULES = {
    "asyncio",
    "http",
    "httpx",
    "os",
    "pathlib",
    "random",
    "secrets",
    "shutil",
    "socket",
    "subprocess",
    "threading",
    "time",
    "urllib",
    "uuid",
}
FORBIDDEN_CALLS = {
    "now",
    "utcnow",
    "today",
    "open",
    "read_bytes",
    "read_text",
    "write_bytes",
    "write_text",
    "getenv",
}


def impurities(source: str) -> list[str]:
    problems = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names = [node.module]
        else:
            names = []
        problems += [
            f"line {node.lineno}: imports {n}" for n in names if n.split(".")[0] in FORBIDDEN_MODULES
        ]
        if isinstance(node, ast.Call):
            func = node.func
            name = (
                func.attr
                if isinstance(func, ast.Attribute)
                else func.id
                if isinstance(func, ast.Name)
                else None
            )
            if name in FORBIDDEN_CALLS:
                problems.append(f"line {node.lineno}: calls {name}()")
    return problems


def test_the_scanner_catches_impurity():
    source = (
        "import time\nfrom os.path import join\nx = datetime.now(UTC)\ny = open('f')\nz = p.read_bytes()\n"
    )
    assert impurities(source) == [
        "line 1: imports time",
        "line 2: imports os.path",
        "line 3: calls now()",
        "line 4: calls open()",
        "line 5: calls read_bytes()",
    ]


def test_the_engine_package_is_where_the_test_expects():
    assert {p.name for p in ENGINE} >= {"assess.py", "checks.py", "integrity.py", "identity.py"}


@pytest.mark.parametrize("path", ENGINE, ids=lambda p: p.name)
def test_engine_module_is_pure(path):
    assert impurities(path.read_text(encoding="utf-8")) == []
