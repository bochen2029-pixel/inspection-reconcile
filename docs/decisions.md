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

## D-004 · 2026-10-09 · Amendment AM-2: entity references resolve by the entity id (SPEC §7.5.6, §22.8)
A rule-level test found that the original wording, "an inspection row with a readable key", made a garbled
revision cell on one inspection produce false `DANGLING_REFERENCE` findings for every artifact and approval of
that inspection. Those findings would also downgrade the inspections dataset with `COVERAGE_CONTRADICTED`, but
the referenced entity is present. Entity references (`inspection_id`) now resolve to any row whose
`inspection_id` cell is readable. Item references to `(artifact_id, artifact_revision)` still need the exact
key. No oracle scenario is affected.

## D-005 · 2026-10-09 · Clarified definition of done for C2 (SPEC §16.3)
A live (or mock) capture declares coverage with the bases `[query_total_matched, two_pass_stable,
operator_attestation]`, while S01 declares `[synthetic_universe]`. Declared coverage is part of the snapshot
semantic digest (§8.4), so `evaluation_id` legitimately differs. The C2 check is therefore: a capture of a mock
app built from the S01 records, normalized and assessed, gives the same `(key, outcome, reason)` for every
finding as S01, and the status READY_FOR_REVIEW. Exact identity equality remains the B3 test (S16 ≡ S01).

## D-006 · 2026-10-09 · Parallel work by helper sessions
Two other Claude Code sessions of the same operator work on claimed, file-scoped tasks in separate git worktrees
(T2: the HTTP client; T3: mapping, normalize and capture; T4: SQL export). The main session reviews and merges
each branch. The helpers also re-derived the oracle independently (T1, D-003).
