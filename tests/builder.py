"""Build variant snapshots from the fixture generator's baseline (tools/make_fixtures.py never imports src/)."""

from __future__ import annotations

import importlib.util
import json
from collections.abc import Callable
from pathlib import Path

from conftest import POLICIES, REPO
from scenario_support import run_snapshot

_spec = importlib.util.spec_from_file_location("make_fixtures", REPO / "tools" / "make_fixtures.py")
assert _spec is not None and _spec.loader is not None
mf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mf)


def write(snap, root: Path) -> Path:
    for rel, data in mf.write_snapshot(snap, Path()).items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return root


def build(
    tmp_path: Path,
    mutate: Callable | None = None,
    policy: str | Path = "north-creek-demo",
    post: Callable[[Path], None] | None = None,
):
    """Baseline (40 obligations) + mutation → snapshot on disk → assessment."""
    snap = mf.baseline("T00-unit")
    if mutate is not None:
        mutate(snap)
    root = write(snap, tmp_path / "snapshot")
    if post is not None:
        post(root)
    policy_path = policy if isinstance(policy, Path) else POLICIES / f"{policy}.yml"
    return run_snapshot(root, policy_path)


def partial(*datasets: str, basis: str = "extraction_interrupted") -> Callable:
    def apply(snap) -> None:
        for name in datasets:
            snap.manifest["datasets"][name]["coverage"] = "partial"
            snap.manifest["datasets"][name]["basis"] = [basis]

    return apply


def chain(*mutations: Callable) -> Callable:
    def apply(snap) -> None:
        for m in mutations:
            m(snap)

    return apply


def custom_policy(tmp_path: Path, **changes: str) -> Path:
    text = (POLICIES / "north-creek-demo.yml").read_text(encoding="utf-8")
    for old, new in changes.items():
        assert old in text, old
        text = text.replace(old, new)
    path = tmp_path / "policy.yml"
    path.write_text(text, encoding="utf-8")
    return path


def edit_manifest(root: Path, fn: Callable[[dict], None]) -> None:
    path = root / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    fn(manifest)
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
