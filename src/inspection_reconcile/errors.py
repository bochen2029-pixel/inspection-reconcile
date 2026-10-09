"""Run errors (SPEC §7.14): exit code 2, a diagnostic on stderr, and no outputs."""

from __future__ import annotations


class RunError(Exception):
    """A configuration or input defect that prevents an authoritative assessment."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
