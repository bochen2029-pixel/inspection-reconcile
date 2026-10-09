from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "fixtures"
SCENARIOS = FIXTURES / "scenarios"
POLICIES = REPO / "policies"

sys.path.insert(0, str(Path(__file__).resolve().parent))


@pytest.fixture(scope="session")
def repo() -> Path:
    return REPO
