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

## D-007 · 2026-10-09 · Amendment AM-3: mapping and export completions (SPEC §12.3, §22.9)
Gaps found while implementing B1/B2 (G1-G6), adopted with the conservative reading:
- **G1, a corrected pairing.** Field 2 maps only as `timestamp` and field 3 only as `recordid`.
- **G2, a `recordid` type.** It is added to the type and conversion tables.
- **G3, mapping consistency rules.** Type and column compatibility, references only on link columns, required
  value maps, the scope filter tied to `project_id`, and a table-level `allow_derived`.
- **G4, the hash input.** The long-file-name hash is taken over the original name.
- **G5, a closed-world export manifest.**
- **G6, export consistency.** `normalize` refuses inconsistent exports (`EXPORT_INVALID`).

## D-006 · 2026-10-09 · Parallel work by helper sessions
Two other Claude Code sessions of the same operator work on claimed, file-scoped tasks in separate git worktrees
(T2: the HTTP client; T3: mapping, normalize and capture; T4: SQL export). The main session reviews and merges
each branch. The helpers also re-derived the oracle independently (T1, D-003).

## D-008 · 2026-10-09 · Amendment AM-4: capture details (SPEC §12.2, §12.5, §22.10)
Choices made while implementing C2 (C-1 to C-6), adopted as written:
- **C-1, the two-pass result.** A capture writes `stable` or `changed`.
- **C-2, file entries.** An uncaptured entry carries no `path` or `sha256`. Only `too_large` records `bytes`.
  `normalize` now enforces this.
- **C-3, coverage declarations.** These cover what each incomplete, unstable, unattested, skipped or failed case
  declares.
- **C-4, names and times.** The `export_id` and description formats.
- **C-5, a stalled keyset.** A non-empty page without keyset progress stops the read as an accounting failure.
- **C-6, stored responses.** Captured responses are parsed and re-serialized, not raw bytes.

## D-009 · 2026-10-09 · Amendment AM-5: engine review corrections (SPEC §7.5.2, §7.5.7, §8.4, §22.1, §22.11)
A spec-only review of every engine module found three defects. None of them is reached by any oracle scenario:
- **The scope member.** A declared scope member with an absent file hashed like no member at all, so different
  evaluations shared one `evaluation_id`.
- **`DUPLICATE_KEY` comparisons.** `identical` compared `x_` provenance columns.
- **Entity attribution.** It ignored inspection rows whose revision cell was unreadable, which AM-2 had already
  ruled out for references.

The review also found one false sentence, about the sort position of `null`, and five wording ambiguities. All
are fixed, each defect with a regression test that fails on the old code. No oracle entry, vector or fixture
identity changed.

## D-010 · 2026-10-09 · The skip-paging fallback (SPEC §12.1, §22.10)
§12.1 named a fallback for a source that refuses the `{3.GT.n}` comparison, and nothing implemented it. It is now
built as specified in the AM-4 addendum (helper task T3b, reviewed):
- **Trigger.** It fires only on HTTP status 400 for a table's first keyset query. The client raises a typed
  error that carries the status, so server text cannot trigger it.
- **The read.** Skip reads run until an empty page, because Quickbase can return short pages mid-table, and
  they use per-page total accounting.
- **Stall guard.** A source that ignores `skip` stops the read instead of spinning to `max_pages`.
- **The manifest** records the mode in an optional `tables[].paging` member.

C3 confirms which mode a real realm needs.

## D-011 · 2026-10-09 · Amendment AM-6: Appendix D import files (SPEC Appendix D, §22.12)
Preparing C3 showed that the generated import files would not have built a working test app:
- **References.** Artifacts and approvals pointed at inspection record IDs 101 and up, which a fresh table does
  not have. Every reference is now the parent's 1-based import position.
- **Decided By.** The approvals file lacked the required `Decided By`, so every live approval would have been
  quarantined. It is now included, with `--decided-by` for a realm user.

A test checks that every reference column resolves to the parent named by S01's canonical snapshot.
`docs/c3-runbook.md` gives the procedure. The fixtures are unchanged (`--check`: 0 problems).

## D-012 · 2026-10-09 · Amendment AM-7: adapter review corrections (SPEC §7.5.1, §12.3, §22.9, §22.13)
A spec-only review of the adapters reproduced four ways in which a capture or export artifact became a FAIL or
PASS that the data does not establish:
- **A re-delivered record** became a `DUPLICATE_KEY` FAIL. `normalize` now keeps the first occurrence when the
  read is already reported as failed, and refuses the export otherwise.
- **A grammar-valid unmapped label** was accepted. Listed cells now always report `UNMAPPED_VALUE`.
- **An unexpected JSON type** could satisfy its grammar: `123` in a text field, for example, gave a false R3
  FAIL. It is now listed as unmapped.
- **A file version with no `files[]` entry** became a `FILE_ABSENT` FAIL. It is now refused.

Each fix has a regression test that fails without it. The fixture identities are unchanged. The fifth finding,
a download held in memory before its size check, is helper task T5: a streamed download with a cap.

## D-013 · 2026-10-09 · Streamed downloads with a size cap (SPEC §12.4, §12.5 step 3, §22.10)
Before, a file far over `max_file_bytes` was fully downloaded and decoded, using roughly 3.7 times its size in
memory, before being marked `too_large`. A huge attachment could end the run instead.
- **Streaming.** Every client request now streams, and the status line decides a retry before any body is read.
- **The cap.** `download_file` stops once the base64 payload passes 4·⌈max/3⌉ characters, so memory is O(cap).
- **The recorded size.** A `too_large` entry records `bytes` only when the whole body was read; a download
  stopped early leaves it null.

Helper task T5 implemented this, with 36 tests, and it was reviewed before the merge.

## D-014 · 2026-10-09 · Amendment AM-8: foundations review corrections (SPEC §5.4, §6.2, §7.5.1, §22.14)
A spec-only review of the canonical JSON, the grammars, the YAML loader, the policy, the snapshot loader and the
probe found them exact, apart from two defects:
- **CSV records.** One defective CSV record aborted the whole run. This contradicted §7.14, under which a defective
  record is never a run error. It now becomes `MALFORMED_ROW`, and reading resumes.
- **YAML integers.** YAML 1.1 integer forms silently changed configuration numbers: `fid: 010` became field 8.
  Integers are now plain decimal.

The reviewer's patch added 18 tests, 12 of which fail without it. The review's notes on record numbering,
`field_count`, `-00:00` and the probe's time-of-check gap are recorded in the spec and in `docs/limitations.md`.

## D-015 · 2026-10-09 · Amendment AM-9: output-surface review corrections (SPEC §9.3, §9.4, §9.6, §11, §22.15)
A spec-only review of the outputs, the report, `compare`, the runner, `demo` and the CLI confirmed that the
templates, the member orders, the exit codes, the escaping, the CSP and determinism were all exact. It found the
following defects:
- **`--force`** could not replace the previous output of `normalize` or `capture-quickbase`. A refused run could
  also delete files. Output is now checked and staged first.
- **`assess --export` provenance** recorded deleted temporary paths and not the export. The decision is option A,
  extended: the snapshot inputs keep their digests with `path: null`, and `export_manifest`, `export_table` and
  `mapping` are added as inputs.
- **The demo** did not keep an export scenario's normalized snapshot.
- **`--log-json`** missed library log records.
- **Report wording:** the "not approval" phrase was missing, and "blocks 0" was hidden.
- **`index.html`** linked reports that a run error had prevented.
- **`compare`:** a malformed assessment gave an internal error, and `--out` overwrote silently. It now takes
  `--force` and never overwrites an input.
- **Small items:** the policy is read once, and a failed write removes the directories it created.

Helper task T5-surfaces made these fixes with 20 tests, failing first where they apply. The lead added the
single policy read and its test.

## D-016 · 2026-10-09 · Amendment AM-10: an all-or-nothing `--force` swap (SPEC §9.6, §22.16)
The pre-publication go/no-go review (G1) found that a failed `--force` swap destroyed the previous output, which
§9.6 forbids. Reproduced on Windows with one CSV held open: 86 of 87 previous files were gone. The swap now runs
as two phases of renames inside `--out`, with a full rollback; the previous output is deleted only after the new
one is in place, and a `.previous-*` directory is never deleted automatically.

There are 6 tests, 4 of which fail on the old writer. The review's informational notes are handled as follows:
- **Documented:** `assess` and `demo` are not staged.
- **Kept as is:** `--log-json` relies on `basicConfig`, and hard links to `compare` inputs are not caught; both are
  minor.
- **Fixed:** `make_screenshots.py` cleanup.

## D-017 · 2026-10-09 · Amendment AM-11: a create-only builder for the Appendix D test app (SPEC §22.17, Appendix D)
Building Appendix D by hand takes 29 fields, 200 records and 80 uploads, and every step is a chance to break the
field ids. Helper task T7 wrote `tools/qb_build_test_app.py`, which does it with one command. It is bounded to six
create and read operations, only inside the app it creates, and outside the package. An independent review
attacked it, and a follow-up made "only creates" a check in its client: an `upsert` may not carry `mergeFieldId`
or Record ID#.

The request it was built to differed in three ways, each forced by the API:
- **Decided By** is the token's own user, because the JSON API documents only `{"id"}` for user writes.
- **Write failures are not retried,** because a failed write may have taken effect.
- **Obligation ID's unique and required are left unset,** because `createField` cannot set them and the
  allowlist has no update.

The runbook's fast path keeps the token in the environment variable that `capture-quickbase` reads.
