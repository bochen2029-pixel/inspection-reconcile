# Decisions and amendments

Append only. Each entry: date, decision, the SPEC section it concerns, and why.

## D-001 · 2026-10-09 · Build from SPEC v3.0
The implementation follows `docs/SPEC.md` (SPEC v3.0). Where the code and the spec disagree, the spec wins until
amended here.

## D-002 · 2026-10-09 · `demo --all` also writes `evaluation_ids.json` (SPEC §9.6, §13.4)
The cross-platform CI job compares a per-platform file of `{scenario: {evaluation_id,
assessment_semantic_sha256, report_sha256}}`. `demo --all` writes it next to `index.html`. It is one of the tool's
own output names for `--force`.
