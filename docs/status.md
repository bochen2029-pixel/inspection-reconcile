# Status

Step plan: SPEC §16.3. Each step ends green (`pytest`, `ruff check`, `ruff format --check`, `mypy`).

| step | state | notes |
|---|---|---|
| A0 scaffold | done | layout, uv lock, CI matrix, CLAUDE.md, docs |
| A1 foundations | done | grammars, restricted YAML, validators, snapshot loading, evidence probe, atomic writer |
| A2 canonical | done | canonical JSON with guards; Appendix B vectors reproduced |
| A3 fixtures and oracle | done | independent generator (1,977 files), oracle equals Appendix A, self-consistency, byte reproducibility |
| A4 engine | done | all 22 canonical scenarios match the oracle; FI-1..FI-5; P1-P3; AM-1, AM-2, AM-5 |
| A5 surfaces | done | JSON, HTML, compare (AC-19), demo, evidence-digest, CLI. **Checkpoint A** |
| A6 SQL | done | helper task T4: SQLite export, Appendix G queries, differential test with query mutations (AC-30) |
| B1 mapping | done | helper task T3: every mapping rule, AM-3 consistency rules |
| B2 normalize | done | helper task T3: conversions, references, file names, closed-world manifest, `EXPORT_INVALID` |
| B3 export fixtures | done | S16 normalized equals S01 in `evaluation_id` and `assessment_semantic_sha256` (AC-14); S17 matches its oracle; P2 covers the export. **Checkpoint B** |
| C1 client | done | helper task T2: four-operation allowlist, rate limit, 429/Retry-After, base64 download, redaction |
| C2 capture | done | helper task T3: keyset reads, two-pass check, coverage declarations, files policy; AM-4. **Checkpoint C-mock** (D-005) |
| C3 live verification | operator-gated | needs a Quickbase account, a test app and a user token; follow `docs/c3-runbook.md` |
| D1 publication | operator-gated | the repository is private; publication is the owner's decision |
| R review | in progress | the invariant and AC audit below is done; independent reviews applied: oracle (D-003), engine (D-009), adapters (D-012, D-013); the output-surfaces review is pending |

## Review pass R: invariants (SPEC §3)

| invariant | evidence |
|---|---|
| I-1 independent expected work | obligations come only from scope rows (`integrity.run`); S11 (no scope: UNKNOWN), S15 (out-of-scope record: advisory R7), S24 (scope not accepted) |
| I-2 open-world evidence | P3 coverage monotonicity; FI-3 (coverage forced complete makes S05/S12 fail); S05, S12 |
| I-3 four outcomes, three statuses | closed vocabularies in `vocab.py`; the oracle checks every scenario's status |
| I-4 explained and traceable | `tests/scenario/test_explained.py`: reason, explanation, resolution and locators on every finding of every scenario (§22.2 exemptions only) |
| I-5 deterministic and pure | `tests/meta/test_engine_purity.py` (no clock, network, randomness, environment or file access in `engine/`, with a scanner self-test); P2 idempotence; identical `evaluation_ids.json` across runs |
| I-6 approvals bind versions | R6 reads the approved digest from the approval record; FI-2 (every digest "matches") makes S04/S08 fail |
| I-7 read-only, least privilege | the client allowlist refuses everything but four read operations before any I/O; token redaction in errors, reprs, logs and every written file (connector tests) |
| I-8 closed configuration | the restricted YAML loader (no duplicate keys, aliases, tags or merge keys); closed policy, mapping, capture-config and export-manifest schemas |
| I-9 no silent interpretation | S17 (unmapped value: UNKNOWN); unknown columns, members and types are run errors; AM-3 `EXPORT_INVALID` refusals |
| I-10 synthetic public data | `test_no_private_terms_in_tracked_files`; `test_public_spec_has_no_private_preface` |
| I-11 cross-platform identity | CI `identical-across-platforms` job compares `evaluation_ids.json` (identities and report digests) from Windows, macOS and Linux |
| I-12 independent oracles | `test_oracle_equals_spec_appendix_a`; `test_generator_never_imports_src`; the oracle was re-derived from the text alone by a second session (D-003) |
| I-13 unique, stable keys | `assess` raises on a duplicate key; P1 row-order invariance |
| I-14 bytes on disk | `test_outputs_are_lf_utf8_bytes`; `test_write_files_bytes_exact`; `test_a_failed_write_leaves_neither_partial_outputs_nor_temporary_files` (every write goes through a temporary file and `os.replace`) |

## Review pass R: acceptance criteria (SPEC §15.2)

| AC | evidence |
|---|---|
| AC-01..AC-11, AC-15..AC-17, AC-26, AC-27 | `tests/scenario/test_scenarios.py` (every canonical scenario equals its oracle entry) with the scenario-specific detail tests; FI-1..FI-3 and P3 where §15.2 names them |
| AC-12 | `test_malformed_policy_exit_2_and_no_outputs`, `test_internal_exception_exit_2_without_traceback` |
| AC-13 | `test_p1_row_order_invariance` |
| AC-14 | `test_assess_export_matches_canonical_identity`, adapter equivalence tests |
| AC-18 | S17 through `normalize` and `assess --export` (UNKNOWN, never guessed) |
| AC-19 | `test_compare_s02_s06_ac19` |
| AC-20 | `tests/fault/test_faults.py` (controls pass without faults; each injection breaks its targets) |
| AC-21 | the CI matrix and its comparison job |
| AC-22 | `test_hostile_strings_are_escaped`, `test_no_external_resources_and_csp` |
| AC-23 | S21; the probe's unit tests (symlinks, junctions, reparse points, unsafe paths) |
| AC-24 | `tests/vectors/test_canonical.py` |
| AC-25 | `test_s18_revision_binding_limitation_is_stated` |
| AC-28 | S22; `test_fi4_case_insensitive_probe`; the CI matrix |
| AC-29 | S23; `test_fi5_duplicate_detection_off`; `test_s23_duplicate_observed` |
| AC-30 | `tests/sql/test_sql.py` (differential agreement and query mutations) |
| AC-31 | `tests/connector/test_qb_client.py` (allowlist, redaction); `tests/connector/test_qb_capture.py` (token absent from every file and log) |
