import subprocess
import sys

from inspection_reconcile import __version__


def test_version_string():
    assert __version__ == "0.1.0"


def test_cli_version_runs():
    proc = subprocess.run(
        [sys.executable, "-m", "inspection_reconcile", "--version"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0
    assert "inspection-reconcile 0.1.0" in proc.stdout
