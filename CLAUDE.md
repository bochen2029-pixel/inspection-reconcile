# CLAUDE.md: inspection-reconcile working agreement

Read `docs/SPEC.md` (normative) and `docs/status.md` before any work. The spec wins over code. Amendments
and decisions go to `docs/decisions.md` (append only).

## Machine rules: Windows 11 workstation (non-negotiable)
- NEVER wrap or kill native Windows programs with Git Bash `timeout`, `kill` or `pkill`, and never use `taskkill /T`.
  An MSYS2 tree-kill over a recycled PID crashed this machine (bugcheck 0xEF, 2026-10-09). Bound runtime with
  the tool's own timeout parameter, or `subprocess.run(timeout=)` inside Python. No retry loops against a hung tool.
- No heredocs or here-strings (a hook blocks them). Create files with the Write and Edit tools. Commit with
  `git commit -F <message-file>`. Keep shell commands under 8,000 characters.
- Forward slashes in every path. Set `PYTHONIOENCODING=utf-8` for Python in Git Bash. Use `MSYS_NO_PATHCONV=1`
  for arguments that start with `/`.
- Use `uv run …`. uv lives at C:/Users/user/AppData/Local/hermes/bin/uv.exe. Never `python3` (the Store stub),
  never pip-install into the system Python.
- Leave Everything/es/facet, the GPU, WSL environments, Docker, other sessions and their processes alone.
  No background servers.
- Remote repositories are private. Making one public or publishing a site is the owner's decision. Never commit
  credentials; `QB_USER_TOKEN` exists only in the environment.

## Commands
    uv sync --all-extras
    uv run pytest -q
    uv run ruff check . && uv run ruff format --check .
    uv run mypy
    uv run inspection-reconcile demo --all --out out/demo --force
    uv run python tools/make_fixtures.py --check

## Codebase rules
- `fixtures/oracle.yaml` (SPEC Appendix A) and the Appendix B vectors are never edited to make code pass.
- Tests never compute expected verdicts with `src/`. The fixture generator never imports `src/`.
- The engine is pure: no clock, network or randomness; evidence is read only through `EvidenceStore`.
- Outputs are UTF-8 bytes with LF line endings, written atomically.
- Public data is synthetic (North Creek); emails use example.invalid. No employer or client names, anywhere.
- Session protocol: SPEC §16.1. Step plan and status: SPEC §16.3 and docs/status.md.
