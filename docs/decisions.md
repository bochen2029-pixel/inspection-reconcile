# Decisions and amendments

Append only. Each entry: date, decision, the SPEC section it concerns, and why.

## D-001 · 2026-10-09 · Build from SPEC v3.0
The implementation follows `docs/SPEC.md` (SPEC v3.0). Where the code and the spec disagree, the spec wins until
amended here.

## D-002 · 2026-10-09 · `demo --all` also writes `evaluation_ids.json` (SPEC §9.6, §13.4)
The cross-platform CI job compares a per-platform file of `{scenario: {evaluation_id,
assessment_semantic_sha256, report_sha256}}`. `demo --all` writes it next to `index.html`. It is one of the tool's
own output names for `--force`.

## D-003 · 2026-10-09 · Amendment AM-1 (SPEC §22, A.1, §5.6, §6.1, §7.12, §14.2)
A second reviewer re-derived the whole Appendix A oracle from the spec text alone, without reading any code, and
found **no oracle errors**. The review also showed that the oracle relied on conventions the text never stated.
AM-1 states them:
- defaults for `reason` (`BLOCKED_BY_UPSTREAM`), `blocked_by` and `caused_by` (`[]`) in A.1;
- the key of a NOT_EVALUATED R7 (`R7:snapshot:all`);
- whole-value, ASCII-only grammar matching, and the TS bounds;
- non-empty `document_kinds` lists;
- the wording of the S05 change and the columns of added fixture rows;
- the full `expected`/`observed` table and the `evidence` table;
- `blocked_by` ordering; the R6 step-10 tie;
- exclusive cell classification and the single-rule PATH reporting;
- multi-subject templates; quarantined `raw` contents; the probe without an evidence root.

The oracle YAML is unchanged.
