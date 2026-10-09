# inspection-reconcile
## Implementation specification v3.0: documentation-readiness checking with a Quickbase adapter

**Version:** 3.0 · **Date:** 2026-10-09 · **Status:** normative build specification.
**Public-safety:** this document is committed as `docs/SPEC.md`. It contains no employer, client or private context, and every data example is synthetic. Quickbase is a trademark of Quickbase, Inc. This project is not affiliated with or endorsed by Quickbase.

> Compare the work a project requires with the inspection records, documents and approvals actually captured. Explain every gap with its source evidence, and never report "ready" on evidence the tool could not see.

---

## 0 · How to use this document

- **MUST**, **MUST NOT**, **SHOULD** and **MAY** are normative (RFC 2119). Every section is normative unless it is marked *(informative)*.
- **Precedence.** Where code and this document disagree, this document wins until it is amended. A disagreement is recorded in `docs/decisions.md` with a proposed amendment; it is never resolved silently in code.
- **Oracles.** Appendix A (the scenario oracle) and Appendix B (digest vectors) are acceptance oracles. They were derived from this text, not from code. Tests MUST NOT regenerate them from the implementation. The implementation MUST NOT edit them to make a test pass. An oracle may change only through a spec amendment that states which semantic rule forced the change.
- **Build order** is §16. Every step ends at a demonstrable, green checkpoint.
- **Amendments.** §22 holds every normative change adopted during the build. Read it with the section it amends: where they differ, §22 wins.

| part | sections | answers |
|---|---|---|
| Intent | 1–4 | what it is, what changed, invariants, scope |
| Contracts | 5–6 | the snapshot, the requirement pack |
| Semantics | 7–8 | how every check decides; identities |
| Surfaces | 9–11 | outputs, reason codes, command line |
| Integration | 12 | Quickbase export, mapping, live capture |
| Build | 13–16 | architecture, fixtures, tests, phased plan and the machine rules |
| Guardrails | 17–21 | security, claims, extensions, open questions, sources |
| Appendices | A–G | oracle, vectors, glossary, test-app blueprint, native parity, repository `CLAUDE.md`, SQL |
| Amendments | 22 | normative changes adopted during the build, each with its decision |

---

## 1 · Purpose

Build **`inspection-reconcile`**: a small, deterministic Python command-line utility. It decides whether a project's inspection documentation package is ready for review under an explicit, versioned requirement pack.

It reads a **captured snapshot**: canonical CSV and JSON files, or a Quickbase-shaped export through an explicit field mapping. It evaluates a fixed set of checks and writes a JSON assessment, a self-contained HTML report and a run manifest.

**The one question:** for this accepted project scope and this requirement pack, what prevents the captured documentation from being ready for review? The answer is specific, and it names the records that serve as evidence.

Three facts justify the tool:

1. **Valid rows are not complete work.** Thirty-nine valid inspection records cannot satisfy forty obligations. Expected work comes from an independently accepted scope. It is never inferred from the records being checked.
2. **Absence from a partial view is not absence.** A missing record proves nothing unless the capture is known to be complete. The tool keeps an established failure separate from "could not determine".
3. **Approvals go stale.** An approval covers a specific inspection revision and specific evidence. A later change to either is detected, at the strength the approval's binding allows.

`READY_FOR_REVIEW` is a statement about documentation, under the configured requirements, for the captured snapshot. It is not a judgment of physical inspection quality, regulatory compliance or client acceptance.

---

## 2 · Changes from v2.0 *(informative)*

v3.0 keeps v2.0's architecture and semantics. It fixes defects found by re-deriving every rule, re-verifies every external fact, and adds what a Claude Code session on the author's machine needs.

| # | change | defect or gap in v2.0 |
|---|---|---|
| 1 | Evidence `relative_path` is relative to the **evidence root**; fixtures use `O-001/report.pdf` | v2.0's fixtures wrote `evidence/O-001/report.pdf` while §7.10 resolved under the evidence root, which would look for `evidence/evidence/…` |
| 2 | Finding keys are unique by construction: R0 keys carry `#<CODE>`; revision rows use `@<rev>`; items use `/`; R7 is keyed by inspection entity (§9.2) | two R0 defects on one artifact, or two current revisions of one out-of-scope inspection, produced duplicate keys, which `compare` cannot handle |
| 3 | Unattributable rows are keyed by a content digest, not a row number | row-number keys break P1 order invariance |
| 4 | An explicit, ordered R0 algorithm: grammar → duplicates (whole group quarantined) → multiple current → completion timestamp → time → dangling (§7.5) | v2.0 left duplicate rows in evaluation, and duplicate current rows raised two overlapping findings |
| 5 | R0 findings are required only when they concern the assessed scope; out-of-scope defects are advisory (§7.5.7) | any defect anywhere in a full-table capture blocked an unrelated project |
| 6 | `required` is fixed per check: R0–R6 are required (R0 per §7.5.7), R7 is advisory; the pack cannot change this | v2.0 let R4 be advisory, but its failure still made required R6 `NOT_EVALUATED`, which contradicts "advisory findings never change the status" |
| 7 | Revision-mode R6 compares only the required artifacts and ignores extra approved items; FAIL details and template are defined for both binding modes (§7.11) | exact set equality failed approvals that also covered a non-required sketch; the FAIL template assumed digests existed |
| 8 | A new `evidence-digest` command computes the digest a review process should store (§11) | v2.0 required approvals to carry a digest that nothing could produce |
| 9 | `MALFORMED_ROW` for a CSV row with the wrong field count (§7.5.1) | ragged rows were unspecified |
| 10 | Timestamps: a strict regular expression, at most six fractional digits, uppercase `T`/`Z`, no leap seconds; `effective_from` is midnight UTC (§5.6, §6) | Python's `fromisoformat` accepts far more than RFC 3339; the date-to-time comparison was undefined |
| 11 | PATH grammar forbids `<>:"\|?*` (no NTFS alternate data streams); reparse points and junctions are refused on Python ≥ 3.12 through `lstat` attributes (§5.6, §7.10) | `photo.png:stream` passed the grammar; junction detection was unspecified for 3.11 |
| 12 | Canonical JSON guards: integers within ±(2⁵³ − 1), no lone surrogates (§8.1) | needed for strict RFC 8785 equivalence |
| 13 | Per-record provenance is excluded from every identity (§8.4) | v2.0's exclusion list said "source", which is ambiguous; S16 ≡ S01 depended on it |
| 14 | All outputs are written as UTF-8 bytes with LF line endings, atomically; `--force` removes only this tool's own output names (§9.6) | text-mode writes on Windows emit CRLF, breaking cross-platform byte identity |
| 15 | YAML loading rejects duplicate keys, non-string keys where strings are expected, aliases and tags (§6.2) | PyYAML silently keeps the last duplicate key, and turns a `Yes`/`No` choice label into a boolean |
| 16 | Quickbase facts re-verified against the official OpenAPI document and the rendered portal pages (§12.1) | v2.0 relied on secondary sources for the query, file and field contracts |
| 17 | Capture is full-table with keyset pagination by Record ID#, an interleaved two-pass check across all tables, and per-page total accounting (§12.5) | filtering child tables by their own project field hid the wrong-project records that R3 exists to find; skip-based pagination can drop rows when rows are deleted mid-capture |
| 18 | Schema verification uses `GET /fields` `fieldType`/`mode`, never the query response's `fields[].type` labels (§12.5) | the query response reports display labels such as `"date time"` |
| 19 | The HTTP client has an allowlist of read operations; `DELETE` is impossible and file `versionNumber` must be ≥ 1 (§12.4) | the download path is shared with a delete operation for which version 0 means "the most recent" |
| 20 | Seven new scenarios (S18–S24): revision binding, insufficient binding strength, dangling reference, invalid path, case-only mismatch, duplicate key, unaccepted scope | v2.0 left R6 revision mode, dangling references and the path rules untested at scenario level |
| 21 | An oracle self-consistency test and two new fault injections, FI-4 (case-insensitive probe) and FI-5 (duplicate detection off) (§15) | arithmetic errors in an oracle would otherwise go unnoticed |
| 22 | An SQLite export and an independent SQL cross-check of R2, R3, R4 and R7 (§A6, Appendix G) | the role requires SQL validation; a second implementation strengthens the oracle |
| 23 | Build order puts the fixtures and oracle **before** the engine (§16.3) | tests first, with oracles authored independently of the engine |
| 24 | §16.2 machine rules, including the incident rule: never wrap or kill native Windows programs with Git Bash `timeout`/`kill` | a 2026-10-09 survey ran `timeout` on a native tool in an elevated session; MSYS2 walked a recycled PID's "process tree" and killed system services, ending in bugcheck 0xEF |
| 25 | Appendix F is the full text of the repository `CLAUDE.md`; Appendix D is a buildable Quickbase test-app blueprint; Appendix E maps each check to native Quickbase constructs | v2.0 referred to a `CLAUDE.md` and a test app it never specified |

---

## 3 · Invariants

These hold in every phase. A change that breaks one needs a spec amendment first.

- **I-1 · Independent expected work.** Obligations come only from the accepted scope. The engine MUST NOT create, infer or drop an obligation because of inspection, artifact or approval records.
- **I-2 · Open-world evidence.** An absence-based FAIL is allowed only when the relevant dataset's effective coverage is complete (§7.3). A universal requirement ("exactly one", "all files readable", "no later revocation") needs complete coverage to PASS.
- **I-3 · Four outcomes, three statuses.** Every finding is `PASS`, `FAIL`, `UNKNOWN` or `NOT_EVALUATED`. The project status is `BLOCKED`, `UNKNOWN` or `READY_FOR_REVIEW` (§7.13). `READY_FOR_REVIEW` is never final approval.
- **I-4 · Explained and traceable.** Every finding carries a reason code, expected and observed values, an explanation, what would resolve it, and source locators (§9.2).
- **I-5 · Deterministic and replayable.** Evaluation reads no wall clock, no network and no randomness. The same semantic inputs give the same `evaluation_id`, findings and report bytes (§8).
- **I-6 · Approvals bind versions.** An approval's expected binding comes from the approval record. The engine MUST NOT regenerate an expected value from current files.
- **I-7 · Read-only and least-privilege.** No writeback to any source. Credentials never appear in files, URLs, logs, reports or exceptions.
- **I-8 · Closed configuration.** Requirement packs and mappings are declarative YAML validated against a schema. No expression language, no `eval`, and a restricted safe loader (§6.2).
- **I-9 · No silent interpretation.** An unmapped value, ambiguous link, unknown column or type mismatch becomes a visible finding or a run error. It is never guessed.
- **I-10 · Synthetic public data.** Public fixtures, examples and screenshots use the fictional North Creek project only.
- **I-11 · Cross-platform identity.** Windows, macOS and Linux produce identical `evaluation_id`s, findings, `assessment.json` bytes and `report.html` bytes for the same inputs.
- **I-12 · Independent oracles.** Expected outcomes are authored from this document. Fixture digests come from an independent implementation in the generator, never from `src/`.
- **I-13 · Unique, stable keys.** Within one assessment every finding key is unique, and a key depends only on semantic identity (never on row order, file paths or time).
- **I-14 · Bytes on disk.** Every output is written as UTF-8 bytes with `\n` line endings, through a temporary file and an atomic rename.

---

## 4 · Scope

### 4.1 First user *(informative)*

An auditor, records coordinator or applications developer who is preparing a project's documentation package for review.

### 4.2 Inputs

1. An **accepted scope**: the list of required inspection activities (obligations), with a revision and an acceptance record.
2. **Inspection records** exported from an application.
3. An **artifact inventory** (documents and photos) and the evidence files it points to.
4. **Approval decisions**, each with the inspection revision it covers and its evidence binding.
5. A **requirement pack**: a versioned YAML policy.
6. A **snapshot manifest** declaring what was captured and how complete each dataset is, with the basis for that claim.
7. For a Quickbase-shaped export only: a **field mapping**.

### 4.3 Outputs

`assessment.json`, `report.html` and `run-manifest.json` per assessment (§9); `comparison.json` per comparison; an SQLite file per `export-sqlite`.

### 4.4 In scope for v3.0

- One fictional project (North Creek, 40 obligations) and two requirement packs: digest binding (the default) and revision binding.
- Canonical snapshots; Quickbase-shaped exports with a mapping; checks R0–R7; 24 oracle scenarios.
- The CLI: `validate`, `assess`, `normalize`, `compare`, `demo`, `evidence-digest`, `export-sqlite`, `capture-quickbase`.
- JSON, HTML and SQLite outputs.
- A read-only live connector, tested against a mock transport built from the official API contract. Live testing against an authorized Quickbase test app is a separate, operator-gated step (C3).

### 4.5 Non-goals

- **No changes to sources:** writeback, automatic remediation, or edits to any source system.
- **No adjacent operations:** scheduling, payroll, invoicing, mobile or field capture.
- **No content or regulatory judgment:** OCR, document-content interpretation, regulatory interpretation, GIS geometry.
- **No rule programming:** arbitrary rule logic or an expression language.
- **No platform features:** tenancy, user accounts, a hosted database, a web server, continuous monitoring or agents.
- **Later rule families:** inspector qualification evidence (course completion, evaluation, qualification and client acceptance must be kept apart, in their own pack family; §19); supersession rules for repeat inspections (v3.0 requires exactly one current completed inspection per obligation); scoped (filtered) live capture with reference closure (§12.5.7).

---

## 5 · Input contract: the canonical snapshot

### 5.1 Directory layout

```
<snapshot>/
  manifest.json          required
  project.json           required
  scope.csv              the scope file named by manifest.scope.file (absent ⇒ R1 SCOPE_MISSING)
  inspections.csv        as declared in the manifest
  artifacts.csv          as declared in the manifest
  approvals.csv          as declared in the manifest
  approval_items.csv     optional; revision-mode bindings (§7.11)
  normalization.json     optional; written by `normalize` (§12.3)
  evidence/              the evidence root; its name is manifest.datasets.evidence_files.dir
    O-001/report.pdf     relative_path "O-001/report.pdf"
```

- **Declared files must exist.** A dataset the manifest declares MUST exist on disk, otherwise the run fails (exit 2).
- **Omitted datasets.** A dataset the manifest omits is treated as empty and not complete (R1 `DATASET_MISSING`).
- **Scope exception.** A missing scope (no `scope` member, or its file absent) is never a run error. It is the finding R1 `SCOPE_MISSING`, so a run with no expected work always yields an explicit UNKNOWN.
- **Evidence root.** The evidence root MUST be a real directory: not a symbolic link, junction or other reparse point. Otherwise the run fails (exit 2).
- **`relative_path`** is always relative to the evidence root.

### 5.2 `manifest.json`

```json
{
  "format": "inspection-reconcile/snapshot/v1",
  "snapshot_id": "NC-001-S01-clean",
  "synthetic": true,
  "source": {"system": "fixture", "description": "North Creek synthetic baseline"},
  "capture": {"started_at": "2026-10-01T17:55:00Z", "ended_at": "2026-10-01T18:00:00Z"},
  "scope": {
    "file": "scope.csv",
    "scope_revision": "S1",
    "accepted": {"by": "Synthetic Client Records Dept", "at": "2026-08-15T14:00:00Z", "reference": "SYN-SCOPE-S1"}
  },
  "datasets": {
    "inspections":    {"file": "inspections.csv", "coverage": "complete_for_declared_scope", "basis": ["synthetic_universe"], "consistency": "not_applicable"},
    "artifacts":      {"file": "artifacts.csv",   "coverage": "complete_for_declared_scope", "basis": ["synthetic_universe"], "consistency": "not_applicable"},
    "approvals":      {"file": "approvals.csv",   "coverage": "complete_for_declared_scope", "basis": ["synthetic_universe"], "consistency": "not_applicable"},
    "evidence_files": {"dir": "evidence",         "coverage": "complete_for_declared_scope", "basis": ["synthetic_universe"], "consistency": "not_applicable"}
  },
  "optional_datasets": {"approval_items": null},
  "normalization": null
}
```

| member | rule |
|---|---|
| `format` | exactly `inspection-reconcile/snapshot/v1` |
| `snapshot_id` | ID grammar (§5.6); provenance only |
| `synthetic` | boolean; the report shows a synthetic-data banner when this, the project or the policy is synthetic |
| `source` | `{system: TEXT, description: TEXT}`; provenance only |
| `capture` | `started_at` ≤ `ended_at`, both TS; `ended_at` is the default `as_of` (§7.1) |
| `scope` | optional; `{file, scope_revision: REV, accepted: null \| {by: TEXT, at: TS, reference: TEXT}}`; absent ⇒ `SCOPE_MISSING`; `accepted: null` ⇒ `SCOPE_NOT_ACCEPTED` |
| `datasets` | an object whose members are a subset of `inspections`, `artifacts`, `approvals`, `evidence_files` |
| `datasets.<n>.file` / `.dir` | `file` for the three CSV datasets, `dir` for `evidence_files`; a single path segment matching the PATH grammar |
| `datasets.<n>.coverage` | `complete_for_declared_scope` · `partial` · `unverified` |
| `datasets.<n>.basis` | a non-empty list of distinct basis tokens, each matching `^[a-z][a-z0-9_]{0,63}$` |
| `datasets.<n>.consistency` | `not_applicable` · `stable_verified` · `changed_during_capture` |
| `optional_datasets.approval_items` | `null`, or `{file}`; it shares the approvals coverage declaration; declaring it without an `approvals` dataset is a run error |
| `normalization` | `null` or a file name (§12.3) |

Every member is required except `scope` and `normalization`. Unknown members at any level, a wrong JSON type, or an invalid value are run errors (exit 2).

**Evidence coverage.** For `evidence_files`, `complete_for_declared_scope` means this: every file referenced by a current artifact linked to an in-scope obligation's inspection was captured, if it exists in the source.

### 5.3 `project.json`

`{"project_id": "NC-001", "client_id": "CL-SYN-01", "name": "North Creek (synthetic)", "synthetic": true}`. All four members are required, and unknown members are rejected. `project_id` and `client_id` are IDs, `name` is TEXT and `synthetic` is boolean. `project_id` MUST equal the policy's `project_id`, otherwise the run fails (exit 2, diagnostic `PROJECT_MISMATCH`).

### 5.4 CSV dialect

- **Decoding.** Strict UTF-8; a leading byte-order mark is removed. Invalid UTF-8, or a NUL byte, makes the file unreadable: a run error.
- **Format.** RFC 4180 quoting, a comma delimiter and one header row, read with Python's `csv` module and `newline=""`. Writers (the generator and `normalize`) use `lineterminator="\n"` and `QUOTE_MINIMAL`.
- **Header.** It MUST contain exactly the columns in §5.5, in any order, each once. A column beginning `x_` is allowed; it is ignored by evaluation and kept in provenance. Any other column, a duplicated column or a missing column is a run error. An empty file with no header is a run error.
- **Rows.**
  - A blank line (the reader yields an empty list) is ignored.
  - A row whose field count differs from the header is not a run error: it is R0 `MALFORMED_ROW` (§7.5.1).
  - A record the csv module cannot parse is not a run error either (AM-8). Examples are text after a closing quote, or a quoted field that never closes. It is R0 `MALFORMED_ROW`, its one raw cell is the record's verbatim text, and reading resumes at the next line. Only a parse error in the header row is a run error.
  - Cells have no length limit at reading time; an over-long cell fails its grammar (AM-8).
  - An empty cell is `null`.
  - Values are never trimmed. Leading or trailing whitespace makes an ID, enum or timestamp invalid.
  - Booleans are exactly `true` or `false`.
- **Locators.** Row numbers count CSV records, not physical lines: they are 1-based, with the header as row 1, and blank lines are not counted. A quoted cell can span lines, so the two can differ (AM-8). Row numbers appear only in locators.

### 5.5 Entities

**`scope.csv`**, one row per obligation: `obligation_id` ID · `project_id` ID · `scope_revision` REV · `asset_id` ID · `activity_kind` KIND. All are required. Key: `obligation_id`.

**`inspections.csv`**. Key: `(inspection_id, revision)`. Entity: `inspection_id`. Link: `obligation_id`.

| column | type | required | notes |
|---|---|---|---|
| inspection_id | ID | yes | entity id; its revisions share it |
| revision | REV | yes | |
| is_current | BOOL | yes | at most one current revision per entity |
| obligation_id | ID | no | the only link to an obligation; `null` means unlinked |
| project_id | ID | yes | |
| asset_id | ID | yes | |
| activity_kind | KIND | yes | |
| completion_status | enum | yes | `completed` · `in_progress` · `not_started` · `cancelled` |
| completed_at | TS | no | required when the row is current and completed (R0 `COMPLETED_WITHOUT_TIMESTAMP`) |

**`artifacts.csv`**. Key: `(artifact_id, revision)`. Entity: `artifact_id`. Link: `inspection_id`.

| column | type | required | notes |
|---|---|---|---|
| artifact_id | ID | yes | |
| revision | REV | yes | |
| is_current | BOOL | yes | at most one current revision per entity |
| inspection_id | ID | yes | links to the inspection entity (any revision) |
| project_id | ID | yes | |
| asset_id | ID | yes | |
| document_kind | KIND | yes | |
| relative_path | PATH | no | relative to the evidence root; `null` means no file |

**`approvals.csv`**. Key: `approval_id`. Link: `inspection_id`.

| column | type | required | notes |
|---|---|---|---|
| approval_id | ID | yes | |
| inspection_id | ID | yes | |
| inspection_revision | REV | yes | the revision this decision covers |
| evidence_digest | DIGEST | no | content-digest binding (§8.3) |
| decision | enum | yes | `approved` · `rejected` · `revoked` |
| decided_at | TS | yes | orders decisions |
| decided_by | TEXT | yes | |

**`approval_items.csv`** (optional): `approval_id` ID · `artifact_id` ID · `artifact_revision` REV, all required. Key: `(approval_id, artifact_id)`. Each row states one artifact revision that the approval covers.

### 5.6 Value grammars

| type | rule |
|---|---|
| ID | `^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$`, compared exactly (case-sensitive, no normalization) |
| REV | `^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$`, compared exactly as a string |
| KIND | `^[a-z][a-z0-9_]{0,63}$` |
| BOOL | `true` or `false` |
| TS | `^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z\|[+-]\d{2}:\d{2})$`, and a valid calendar date and time (seconds 00–59, offset hours ≤ 23). Normalized to UTC and serialized as `YYYY-MM-DDTHH:MM:SS.ffffffZ` |
| DIGEST | `^sha256:[0-9a-f]{64}$` |
| TEXT | 1–200 Unicode code points, with no character of category Cc and no surrogate |
| PATH | the path rules below |

Every pattern is applied to the whole value (`re.fullmatch`); character classes are ASCII only, so `[0-9]` never matches other Unicode digits, and a trailing newline or space makes a value invalid. TS bounds: hour 00–23, minute 00–59, second 00–59, offset hours 00–23, offset minutes 00–59, and 1–6 fractional digits (AM-1).

**PATH rules.** A violation is R0 `INVALID_PATH`, and the file is never opened.
- **Segments.** `/`-separated and non-empty; no `.` or `..` segment; no segment ending in `.` or a space.
- **Relative only.** No leading `/`.
- **Forbidden characters.** None of `\ < > : " | ? *` and no control character (Cc).
- **Reserved names.** No Windows reserved device name as a segment, case-insensitively, with or without an extension: `CON`, `PRN`, `AUX`, `NUL`, `COM1`–`COM9`, `LPT1`–`LPT9`.
- **Length.** At most 240 characters in total, and at most 100 characters per segment.

### 5.7 Keys, links and current revisions

- **Keys** are listed in §5.5. A repeated key is R0 `DUPLICATE_KEY` (§7.5.2).
- **Current revisions.** At most one non-quarantined row per inspection entity, and per artifact entity, may have `is_current = true`. More is R0 `MULTIPLE_CURRENT_REVISIONS`. Currency is read only from `is_current`, never inferred from timestamps or revision order.
- **Links** are exact key equality: no fuzzy matching, display-name matching or inference.
  - **Inspection → obligation:** by `obligation_id`.
  - **Artifact → inspection:** by `inspection_id`, at entity level, regardless of revision.
  - **Approval → inspection:** by `inspection_id`, with `inspection_revision` for coverage.
  - **Item → approval and artifact:** by `approval_id` and `(artifact_id, artifact_revision)`.
- **Mappings** (§12.3) MAY derive a link only through an explicit reference rule.

### 5.8 Coverage, basis and effective coverage

Coverage is a **claim**. Each dataset declares `coverage`, a `basis` list and `consistency`. Basis tokens are an open vocabulary; these are documented:

| basis token | meaning |
|---|---|
| `synthetic_universe` | a fixture whose universe is known by construction |
| `query_total_matched` | every page was read, and the retrieved count equals the source's reported total for the declared query |
| `two_pass_stable` | a second pass found the same record IDs and modification times (§12.5) |
| `operator_attestation` | a named person attests the reading identity can see every record in scope |
| `ui_export` | a manual export from an application's user interface |
| `extraction_interrupted` | an incomplete capture |
| `attachment_capture_skipped` | file bytes were deliberately not captured |
| `pagination_incomplete` | the connector could not account for every page |

The policy lists **accepted basis sets** (§6). A declared `complete_for_declared_scope` is honored only when at least one accepted set is a subset of the dataset's declared basis.

**Effective coverage** is computed after R0 and before any per-obligation check. Every applicable reason is collected in `observed.reasons`. The first applicable reason, in this fixed order, becomes the R1 dataset finding's reason:

| order | reason | when | effective |
|---|---|---|---|
| 1 | `DATASET_MISSING` | the manifest omits the dataset | `unverified` |
| 2 | `COVERAGE_PARTIAL` | declared `partial` | `partial` |
| 3 | `COVERAGE_UNVERIFIED` | declared `unverified` | `unverified` |
| 4 | `COVERAGE_BASIS_NOT_ACCEPTED` | declared complete, but no accepted basis set is satisfied | `unverified` |
| 5 | `CHANGED_DURING_CAPTURE` | `consistency = changed_during_capture` | `unverified` |
| 6 | `COVERAGE_CONTRADICTED` | a record elsewhere references a record absent from this dataset (R0 `DANGLING_REFERENCE`) | `partial` |
| 7 | `UNATTRIBUTABLE_RECORDS` | this dataset contains a quarantined row that cannot be attributed (§7.5.7) | `partial` |

A dataset is **complete** only if no reason applies. `partial` and `unverified` behave identically in evaluation; the difference is informational. The `approval_items` file shares the approvals dataset's coverage, so reasons arising from items apply to `approvals`.

### 5.9 Provenance

Every normalized record carries a source reference `{system, dataset, locator, source_record_id?, file_version?}`. `system` is `fixture` or `quickbase`. `locator` is, for example, `inspections.csv#row=18` or `table=bsyn00002;rid=117`.

Source references appear only in a finding's `evidence` list. They never appear in `expected`, `observed`, a key, or any identity digest.

---

## 6 · Requirement pack (policy)

### 6.1 Format

```yaml
schema: inspection-reconcile/policy/v1
pack_id: north-creek-demo
version: "3.0.0"
effective_from: "2026-09-01"
synthetic: true
project_id: NC-001
scope_revision: S1
coverage:
  accepted_bases:                 # any one set suffices; it must be a subset of a dataset's declared basis
    - [synthetic_universe]
    - [query_total_matched, two_pass_stable, operator_attestation]
requirements:                     # activity_kind -> required current document kinds
  visual_inspection:
    document_kinds: [inspection_report, photo]
approval:
  required_binding: digest        # digest | revisions
limits:
  max_file_bytes: 104857600
checks:
  - {id: R0, type: input_integrity,        required: true}
  - {id: R1, type: scope_and_coverage,     required: true}
  - {id: R2, type: inspection_cardinality, required: true}
  - {id: R3, type: identity_consistency,   required: true}
  - {id: R4, type: required_artifacts,     required: true}
  - {id: R5, type: artifact_availability,  required: true}
  - {id: R6, type: approval_binding,       required: true}
  - {id: R7, type: unmatched_records,      required: false}
```

**Validation rules.** A violation is a run error (exit 2).
- **Shape.**
  - Every member shown is required, and unknown members are rejected at every level.
  - `checks` MUST equal exactly the eight entries shown, in that order. The `required` values are fixed by this specification and listed for readability; they cannot be changed. R0's per-finding requiredness follows §7.5.7.
- **Values.**
  - `pack_id` is an ID and `version` is a quoted string.
  - `effective_from` is a quoted string `^\d{4}-\d{2}-\d{2}$` naming a valid date. It means midnight UTC of that date.
  - `synthetic` is a boolean; `project_id` is an ID; `scope_revision` is a REV.
  - `accepted_bases` is a non-empty list of non-empty lists of distinct basis tokens.
  - Every requirement key and every document kind is a KIND. Each `document_kinds` list is non-empty and has no duplicates (AM-1).
  - `required_binding` is `digest` or `revisions`; `max_file_bytes` is an integer from 1 to 2³¹ − 1.
- **Consistency with the inputs.**
  - The policy's `project_id` equals `project.json`'s (`PROJECT_MISMATCH`).
  - `as_of` is not before `effective_from` (`POLICY_NOT_EFFECTIVE`).

The demonstration ships two packs: `policies/north-creek-demo.yml` (as shown) and `policies/north-creek-revisions.yml` (identical except `pack_id: north-creek-revisions` and `required_binding: revisions`).

A pack is a demonstration policy. Real client requirements must be supplied and reviewed separately, because application configuration cannot establish business intent.

### 6.2 YAML loading

All YAML (policies, mappings, capture configuration, the oracle) is loaded with a `yaml.SafeLoader` subclass that additionally:
- **Rejects duplicate keys** in any mapping;
- **Rejects aliases, anchors and explicit tags;**
- **Rejects non-string keys** where a string key is expected. For example, an unquoted `Yes:` in a value map parses as a boolean and is rejected with "quote this key".
- **Reads integers as plain decimal only** (an optional sign, no leading zeros) (AM-8). YAML 1.1's octal, sexagesimal, hexadecimal, binary and underscore forms (`0100`, `1:30`, `0x64`, `0b11`, `1_000`) load as strings. A validator then rejects them where an integer is expected, instead of silently using another number.

The policy SHA-256 (§8.5) is computed over the canonical JSON of the parsed document, so comments and formatting do not change it.

---
## 7 · Evaluation semantics

### 7.1 Pipeline

1. **Load and validate** the manifest, project, policy, optional normalization file and CSV headers. A configuration defect is a run error (§7.14). A defective *record* never is: it becomes an R0 finding.
2. **Fix `as_of`.** Use `--as-of` if given, otherwise `capture.ended_at`. If `as_of` is before `effective_from` (midnight UTC), the run fails (exit 2).
3. **R0** (§7.5): findings, quarantine, attribution, requiredness, and the coverage reasons it creates.
4. **Probe evidence** (§7.10) for every non-quarantined artifact row with a non-null `relative_path`. Results are cached per path and reused by R5 and §8.4.
5. **R1** (§7.6): the scope finding and the four dataset findings, with effective coverage (§5.8).
6. **Index:** current inspections by obligation; current artifacts by inspection entity; approvals by inspection; items by approval.
7. **Per obligation** in ascending `obligation_id` order: R2, then R3–R6 under the dependencies of §7.4.
8. **R7** (§7.12).
9. **Status** (§7.13), identities (§8), outputs (§9).

All iteration is over sorted keys. Python string order (code points) is the only ordering.

### 7.2 Outcomes

| outcome | meaning |
|---|---|
| `PASS` | the requirement holds in every completion of the captured data |
| `FAIL` | the requirement fails in every completion: an established defect |
| `UNKNOWN` | the captured data cannot decide. The finding states what would resolve it, and `caused_by` names the coverage finding behind it when there is one |
| `NOT_EVALUATED` | not run, because a prerequisite did not pass. `blocked_by` lists the direct prerequisite findings |

### 7.3 The open-world rule

A **completion** of a snapshot is any superset of its captured records that adds records only to datasets whose effective coverage is not complete. A dataset with complete effective coverage has exactly one completion: itself.

A check outcome is **PASS** if the requirement holds in every completion, **FAIL** if it fails in every completion, and **UNKNOWN** otherwise. Two consequences follow, and the truth tables below implement them:

- **Existential requirements** ("at least one photo exists") PASS on a captured witness. They FAIL only under complete coverage.
- **Universal requirements** ("exactly one", "no conflict", "every file readable", "no later revocation") FAIL on a captured counterexample. They PASS only under complete coverage.

### 7.4 Dependencies and `NOT_EVALUATED`

Let S be the set of scope obligations (§7.6). For each obligation O in S:

| check | evaluated when | otherwise `NOT_EVALUATED`, with `blocked_by` = |
|---|---|---|
| R2(O) | the R1 project finding is PASS **and** no non-PASS R0 finding is attributed to O (§7.5.7) | the R1 project finding if it is not PASS, else every R0 finding attributed to O |
| R3(O) | R2(O) is PASS, or UNKNOWN `CARDINALITY_UNCONFIRMED` (exactly one candidate) | R2(O) |
| R4(O) | R3(O) is PASS or UNKNOWN | R3(O) |
| R5(O) | R3(O) is PASS or UNKNOWN **and** E(O) is non-empty (§7.9) | R3(O) if R3 failed that test, else R4(O) |
| R6(O) | R4(O) is PASS **and** R5(O) is PASS | each of R4(O) and R5(O) that is not PASS |
| R7 | the R1 project finding is PASS | the R1 project finding |

`blocked_by` lists finding keys in ascending key order (§9.2). The report derives root causes by following `blocked_by` transitively. No advisory finding can block a required one: R7, the only advisory check, has no dependents, and an R0 finding attributed to O is required by §7.5.7.

### 7.5 R0 · input integrity

R0 runs in the fixed order below. Each step sees the quarantine decisions of the steps before it. **Readable** means that a cell satisfies its grammar.

#### 7.5.1 Row validation

- **`MALFORMED_ROW` (UNKNOWN).** A row whose field count differs from the header, or a record the csv module cannot parse. The latter has `field_count` 1, because its one raw cell is the verbatim text (AM-8). The row is quarantined and unattributable. Identical malformed rows in one dataset merge into one finding with `observed.count`.
- **Cell validation.** Every other row has each schema column checked against its type, its required rule and its enum. Each violation is classified:
  - `INVALID_PATH` for a `relative_path` grammar violation;
  - `UNMAPPED_VALUE` for a cell that the snapshot's normalization file lists as unmapped (§12.3), even when the cell satisfies its grammar (AM-7);
  - `INVALID_VALUE` for everything else.

  A row with any violation is **quarantined**. Each code produces one finding per row subject. All rows that share a subject and a code merge into one finding, and `observed.violations` lists the distinct `{field, value, rule}` triples, sorted.
- **`rule` vocabulary** (deterministic):
  - `required`, `grammar:ID`, `grammar:REV`, `grammar:KIND`, `grammar:BOOL`, `grammar:TS`, `grammar:DIGEST`, `grammar:TEXT`;
  - `enum:<v1|v2|…>`, and `unmapped`;
  - `path:empty-segment`, `path:dot-segment`, `path:absolute`, `path:forbidden-character`, `path:control-character`, `path:trailing-dot-or-space`, `path:reserved-name`, `path:too-long`, `path:segment-too-long`.

#### 7.5.2 Duplicate keys (`DUPLICATE_KEY`, FAIL)

Group every row with a readable key, including rows quarantined in §7.5.1, by its key (§5.5). Each group of two or more rows yields one finding:
- **subject** — the key;
- **observed** — `{count, identical}`, where `identical` means every row has equal raw schema cells (`x_` columns excluded, §5.4; AM-5).

**Every row in the group is quarantined**, because the record's identity is ambiguous. Quarantined rows still resolve references (§7.5.6). A key held by a quarantined row is not "missing".

#### 7.5.3 Multiple current revisions (`MULTIPLE_CURRENT_REVISIONS`, FAIL)

For inspections and artifacts, among non-quarantined rows, an entity with two or more `is_current = true` rows yields one finding:
- **subject** — the entity id;
- **observed** — `{current_revisions}`, sorted.

The rows stay unquarantined; the conflict reaches evaluation through attribution (§7.5.7).

#### 7.5.4 `COMPLETED_WITHOUT_TIMESTAMP` (FAIL)

A non-quarantined inspection row with `is_current = true`, `completion_status = completed` and a null `completed_at`.

#### 7.5.5 `TIMESTAMP_AFTER_AS_OF` (UNKNOWN)

A non-quarantined inspection row whose `completed_at`, or approval row whose `decided_at`, is later than `as_of`.

#### 7.5.6 Dangling references (`DANGLING_REFERENCE`, UNKNOWN)

These references are checked on non-quarantined rows, and only when the target dataset is **declared** `complete_for_declared_scope` in the manifest. The test uses the declared value, not the effective one, so effective coverage cannot depend on itself.

| reference | the target must exist as | the target dataset that gains `COVERAGE_CONTRADICTED` |
|---|---|---|
| artifact `inspection_id` | an inspection row whose `inspection_id` cell is readable and equal, whatever its revision cell and whether quarantined or not (AM-2) | inspections |
| approval `inspection_id` | the same | inspections |
| item `approval_id` | an approval row with a readable key | approvals |
| item `(artifact_id, artifact_revision)` | an artifact row with exactly that key | artifacts |

There is one finding per referencing row, and `observed.missing` lists each `{field, target, target_key}`, sorted. A reference into a dataset that is not declared complete simply does not resolve and produces no finding.

#### 7.5.7 Attribution and requiredness

**Attribution** maps an R0 finding to a set of scope obligations, or marks it **unattributable**:

| finding subject | attribution |
|---|---|
| scope row | its `obligation_id` |
| inspection row | its `obligation_id`; the empty set if that cell is null; **unattributable** if the cell is non-null but unreadable |
| inspection entity (`MULTIPLE_CURRENT_REVISIONS`) | the union over its current rows (AM-5) |
| artifact row or entity | the attribution of its inspection entity: the union of the readable, non-null `obligation_id`s of every inspection row with that `inspection_id`. **Unattributable** if the artifact's `inspection_id` cell is unreadable |
| approval row | the attribution of its inspection entity. **Unattributable** if `inspection_id` is unreadable |
| approval item row | the attribution of its approval's inspection entity, through approval rows with that `approval_id` |
| a row whose key is unreadable, or `MALFORMED_ROW` | **unattributable** |
| `DUPLICATE_KEY` group | the union over its rows; **unattributable** if any row's link cell is unreadable |

Consequences:
- **Requiredness.** An R0 finding is **required** when its attribution intersects S, when it is unattributable, or when its subject is a scope row. Otherwise it is **advisory**: it concerns records outside the assessed scope. Such a finding is still reported, but it cannot change the status. An unlinked inspection is R7's business.
- **Coverage downgrade.** An unattributable finding adds `UNATTRIBUTABLE_RECORDS` to its row's dataset: inspections, artifacts, or approvals for both approvals and items. A scope row instead makes the R1 project finding `SCOPE_INVALID`.
- **R2 gate.** A non-PASS R0 finding whose attribution contains O blocks R2(O) (§7.4). An unattributable finding blocks no obligation directly; it acts through coverage.
- **Clean snapshots.** When R0 produces no findings at all, it emits the single finding `R0:snapshot:all`: PASS `INTEGRITY_OK`, required.

### 7.6 R1 · scope and coverage

S is the set of distinct readable `obligation_id`s among scope rows. A quarantined scope row with a readable `obligation_id` still contributes; its R0 finding blocks R2 for that obligation.

One finding for the project, `R1:project:<project_id>`:

| condition (first match) | outcome | code |
|---|---|---|
| no `scope` in the manifest, or its file is absent | UNKNOWN | `SCOPE_MISSING` |
| a scope row is unattributable (§7.5.7) | UNKNOWN | `SCOPE_INVALID` |
| S is empty | UNKNOWN | `SCOPE_EMPTY` |
| `scope.accepted` is null | UNKNOWN | `SCOPE_NOT_ACCEPTED` |
| the manifest's `scope_revision` differs from the policy's, or from any non-quarantined row's | UNKNOWN | `SCOPE_REVISION_MISMATCH` |
| a non-quarantined row's `project_id` differs from the project's | UNKNOWN | `SCOPE_PROJECT_MISMATCH` |
| otherwise | PASS | `SCOPE_ESTABLISHED` |

Four dataset findings, `R1:dataset:<inspections|artifacts|approvals|evidence_files>`: PASS `COVERAGE_COMPLETE` when effective coverage is complete. Otherwise UNKNOWN, with the first reason from §5.8. `observed` is `{declared, basis, consistency, effective, reasons}`; for an omitted dataset `declared` is `null`.

Per-obligation findings (R2–R6) exist for every O in S, even when the project finding is not PASS. In that case they are all `NOT_EVALUATED`.

### 7.7 R2 · inspection cardinality

Cur(O) is the set of non-quarantined inspection rows with `is_current = true` and `obligation_id = O`. "Complete" means the inspections dataset's effective coverage.

| captured Cur(O) | coverage complete | not complete |
|---|---|---|
| two or more rows | FAIL `MULTIPLE_CURRENT` | FAIL `MULTIPLE_CURRENT` |
| one row, `completed` | PASS `SINGLE_CURRENT_COMPLETED` | UNKNOWN `CARDINALITY_UNCONFIRMED` |
| one row, not `completed` | FAIL `NOT_COMPLETED` | FAIL `NOT_COMPLETED` |
| none | FAIL `NO_CURRENT_INSPECTION` | UNKNOWN `ABSENCE_UNCONFIRMED` |

- **Partial coverage and `NOT_COMPLETED`.** Every completion either keeps the captured row as the only current one, which is not completed, or adds a second current row. Both fail.
- **The candidate.** Two rows in Cur(O) are always distinct entities, because two current revisions of one entity would be an R0 finding blocking R2(O). The candidate I is the single row whenever the outcome is PASS or `CARDINALITY_UNCONFIRMED`.
- **Values.** `expected` is `{current_completed_inspections: 1}`. `observed` is `{current_inspections: [<id>@<rev>, …], superseded_revisions: <count of non-current, non-quarantined rows linked to O>}`.
- **Causes.** Both UNKNOWN outcomes carry `caused_by = [R1:dataset:inspections]`.

### 7.8 R3 · identity consistency

Consider the candidate I, and every non-quarantined current artifact row linked to I's entity.

- **Counterexamples.** FAIL when any of these differ from the obligation's values:
  - I's `project_id`, `asset_id` or `activity_kind`;
  - a linked artifact's `project_id` or `asset_id`.
- **Reason order.** The reason is the first applicable code in this order: `INSPECTION_PROJECT_MISMATCH`, `INSPECTION_ASSET_MISMATCH`, `INSPECTION_ACTIVITY_MISMATCH`, `ARTIFACT_PROJECT_MISMATCH`, `ARTIFACT_ASSET_MISMATCH`.
- **Values.** `observed.mismatches` lists every `{subject, field, expected, observed}`, sorted; `expected` holds the obligation's `{project_id, asset_id, activity_kind}`.
- **No counterexample.** PASS `IDENTITY_CONSISTENT` if artifacts coverage is complete. Otherwise UNKNOWN `IDENTITY_UNCONFIRMED`, with `caused_by = [R1:dataset:artifacts]`.

### 7.9 R4 · required artifacts

K is `requirements[I.activity_kind].document_kinds`. If the pack has no entry for the activity: UNKNOWN `REQUIREMENT_UNDEFINED`, and E(O) = ∅.

**E(O)** is the set of non-quarantined current artifact rows linked to I's entity whose `document_kind` is in K. For each kind k in K:
- a witness in E(O) satisfies k;
- without a witness, k FAILs under complete artifacts coverage and is UNKNOWN otherwise.

The per-kind results combine to FAIL if any kind fails, else UNKNOWN if any is unknown, else PASS.

| result | code | values |
|---|---|---|
| PASS | `REQUIRED_KINDS_PRESENT` | `observed.kinds`: kind → sorted artifact ids |
| FAIL | `KIND_MISSING` | `observed.missing_kinds`, sorted |
| UNKNOWN | `KIND_ABSENCE_UNCONFIRMED` | `caused_by = [R1:dataset:artifacts]` |

### 7.10 R5 · artifact availability and the evidence probe

**The probe** resolves a `relative_path` under the evidence root, one segment at a time, from directory listings (`os.scandir`). It behaves identically on every operating system:

1. **Missing path.** A null `relative_path` is `NoPath`, which is treated as absent.
2. **Exact match.** Each segment must equal a directory entry's name exactly (case-sensitive), on every operating system.
3. **Links.** An entry that is a symbolic link, a junction or any reparse point is `Unreadable("link not followed")`. Detect it with `entry.is_symlink()`, and on Windows with `stat.FILE_ATTRIBUTE_REPARSE_POINT` in `entry.stat(follow_symlinks=False).st_file_attributes`.
4. **Intermediate segments.** Each must be a directory (`is_dir(follow_symlinks=False)`), otherwise the path is absent.
5. **Final segment.** It must be a regular file (`is_file(follow_symlinks=False)`), otherwise `Unreadable("not a regular file")`.
6. **Size.** If `st_size > max_file_bytes`: `TooLarge(size)`.
7. **Hashing.** SHA-256 over the bytes, read in 1 MiB chunks:
   - If the read passes `max_file_bytes`, the result is `TooLarge`.
   - `PermissionError` gives `Unreadable("permission denied")`; any other `OSError` gives `Unreadable("read error")`.
8. **Case hint.** When the exact path is absent, the probe retries the resolution case-insensitively (`casefold`). When several entries match, it takes the smallest name. If that reaches a regular file, the actual relative path becomes the hint, for example `O-008/photo.png`. The file stays **absent**.

Probe results are `Present(sha256, size)`, `Absent(case_hint?)`, `NoPath`, `Unreadable(detail)` or `TooLarge(size)`. Each distinct path is probed at most once per run.

**R5** probes every artifact e in E(O):

| probe | evidence coverage complete | not complete |
|---|---|---|
| present and hashed | ok | ok |
| absent, or no path | FAIL `FILE_ABSENT` | UNKNOWN `NOT_CAPTURED`, `caused_by = [R1:dataset:evidence_files]` |
| unreadable, or a link | UNKNOWN `FILE_UNREADABLE` | UNKNOWN `FILE_UNREADABLE` |
| over the size limit | UNKNOWN `FILE_TOO_LARGE` | UNKNOWN `FILE_TOO_LARGE` |

**Combining the artifacts:**
1. FAIL `FILE_ABSENT` if any artifact is established absent.
2. Otherwise UNKNOWN if any artifact is unknown, with the first code in this order: `NOT_CAPTURED`, `FILE_UNREADABLE`, `FILE_TOO_LARGE`.
3. Otherwise, if artifacts coverage is not complete: UNKNOWN `AVAILABILITY_UNCONFIRMED`, with `caused_by = [R1:dataset:artifacts]`, because uncaptured required artifacts could exist.
4. Otherwise PASS `EVIDENCE_AVAILABLE`.

`observed.files` lists, per artifact, `{artifact, status, sha256?}`.

### 7.11 R6 · approval binding

**Preconditions.** R4(O) and R5(O) are both PASS. D is the evidence-set digest of E(O), using the content hashes from R5 (§8.3). R(O) is the set `{(e.artifact_id, e.revision) : e ∈ E(O)}`. Items(a) is the set of non-quarantined item rows with `approval_id = a.approval_id`.

```
1  if approvals effective coverage is not complete:
       UNKNOWN APPROVAL_UNCONFIRMED                    caused_by [R1:dataset:approvals]
2  A    := non-quarantined approvals with inspection_id = I.inspection_id
3  if A is empty:
       FAIL NO_APPROVAL
4  Acur := { a in A : a.inspection_revision = I.revision }
5  if Acur is empty:
       FAIL REVISION_NOT_APPROVED                      observed.approved_revisions = sorted distinct revisions in A
6  classify each a in Acur:
     if a.evidence_digest is not null:
         MATCH if it equals D, else MISMATCH                                    (binding: digest)
     elif Items(a) is non-empty:
         if policy.approval.required_binding = digest:
             UNDETERMINED(BINDING_STRENGTH_INSUFFICIENT)
         else:
             J := { (x, r) in Items(a) : x is the artifact_id of some e in E(O) }
             MATCH if J = R(O), else MISMATCH                                   (binding: revisions)
     else:
         UNDETERMINED(BINDING_UNAVAILABLE)
7  M := the MATCH approvals; U := the UNDETERMINED approvals
8  if M is empty:
       if U is non-empty:
           UNKNOWN, with the code of the first a in U ordered by (decided_at, approval_id)
       else:
           FAIL EVIDENCE_CHANGED_SINCE_APPROVAL
9  t := the latest decided_at in M
10 if some a in U has decided_at > t:
       UNKNOWN, with that approval's code          (a later, undeterminable decision may supersede)
11 L := { a in M : a.decided_at = t }
12 if the decisions in L differ:
       UNKNOWN CONFLICTING_DECISIONS
13 the common decision of L:
       approved -> PASS APPROVAL_COVERS_CURRENT
       rejected -> FAIL APPROVAL_REJECTED
       revoked  -> FAIL APPROVAL_REVOKED
```

- **Revision binding.** In revision mode, items naming artifacts outside E(O) are ignored. An approval that also covered, say, an optional sketch still matches.
- **Ordering.** Approvals that MISMATCH never supersede a MATCH: they concern different evidence.
- **Values.**
  - `expected` is `{approved_evidence_digests, approved_artifact_revisions}`: the distinct digests of the MISMATCH approvals in Acur, and the sorted list of their sorted revision lists, one per revision-mode MISMATCH approval and not deduplicated (§22.1, AM-5).
  - `observed` is `{binding, current_evidence_digest: D, current_artifact_revisions: sorted R(O), decision_approvals}`.
- **Revision-mode PASS wording.** Its explanation states that byte changes under an unchanged revision label are undetectable in this mode.

### 7.12 R7 · unmatched records (advisory)

R7 considers the inspection entities that have at least one non-quarantined current row whose `obligation_id` is null or not in S:
- Each such entity is one FAIL `UNMATCHED_INSPECTION` finding, `R7:inspection:<inspection_id>`, with `required: false`. `observed` is `{current_revisions, obligation_ids}`, sorted, with null rendered as `null`.
- If there are none: `R7:snapshot:all`, PASS `NO_UNMATCHED_RECORDS`.
- When R7 is not evaluated (§7.4), it emits the single finding `R7:snapshot:all`: NOT_EVALUATED `BLOCKED_BY_UPSTREAM`, `required: false` (AM-1). R0 always runs, so it is never NOT_EVALUATED.

R7 speaks only about captured records; it never asserts that the source contains no unmatched records. An unmatched inspection often means the scope is stale.

### 7.13 Project status

Over **required** findings only:

1. any FAIL → **BLOCKED**;
2. otherwise any UNKNOWN or NOT_EVALUATED → **UNKNOWN**;
3. otherwise → **READY_FOR_REVIEW**.

R1 is required and PASSes only for a non-empty, accepted scope, so `READY_FOR_REVIEW` always covers at least one obligation. When FAIL and UNKNOWN coexist, the status is BLOCKED and the report still shows the unknown count. Advisory findings never change the status.

### 7.14 Run errors versus findings

A **run error** exits with code 2 and writes a diagnostic to standard error. It writes no `assessment.json`, `report.html` or `run-manifest.json` (§9.6). These are the run errors:

- invalid configuration: the policy, mapping, manifest, project or normalization file;
- a declared dataset file or the evidence root missing (the scope is the exception, §5.1);
- an unreadable CSV: bad UTF-8, a NUL byte, or a header violation;
- an undeterminable `as_of`, or one before `effective_from`; a project mismatch;
- an `--out` that exists and is not empty, without `--force`;
- any unexpected exception, caught at the top level.

A defective **record** is never a run error.

---

## 8 · Digests and identity

### 8.1 Canonical JSON

Canonical JSON is RFC 8785 (JCS). Every digested object in this project contains only:
- strings with no lone surrogates;
- booleans and `null`;
- integers with |n| ≤ 2⁵³ − 1;
- arrays and objects with ASCII keys.

Python's `json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")` produces JCS-identical bytes for such objects. `canonical()` MUST reject anything else: floats, out-of-range integers, non-ASCII keys, lone surrogates, and any other type. A test asserts this. SHA-256 is written as lowercase hex, and a digest reference is `sha256:<hex>`.

### 8.2 Content digest

SHA-256 over the exact bytes read by the probe.

### 8.3 Evidence-set digest (scheme v1)

```
entries = [ {"artifact_id", "document_kind", "revision", "sha256": <hex content digest>} for e in E ]
          sorted by (document_kind, artifact_id, revision)
D = "sha256:" + sha256( canonical({"artifacts": entries, "scheme": "inspection-reconcile/evidence-set/v1"}) )
```

The scheme string separates the domain and versions the format. Appendix B gives the vectors, re-verified on 2026-10-09.

### 8.4 Snapshot semantic digest

`snapshot_semantic_sha256` is the SHA-256 of the canonical JSON of this object:

```
{
  "scheme": "inspection-reconcile/snapshot-semantic/v1",
  "project":  {"client_id", "name", "project_id", "synthetic"},
  "scope":    null (no scope member) | {"accepted": null | {"at", "by", "reference"}, "rows": null (file absent) | [...], "scope_revision"},
  "datasets": {
    "inspections":    {"declared": null | {"basis": [...], "consistency", "coverage"}, "rows": [...]},
    "artifacts":      {"declared": ..., "rows": [...]},
    "approvals":      {"declared": ..., "rows": [...]},
    "approval_items": {"rows": [...]},
    "evidence_files": {"declared": ...}
  }
}
```

**Rows.**
- **A valid row** is an object of its schema columns with normalized values: TS in canonical UTC form, booleans as JSON booleans, empty cells as `null`.
- **Artifact rows** replace `relative_path` with `"evidence"`: `{"status": "present", "sha256": <hex>}`, or `{"status": "absent" | "no_path" | "unreadable" | "too_large"}`. Case hints are excluded.
- **A quarantined row** is `{"quarantined": [sorted codes], "raw": {column: raw string or null}}`. A malformed row is `{"quarantined": ["MALFORMED_ROW"], "raw_cells": [...]}`.

**Rules.**
- **Sorting.** Every list is sorted by the canonical JSON of its elements, except a malformed row's `raw_cells`, which keeps file order (AM-5). Basis lists are sorted.
- **Exclusions.** `x_` columns, per-record provenance (§5.9), locators, row numbers, paths, `snapshot_id`, `source`, capture times, the normalization file and the evidence root are all excluded.

### 8.5 Identities

| identity | covers | used for |
|---|---|---|
| `policy_sha256` | the canonical JSON of the parsed policy | |
| `evaluation_id` | the canonical JSON of `{scheme: "inspection-reconcile/evaluation/v1", engine_version, policy_sha256, snapshot_semantic_sha256, as_of}` | reproducibility; equal for row-shuffled inputs and for equivalent exports |
| `assessment_semantic_sha256` | the sorted findings, each reduced to `key`, `outcome`, `reason`, `expected`, `observed`, `blocked_by`, `caused_by`, `required` | `compare`; equivalence and order-invariance tests |
| `provenance_id` | `{scheme: "inspection-reconcile/provenance/v1", evaluation_id, inputs: sorted [{role, sha256}], mapping_sha256}` | audit: exactly which input bytes were read |

The order-invariance property (P1) compares `evaluation_id` and `assessment_semantic_sha256`, never output bytes: locators legitimately change when rows move.

---

## 9 · Outputs

### 9.1 `assessment.json`

```json
{
  "schema": "inspection-reconcile/assessment/v1",
  "evaluation_id": "sha256:…",
  "assessment_semantic_sha256": "sha256:…",
  "provenance_id": "sha256:…",
  "status": "BLOCKED",
  "as_of": "2026-10-01T18:00:00.000000Z",
  "engine": {"name": "inspection-reconcile", "version": "0.1.0"},
  "project": {"project_id": "NC-001", "client_id": "CL-SYN-01", "name": "North Creek (synthetic)", "synthetic": true},
  "scope": {"present": true, "scope_revision": "S1", "obligation_count": 40, "accepted": {"by": "…", "at": "…", "reference": "…"}},
  "policy": {"pack_id": "north-creek-demo", "version": "3.0.0", "sha256": "sha256:…", "required_binding": "digest"},
  "snapshot": {"snapshot_id": "NC-001-S02-missing-inspection", "synthetic": true, "semantic_sha256": "sha256:…"},
  "coverage": {"inspections": {"declared": "…", "basis": ["…"], "consistency": "…", "effective": "…", "reasons": []}},
  "counts": {
    "by_outcome": {"PASS": 202, "FAIL": 1, "UNKNOWN": 0, "NOT_EVALUATED": 4},
    "required":   {"PASS": 201, "FAIL": 1, "UNKNOWN": 0, "NOT_EVALUATED": 4},
    "advisory":   {"PASS": 1,   "FAIL": 0, "UNKNOWN": 0, "NOT_EVALUATED": 0},
    "roots":      {"FAIL": 1, "UNKNOWN": 0}
  },
  "findings": []
}
```

- **Roots.** A root is a required finding that is FAIL or UNKNOWN and has an empty `caused_by`.
- **Ordering** follows §9.2.
- **No run-time values.** Wall-clock times and absolute local paths MUST NOT appear.
- **Serialization.** The file is `json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=False)` plus `"\n"`, with member order exactly as specified, written as bytes (§9.6).

### 9.2 Finding

```json
{
  "key": "R2:obligation:O-017",
  "check_id": "R2", "check_type": "inspection_cardinality", "required": true,
  "subject": {"kind": "obligation", "id": "O-017"},
  "outcome": "FAIL", "reason": "NO_CURRENT_INSPECTION",
  "expected": {"current_completed_inspections": 1},
  "observed": {"current_inspections": [], "superseded_revisions": 0},
  "explanation": "Obligation O-017 requires a visual_inspection inspection of asset A-017. No current inspection references it in the inspections dataset, which is complete for scope S1.",
  "resolution": "Record or link the inspection for O-017, or remove O-017 from the scope through an accepted scope revision.",
  "blocked_by": [], "caused_by": [],
  "evidence": [{"system": "fixture", "dataset": "scope", "locator": "scope.csv#row=18"}]
}
```

**Keys** have the form `<check_id>:<subject_kind>:<subject_id>`. R0 keys add `#<CODE>`. IDs never contain `@`, `/` or `#`, so keys are unambiguous.

| subject kind | subject id | used by |
|---|---|---|
| `snapshot` | `all` | R0 PASS, R7 PASS |
| `project` | project_id | R1 |
| `dataset` | dataset name | R1 |
| `obligation` | obligation_id | R2–R6 |
| `scope_row` | obligation_id | R0 |
| `inspection` | `<id>@<rev>` for row defects; `<id>` for the entity defect | R0; R7 (`<id>`) |
| `artifact` | `<id>@<rev>`; `<id>` for the entity defect | R0 |
| `approval` | approval_id | R0 |
| `approval_item` | `<approval_id>/<artifact_id>` | R0 |
| `unattributable_row` | `<dataset>.<first 16 hex of sha256(canonical({"dataset", "cells"}))>` | R0 |

**Ordering.** Findings are sorted by check (R0 … R7), then by subject-kind rank in the order listed above, then by subject id, then by code.

**Content.**
- `expected` and `observed` hold canonical values only: no locators and no paths.
- `evidence` lists locators, sorted. A normalized Quickbase row whose `x_source` column is set uses that value as its locator, for example `table=bsyn00002;rid=117`.
- Explanations and resolutions come from the §10 templates, filled deterministically.

### 9.3 `run-manifest.json`

```
{schema: "inspection-reconcile/run-manifest/v1", evaluation_id, provenance_id, engine, python_version,
 platform, generated_at, as_of, policy: {pack_id, version, sha256},
 inputs: [{role, path, bytes, sha256}] in order of (role, path),
 mapping: null | {mapping_id, version, sha256}}
```

**Inputs** (AM-9).
- **`path`** is relative to the current directory when possible, else absolute. It is `null` for a file that existed only in a temporary directory that the run removed: the normalized snapshot of `assess --export` (§11).
- **Roles:**
  - the snapshot's files: `manifest`, `project`, `scope`, `inspections`, `artifacts`, `approvals`, `approval_items`, `normalization`;
  - `policy`, whose size and digest are those of the bytes that were parsed (a single read);
  - for a run that normalizes an export (`assess --export`, or a `demo` export scenario), three more roles:
    - `export_manifest`: the export's `capture-manifest.json`;
    - `export_table`: each `fields.json` and page file that `normalize` reads, never `pass2.json`;
    - `mapping`: the mapping file's bytes.
- **Captured files** are not listed, because their bytes reach the evaluation through the snapshot's evidence.
- **`provenance_id`** covers every input (§8.5).
- **The demo** writes an export scenario's normalized snapshot to `<out>/<scenario>/snapshot` and records its files there.

This is the only output that holds wall-clock and platform facts. It is excluded from every identity. `inputs[].sha256` is bare lowercase hex, like every content digest in a member named `sha256` (§8.3, §8.4). `policy.sha256` and `mapping.sha256` are digest references, `sha256:<hex>` (§8.1) (AM-9).

### 9.4 `report.html`

- **Self-contained.** One file with inline CSS and no JavaScript. It makes no external requests, and its `<head>` carries `<meta charset="utf-8">` and `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">`.
- **Escaped.** Rendered by Jinja2 with `autoescape=True`; every source string is escaped.
- **Deterministic.** Byte-identical across runs and operating systems. It contains no wall-clock values and no absolute paths; inputs are shown by role and digest.
- **Accessible and printable.** Status colors always come with text labels, the layout prints cleanly, and `<details>` works without scripts.
- **Section order:**
  1. **Banner:** "SYNTHETIC DATA: fictional project" when synthetic.
  2. **Header:** project, status as text, `as_of`, snapshot id, scope revision, pack id and version, and the abbreviated `evaluation_id` (the full value in a `title` attribute).
  3. **What this result means:** the three statuses, and "READY_FOR_REVIEW is not approval".
  4. **Coverage table:** dataset, declared, effective, basis, reasons.
  5. **Summary counts:** root failures, root unknowns, dependent unknowns, not evaluated, advisory.
  6. **Root findings:** FAIL first, then UNKNOWN. Each is a `<details>` showing the explanation, an expected-versus-observed table, the resolution, the evidence locators, and the number of findings it blocks.
  7. **Obligation grid:** one row per obligation, with columns R2–R6. Cells read `PASS`, `FAIL`, `UNK` or `N/E`, with the reason in a `title`.
  8. **All findings**, as a table.
  9. **Provenance:** `evaluation_id`, `provenance_id`, the semantic digests, and input digests by role.

### 9.5 `comparison.json`

```
{schema: "inspection-reconcile/comparison/v1", before: {evaluation_id, status}, after: {evaluation_id, status},
 added: [keys], removed: [keys], changed: [{key, before: {outcome, reason}, after: {outcome, reason}}],
 unchanged_count}
```

Findings are matched by `key`. A finding has **changed** when its outcome, reason, `expected`, `observed`, `blocked_by`, `caused_by` or `required` differs.

### 9.6 Write protocol

- **Bytes only.** Outputs are encoded to UTF-8 bytes in memory, with `\n` line endings and one trailing newline. Each is written to `<name>.tmp.<pid>` in the target directory, flushed, `os.fsync`ed, and moved into place with `os.replace`. Text-mode writes are forbidden.
- **The `--out` directory** MUST be absent or empty unless `--force` is given.
  - With `--force`, only the command's own output names are deleted (AM-9):
    - `assess` and `demo`: `assessment.json`, `report.html`, `run-manifest.json`, `comparison.json`, `index.html`, `evaluation_ids.json` (D-002), and the directories `demo` creates. Those are one per oracle scenario, plus `compare-S02-S06`. A directory is recognized by its name.
    - `normalize`: `manifest.json`, `project.json`, `scope.csv`, `inspections.csv`, `artifacts.csv`, `approvals.csv`, `approval_items.csv`, `normalization.json` and `evidence/`.
    - `capture-quickbase`: `capture-manifest.json`, `tables/` and `files/`.
  - `normalize` and `capture-quickbase` need `--out` to hold nothing but their own names. Anything else refuses the run (`OUT_NOT_EMPTY`) before a file is touched.
  - Their new output is built in a staging directory inside `--out`. It replaces the previous output only when the run succeeds, and only by renames inside `--out` (AM-10):
    1. the previous output is moved into a `.previous-*` directory;
    2. the new output is moved in;
    3. only then is the previous output deleted.

    If a rename fails (for example, another program holds a file open on Windows), the completed renames are undone and the run exits 2 (`WRITE_FAILED`) with `--out` as it was. If the previous output cannot be put back, the message names the `.previous-*` directory that holds it.
  - A `.staging-*` directory left by an interrupted run is the command's own, and the next `--force` run deletes it. A `.previous-*` directory is not the command's own: it may hold the only copy of an earlier output, so it refuses the run (`OUT_NOT_EMPTY`) until it is restored or deleted (AM-10).
  - `assess` and `demo` are not staged: their `--force` deletes their own previous outputs before writing, as above.
  - Symbolic links and junctions are never followed or deleted. Nothing outside `--out` is ever deleted.
- **Failure.** Evaluation completes in memory before any output is written. If a write fails, every file this run wrote is removed, then every directory it created that is empty again. The run exits 2 (AM-9).

---

## 10 · Reason-code catalog

**Placeholders.** They are filled from canonical values:
- `{cov}` is the effective coverage word.
- `{subject}` is the human form of a subject: "Inspection INS-006 revision 1", "Inspection INS-006", "Artifact ART-005-R revision 1", "Approval APR-777", "Approval item APR-001/ART-001-R", "Scope row O-005", or "An unattributable row in inspections".
- Digests are shortened as `sha256:` plus 12 hex characters. The full values are in `observed`.

Every UNKNOWN resolution says what information would resolve it.

| check | outcome | code | explanation template | resolution template |
|---|---|---|---|---|
| R0 | PASS | `INTEGRITY_OK` | No duplicate keys, conflicting current revisions, invalid values or dangling references were found. | — |
| R0 | FAIL | `DUPLICATE_KEY` | {dataset} key {key} appears {count} times ({identical_note}). | Remove or correct the duplicate records in the source. |
| R0 | FAIL | `MULTIPLE_CURRENT_REVISIONS` | {entity} {id} has {count} revisions marked current: {revisions}. | Mark exactly one revision current. |
| R0 | FAIL | `COMPLETED_WITHOUT_TIMESTAMP` | Inspection {id} revision {revision} is current and completed but has no completion time. | Record the completion time. |
| R0 | UNKNOWN | `INVALID_VALUE` | {subject}: {violations}. | Correct the value(s) in the source, or the mapping. |
| R0 | UNKNOWN | `UNMAPPED_VALUE` | {subject}: {violations}; the mapping has no entry for these source values. | Confirm the values' meaning and extend the mapping's value map. |
| R0 | UNKNOWN | `INVALID_PATH` | {subject}: {violations}; the file was not opened. | Correct the inventory path. |
| R0 | UNKNOWN | `MALFORMED_ROW` | A row in {dataset} has {field_count} fields but the header has {header_count} ({count} such row(s)). | Repair the row in the source export. |
| R0 | UNKNOWN | `TIMESTAMP_AFTER_AS_OF` | {subject}: {field} {value} is later than the evaluation time {as_of}. | Check the time zone mapping, the source value, or the capture time. |
| R0 | UNKNOWN | `DANGLING_REFERENCE` | {subject} references {missing}, absent from a dataset declared complete. | Capture the referenced record(s), or correct the reference. |
| R1 | PASS | `SCOPE_ESTABLISHED` | Accepted scope {scope_revision} for project {project_id} lists {count} obligations. | — |
| R1 | UNKNOWN | `SCOPE_MISSING` | No accepted scope was supplied, so readiness cannot be assessed. | Supply the accepted scope for this project. |
| R1 | UNKNOWN | `SCOPE_INVALID` | The scope contains rows whose obligation could not be identified. | Correct the scope rows. |
| R1 | UNKNOWN | `SCOPE_EMPTY` | The scope lists no obligations; an empty scope cannot be ready. | Supply the scope's obligations. |
| R1 | UNKNOWN | `SCOPE_NOT_ACCEPTED` | Scope {scope_revision} has no acceptance record. | Record who accepted the scope, and when. |
| R1 | UNKNOWN | `SCOPE_REVISION_MISMATCH` | The scope revisions disagree: manifest {m}, rows {r}, policy {p}. | Align the scope revision with the policy. |
| R1 | UNKNOWN | `SCOPE_PROJECT_MISMATCH` | Scope rows name project(s) {projects}; expected {project_id}. | Correct the scope rows. |
| R1 | PASS | `COVERAGE_COMPLETE` | Dataset {dataset} is complete for the declared scope (basis: {basis}). | — |
| R1 | UNKNOWN | `DATASET_MISSING` | Dataset {dataset} was not supplied. | Capture {dataset} for the declared scope. |
| R1 | UNKNOWN | `COVERAGE_PARTIAL` | Dataset {dataset} is declared partial (basis: {basis}). | Capture {dataset} completely for the declared scope. |
| R1 | UNKNOWN | `COVERAGE_UNVERIFIED` | The completeness of dataset {dataset} is unverified (basis: {basis}). | Establish completeness with a basis the policy accepts. |
| R1 | UNKNOWN | `COVERAGE_BASIS_NOT_ACCEPTED` | Dataset {dataset} is declared complete on basis {basis}, which the policy does not accept. | Re-capture with an accepted basis, or amend the policy deliberately. |
| R1 | UNKNOWN | `CHANGED_DURING_CAPTURE` | Records in {dataset} changed while it was being captured. | Re-capture when the source is stable. |
| R1 | UNKNOWN | `COVERAGE_CONTRADICTED` | Dataset {dataset} is declared complete, but {n} reference(s) point to records it does not contain. | Capture the missing records, or correct the references. |
| R1 | UNKNOWN | `UNATTRIBUTABLE_RECORDS` | Dataset {dataset} contains {n} row(s) that cannot be attributed to an obligation. | Correct those rows. |
| R2 | PASS | `SINGLE_CURRENT_COMPLETED` | Obligation {obligation_id} has exactly one current completed inspection: {inspection_id} revision {revision}. | — |
| R2 | FAIL | `NO_CURRENT_INSPECTION` | Obligation {obligation_id} requires a {activity_kind} inspection of asset {asset_id}. No current inspection references it in the inspections dataset, which is complete for scope {scope_revision}. | Record or link the inspection for {obligation_id}, or remove {obligation_id} from the scope through an accepted scope revision. |
| R2 | FAIL | `MULTIPLE_CURRENT` | Obligation {obligation_id} has {count} current inspections ({inspections}); exactly one is required. | Mark superseded inspections not current, or correct their obligation links. |
| R2 | FAIL | `NOT_COMPLETED` | The current inspection {inspection_id} for obligation {obligation_id} has status {completion_status}. | Complete the inspection, or correct its status. |
| R2 | UNKNOWN | `ABSENCE_UNCONFIRMED` | No current inspection for obligation {obligation_id} was captured, but the inspections dataset is {cov}; the record may exist uncaptured. | Capture the complete inspections dataset, or confirm the absence in the source. |
| R2 | UNKNOWN | `CARDINALITY_UNCONFIRMED` | One current completed inspection ({inspection_id}) was captured for obligation {obligation_id}, but the inspections dataset is {cov}; another current inspection could exist. | Capture the complete inspections dataset. |
| R3 | PASS | `IDENTITY_CONSISTENT` | Inspection {inspection_id} and its {n} current artifact(s) match obligation {obligation_id}: project {project_id}, asset {asset_id}, activity {activity_kind}. | — |
| R3 | FAIL | `INSPECTION_*_MISMATCH` | Inspection {inspection_id} names {field} {observed}, but obligation {obligation_id} requires {expected}. | Correct the inspection's {field}, or link it to the correct obligation. |
| R3 | FAIL | `ARTIFACT_*_MISMATCH` | Artifact {artifact_id} ({document_kind}) attached to inspection {inspection_id} names {field} {observed}, but obligation {obligation_id} requires {expected}. | Attach the correct document, or correct the artifact's {field}. |
| R3 | UNKNOWN | `IDENTITY_UNCONFIRMED` | No identity conflict was found among the captured records for obligation {obligation_id}, but the artifacts dataset is {cov}. | Capture the complete artifacts dataset. |
| R4 | PASS | `REQUIRED_KINDS_PRESENT` | Inspection {inspection_id} has current artifacts of every required kind: {kinds}. | — |
| R4 | FAIL | `KIND_MISSING` | Inspection {inspection_id} has no current artifact of the required kind(s) {missing_kinds}; the artifacts dataset is complete. | Attach the missing document(s), or correct their kind or current flag. |
| R4 | UNKNOWN | `KIND_ABSENCE_UNCONFIRMED` | No current {missing_kinds} artifact was captured for inspection {inspection_id}, but the artifacts dataset is {cov}. | Capture the complete artifacts dataset. |
| R4 | UNKNOWN | `REQUIREMENT_UNDEFINED` | The requirement pack defines no document requirements for activity {activity_kind}. | Add requirements for {activity_kind} to the pack. |
| R5 | PASS | `EVIDENCE_AVAILABLE` | All {n} required artifact files for inspection {inspection_id} were read and hashed. | — |
| R5 | FAIL | `FILE_ABSENT` | The file for artifact {artifact_id} ("{path}") is not in the evidence set, which is complete for this snapshot.{case_hint} | Restore the file, or correct the inventory path. |
| R5 | UNKNOWN | `NOT_CAPTURED` | The file for artifact {artifact_id} was not captured; the evidence set is {cov}. | Capture the file, or confirm its absence in the source. |
| R5 | UNKNOWN | `FILE_UNREADABLE` | The file for artifact {artifact_id} exists but could not be read ({detail}). | Fix the file's permissions or format, then re-run. |
| R5 | UNKNOWN | `FILE_TOO_LARGE` | The file for artifact {artifact_id} exceeds the {limit}-byte limit. | Raise the limit deliberately, or review the file separately. |
| R5 | UNKNOWN | `AVAILABILITY_UNCONFIRMED` | Every captured required file was available, but the artifacts dataset is {cov}; other required artifacts could exist. | Capture the complete artifacts dataset. |
| R6 | PASS | `APPROVAL_COVERS_CURRENT` | Approval {approval_id} ({decided_at}) covers inspection {inspection_id} revision {revision} and its current required evidence {digest_short} (binding: {binding}).{binding_note} | — |
| R6 | FAIL | `NO_APPROVAL` | No approval decision exists for inspection {inspection_id}; the approvals dataset is complete. | Obtain a review decision. |
| R6 | FAIL | `REVISION_NOT_APPROVED` | Approvals exist for inspection {inspection_id} revision(s) {approved_revisions}, but not for the current revision {revision}. | Obtain approval for revision {revision}. |
| R6 | FAIL | `EVIDENCE_CHANGED_SINCE_APPROVAL` | No decision on inspection {inspection_id} revision {revision} covers its current required evidence {digest_short}; decision(s) {approval_ids} bind different evidence. The evidence changed after approval. | Re-review the current evidence, or restore the approved version. |
| R6 | FAIL | `APPROVAL_REJECTED` · `APPROVAL_REVOKED` | The latest decision on the current evidence for inspection {inspection_id} is {decision} ({approval_id}, {decided_at}). | Resolve the reviewer's decision. |
| R6 | UNKNOWN | `APPROVAL_UNCONFIRMED` | The approvals dataset is {cov}; a later decision could exist. | Capture the complete approvals dataset. |
| R6 | UNKNOWN | `BINDING_UNAVAILABLE` | Approval {approval_id} does not record which evidence it covers. | Record the evidence digest (see `evidence-digest`) or the approved artifact revisions. |
| R6 | UNKNOWN | `BINDING_STRENGTH_INSUFFICIENT` | Approval {approval_id} binds artifact revisions only; the pack requires a content-digest binding. | Record the evidence digest (see `evidence-digest`), or use a pack that accepts revision binding. |
| R6 | UNKNOWN | `CONFLICTING_DECISIONS` | Decisions {approval_ids} on the current evidence share the timestamp {decided_at} but disagree. | Correct the decision records. |
| R7 | PASS | `NO_UNMATCHED_RECORDS` | Every captured current inspection references an obligation in the accepted scope. | — |
| R7 | FAIL | `UNMATCHED_INSPECTION` | Current inspection {inspection_id} references {obligation_ids}, which is not in accepted scope {scope_revision}. (Advisory.) | Link the inspection to an in-scope obligation, or review whether the scope is stale. |
| any | NOT_EVALUATED | `BLOCKED_BY_UPSTREAM` | Not evaluated because {blocked_by} did not pass. | Resolve {blocked_by}. |

**Filled-in fragments.**
- `{identical_note}` is "the rows are identical" or "the rows differ".
- `{case_hint}` is empty, or " A file differing only in letter case exists: {hint}."
- `{binding_note}` is empty for digest binding, or " Byte changes under an unchanged revision label are undetectable in revision binding." for revision binding.
- `{violations}` joins `field <f> value "<v>" violates <rule>` with "; ".

---

## 11 · Command-line contract

```
inspection-reconcile --version
inspection-reconcile validate         --snapshot DIR --policy FILE
inspection-reconcile assess           (--snapshot DIR | --export DIR --mapping FILE) --policy FILE --out DIR [--as-of TS] [--force]
inspection-reconcile normalize        --export DIR --mapping FILE --out DIR [--force]
inspection-reconcile compare          --before PATH --after PATH [--out FILE] [--force]
inspection-reconcile demo             (--scenario ID | --all) --out DIR [--fixtures DIR] [--force]
inspection-reconcile evidence-digest  --snapshot DIR --policy FILE --inspection ID [--revision REV]
inspection-reconcile export-sqlite    --snapshot DIR --policy FILE --out FILE [--force]
inspection-reconcile capture-quickbase --config FILE --mapping FILE --out DIR [--force]
```

- **Standard output.**
  - `assess` prints one summary line, for example `BLOCKED  roots: FAIL=1 UNKNOWN=0  not_evaluated=4  evaluation_id=sha256:3f9c…`.
  - `validate`, `demo` and `evidence-digest` print what their bullets below describe (AM-9).
  - On Windows, `main()` reconfigures `stdout` and `stderr` to UTF-8.
- **Standard error** carries diagnostics only.
- **`--log-json`** may be given before or after the command. With it, every diagnostic line on standard error is one JSON object `{level, code, message}`. A log record from a library module has `code: "LOG"` and adds `logger` (AM-9).
- **`assess --snapshot` with `--mapping`** is a usage error, because `--mapping` belongs to `--export` (AM-9).
- **`compare --out`.** An existing FILE is replaced only with `--force`. Without it the run is `OUT_EXISTS`, and a directory is `OUT_INVALID`. An `--out` that names an input of the comparison is refused even with `--force` (`COMPARE_OUT_IS_INPUT`). The inputs are `--before`, `--after`, or the `assessment.json` inside a directory argument, compared after resolving the paths (AM-9).
- **`validate`** loads and checks every configuration file and header, and lists the record-level defects that `assess` would report under R0. It exits 0 when the inputs can be assessed, and 2 otherwise.
- **`assess --export`** normalizes into a temporary directory, assesses it, and removes the temporary directory.
- **`compare`** takes, for each `PATH`, an `assessment.json` or a directory that contains one.
- **`demo`**
  - It runs the scenarios in `--fixtures` (default `fixtures/scenarios`) against `fixtures/oracle.yaml`.
  - For an export scenario it first runs `normalize` into `<out>/<scenario>/snapshot`.
  - It checks each result against the oracle (§15.1).
  - With `--all` it also writes `index.html`, with every scenario's oracle status beside its actual status, links to each report, and the S02 → S06 comparison.
- **`evidence-digest`** prints the evidence-set digest D (§8.3) for the inspection's current revision, or for `--revision`, together with its entries. It is the value a review process should store as `evidence_digest`.

| exit | meaning |
|---|---|
| 0 | `assess`: READY_FOR_REVIEW · `compare`: no semantic difference · `demo`: every scenario matched its oracle · any other command: success |
| 2 | run error: usage, configuration, unreadable input, or an internal error caught at the top level |
| 10 | `assess`: BLOCKED |
| 11 | `assess`: UNKNOWN |
| 20 | `compare`: semantic differences found |
| 30 | `demo`: at least one scenario did not match its oracle |

`demo --scenario ID` exits 30 on an oracle mismatch, and otherwise with that scenario's status code (0, 10 or 11). `assess` writes its outputs before exiting 10 or 11. `main()` catches every exception, logs it without secrets, and exits 2. Exit 1 is never used deliberately.

---
## 12 · Quickbase integration

### 12.1 Verified facts

The portal `developer.quickbase.com` renders only with JavaScript. On 2026-10-09 the facts below were checked against the official OpenAPI document the portal loads, **[QB-OAS]**, `https://developer.quickbase.com/qb-openapi-v3.json`: OpenAPI 3.0.1, "Quickbase RESTful JSON API", 67 operations. They were also checked against portal pages rendered in headless Chrome: **[QB-FT]** field types, **[QB-PG]** pagination, **[QB-ER]** errors and **[QB-AU]** authorization. Step C3 re-checks the facts the live run touches and records the date.

| fact | status | source |
|---|---|---|
| Base URL `https://api.quickbase.com/v1` | verified, primary | QB-OAS `servers` |
| Required headers `QB-Realm-Hostname` and `Authorization`; `User-Agent` optional | verified, primary | QB-OAS runQuery parameters |
| `Authorization: QB-USER-TOKEN <token>` for permanent tokens. `QB-TEMP-TOKEN` tokens need a browser session (code pages) and expire after 5 minutes | verified, primary | QB-AU; QB-OAS getTempTokenDBID |
| `POST /records/query` with body `{from (required), select, where, sortBy, groupBy, options: {skip, top, compareWithAppLocalTime}}` | verified, primary | QB-OAS runQuery |
| `where` is a query-language string **or** an array of up to 50,000 record IDs; omitting it returns all records | verified, primary | QB-OAS runQuery |
| An empty or missing `select` returns only the table's **default** columns | verified, primary | QB-OAS runQuery |
| An unset (or `false`) `sortBy` returns records **unsorted** | verified, primary | QB-OAS runQuery |
| The response is `{data: [{"<fid>": {"value": …}}], fields: [{id, label, type}], metadata: {totalRecords, numRecords, numFields, skip, top}}`, with `numFields`, `totalRecords` and `numRecords` required | verified, primary | QB-OAS runQueryResponse; QB-PG |
| Intelligent pagination: `numRecords` may be smaller than `top`; compare it with `totalRecords` to decide whether more requests are needed | verified, primary | QB-PG |
| The query response's `fields[].type` holds display labels (the example uses `"date time"`); it is not the `fieldType` vocabulary | verified, primary | QB-OAS example |
| `GET /fields?tableId=…` returns each field's `id`, `label`, `fieldType` and `mode` (`lookup`, `summary`, `formula`, or blank), plus `properties` (including `versionMode`: `keepallversions` or `keeplastversions`) | verified, primary | QB-OAS getFields, fieldResponse |
| `GET /files/{tableId}/{recordId}/{fieldId}/{versionNumber}` returns base64 content, with the file name in `Content-Disposition` | verified, primary | QB-OAS downloadFile |
| `DELETE` on the same path deletes a version, and `versionNumber` 0 means the most recent | verified, primary | QB-OAS deleteFile |
| A file attachment value is `{url: "/files/<table>/<rid>/<fid>", reservedBy?, versions: [{versionNumber, fileName, uploaded, creator}]}` | verified, primary | QB-FT |
| Checkbox values are booleans, `false` when empty; numeric values are JSON numbers; a Record ID is an integer | verified, primary | QB-FT |
| DateTime values are returned as `YYYY-MM-DDThh:mm:ssZ` (UTC) | verified, primary | QB-FT |
| A user value is `{email, id, name, userName?}` | verified, primary | QB-FT |
| A multiple-choice text value is a **string** in the example, though the prose says "array of strings" | verified documentation inconsistency, so the adapter accepts both (§12.3) | QB-FT |
| The table key field defaults to 3, "usually the Quickbase Record ID" | verified, primary | QB-OAS getTable, upsert |
| Errors are 4xx/5xx with `{message, description}`; a 2xx may still carry partial errors (`lineErrors`, on writes) | verified, primary | QB-ER |
| Limit of 100 requests per 10 seconds per user token; throttled responses are 429 with `retry-after`; retry only 429 with backoff, back off on 5xx and connectivity errors, never retry other 4xx | verified, primary in the v2.0 pass; absent from the OpenAPI document | S6 |
| `recordsModifiedSince` and `getRoles` require app-admin permission | verified, primary; **not used**, for least privilege | QB-OAS |
| Built-in field 2 is Date Modified (`timestamp`); fields 1, 4 and 5 are Date Created, Record Owner and Last Modified By | **unverified**; C2 checks field 2's `fieldType` | — |
| `getFields` `fieldType` names `text-multiple-choice`, `user`, `file`, `recordid` | **unverified** (`text`, `numeric`, `timestamp` and `checkbox` appear in the formula-type enum); C2 compares every mapped field against the live list, so a wrong name fails loudly with the observed value | — |
| The `{3.GT.<n>}` comparison on Record ID# in a `where` string | **unverified**; C3 confirms it. If it is refused, capture falls back to `skip` paging with per-page total accounting | — |

### 12.2 Export format

The Phase B fixtures and the Phase C capture share this format:

```
<export>/
  capture-manifest.json
  tables/<role>/fields.json            GET /fields response (parsed JSON, re-serialized; AM-4)
  tables/<role>/page-0001.json …       POST /records/query responses (parsed JSON, re-serialized), in order (pass 1)
  tables/<role>/pass2.json             second-pass {rid: date-modified} map (live capture only)
  files/<table_id>/<rid>/<fid>/v<version>/<file name>
```

Roles are `obligations` (required), `inspections`, `artifacts`, `approvals`, and `approval_items` (optional).

```json
{
  "format": "inspection-reconcile/qb-export/v1",
  "export_id": "NC-001-S16-quickbase-clean",
  "synthetic": true,
  "source": {"system": "quickbase", "realm_hostname": "synthetic.quickbase.invalid", "app_id": "bsyn00000", "description": "…"},
  "capture": {"started_at": "…", "ended_at": "…"},
  "scope": {"scope_revision": "S1", "accepted": {"by": "…", "at": "…", "reference": "…"}},
  "tables": {"obligations": {"table_id": "bsyn00001", "select": [2, 3, 6, 7, 8, 9, 10], "where": "{7.EX.'NC-001'}",
                              "pages": 1, "total_records": 40, "retrieved": 40, "two_pass": "not_run"}},
  "datasets": {"inspections": {"coverage": "…", "basis": ["…"], "consistency": "…"}, "artifacts": {}, "approvals": {}, "evidence_files": {}},
  "files": [{"table_id": "bsyn00003", "record_id": 201, "field_id": 13, "version": 1, "file_name": "report.pdf",
             "path": "files/bsyn00003/201/13/v1/report.pdf", "bytes": 0, "sha256": "…", "status": "captured"}]
}
```

`files[].status` is `captured`, `not_captured`, `out_of_scope`, `too_large` or `error`. Unknown members are a run error.

### 12.3 Mapping and `normalize`

```yaml
schema: inspection-reconcile/mapping/v1
mapping_id: quickbase-demo
version: "3.0.0"
source: quickbase
synthetic: true
project: {project_id: NC-001, client_id: CL-SYN-01, name: "North Creek (synthetic)", synthetic: true}
scope_filter: {fid: 7, value: NC-001}           # obligations table only: {7.EX.'NC-001'}
tables:
  obligations:
    table_id: bsyn00001
    fields:
      obligation_id:  {fid: 6,  type: text}
      project_id:     {fid: 7,  type: text}
      scope_revision: {fid: 8,  type: text}
      asset_id:       {fid: 9,  type: text}
      activity_kind:  {fid: 10, type: text-multiple-choice, values: {"Visual Inspection": visual_inspection}}
  inspections:
    table_id: bsyn00002
    fields:
      inspection_id:     {fid: 6,  type: text}
      revision:          {fid: 7,  type: numeric, as: integer_string}
      is_current:        {fid: 8,  type: checkbox}
      obligation_id:     {fid: 9,  type: reference, target: obligations}
      project_id:        {fid: 10, type: text}
      asset_id:          {fid: 11, type: text}
      activity_kind:     {fid: 12, type: text-multiple-choice, values: {"Visual Inspection": visual_inspection}}
      completion_status: {fid: 13, type: text-multiple-choice,
                          values: {"Complete": completed, "In Progress": in_progress, "Not Started": not_started, "Cancelled": cancelled}}
      completed_at:      {fid: 14, type: timestamp}
  artifacts:
    table_id: bsyn00003
    fields:
      artifact_id:   {fid: 6,  type: text}
      revision:      {fid: 7,  type: numeric, as: integer_string}
      is_current:    {fid: 8,  type: checkbox}
      inspection_id: {fid: 9,  type: reference, target: inspections}
      project_id:    {fid: 10, type: text}
      asset_id:      {fid: 11, type: text}
      document_kind: {fid: 12, type: text-multiple-choice, values: {"Inspection Report": inspection_report, "Photo": photo}}
      relative_path: {fid: 13, type: file}
  approvals:
    table_id: bsyn00004
    fields:
      approval_id:         {fid: 6,  type: text}
      inspection_id:       {fid: 7,  type: reference, target: inspections}
      inspection_revision: {fid: 8,  type: numeric, as: integer_string}
      evidence_digest:     {fid: 9,  type: text}
      decision:            {fid: 10, type: text-multiple-choice, values: {"Approved": approved, "Rejected": rejected, "Revoked": revoked}}
      decided_at:          {fid: 11, type: timestamp}
      decided_by:          {fid: 12, type: user, as: email}
```

**Mapping rules.** A violation is a run error unless stated otherwise.

- **Coverage of columns.** Every required canonical column (§5.5) is mapped exactly once. A nullable column MAY be omitted, which makes it `null`. FIDs are positive integers, and no FID is mapped twice within a table. Fields 2 and 3 are always selected. Mapping field 2 or 3 to a canonical column is allowed only for `type: timestamp` (field 2) or `recordid` (field 3) (AM-3).
- **Types.** Each mapping type names the expected `getFields` `fieldType`:

  | type | expected fieldType |
  |---|---|
  | `text` | `text` |
  | `text-multiple-choice` | `text-multiple-choice` |
  | `numeric` | `numeric` |
  | `checkbox` | `checkbox` |
  | `timestamp` | `timestamp` |
  | `user` | `user` |
  | `file` | `file` |
  | `reference` | `numeric` |
  | `recordid` | `recordid` (AM-3) |

  `normalize` checks the export's `fields.json` against this table, and C2 checks the live field list. A mismatch is a run error that names both values. Every mapped field MUST also have a blank `mode` (a data-entry field), except where the mapping explicitly declares `allow_derived: true`.
- **Value maps.** `values` maps source labels to canonical enum values, exactly and case-sensitively. Keys MUST be strings (§6.2). A label missing from the map is never guessed. The canonical cell receives the raw label, and `normalization.json` lists it under `unmapped_values`, keyed by the row's `x_source` and the field. R0 then reports `UNMAPPED_VALUE`.
- **Conversions**, from the verified formats of §12.1. Any other JSON type produces a canonical cell holding the canonical JSON text of the value, and `normalization.json` lists that cell under `unmapped_values`, so R0 reports `UNMAPPED_VALUE` whether or not the text satisfies the column grammar (AM-7).

  | type | source value | canonical value |
  |---|---|---|
  | `text` | string | the string; `""` becomes null |
  | `text-multiple-choice` | a string, or a one-element list of strings | the mapped label; `""` or `[]` becomes null |
  | `numeric` with `as: integer_string` | an integer, or a float with an integral value | its decimal string |
  | `checkbox` | boolean | `true` or `false` |
  | `timestamp` | string | the string, validated by R0; null or `""` becomes null |
  | `user` with `as: email` | user object | its `email` |
  | `file` | file attachment value | see "Files" below |
  | `recordid` | integer | its decimal string (AM-3) |
  | `reference` | numeric Record ID# | resolved against the **captured** rows of the target table to its canonical id (`obligation_id`, `inspection_id` or `approval_id`) |

  An unresolved reference becomes the placeholder `qbrid.<target_table_id>.<rid>`, which is a valid ID, and is listed under `unresolved_references`. R0 then reports `DANGLING_REFERENCE` if the target dataset is declared complete, and R7 reports an unresolved obligation link. A null reference becomes null.
- **Files.**
  - **Version.** The highest `versionNumber` is the artifact's evidence. An empty `versions` list, or a null value, means `relative_path = null`.
  - **Name sanitization.** Each character outside `[A-Za-z0-9._-]` becomes `_`. Trailing `.` characters are removed. An empty result becomes `file`, and a reserved device name gets a `_` prefix. A name longer than 100 characters becomes its first 83 characters, `~`, the first 8 hex digits of its SHA-256, and its extension, truncated to 100 characters. The original name is kept in `normalization.json`.
  - **Path.** `relative_path` is `files/<table_id>/<rid>/<fid>/v<n>/<sanitized>`, and `normalize` copies the bytes there under the evidence root. A file whose export status is not `captured` is not copied, and the coverage claim comes from the capture manifest.
- **Provenance.** Every canonical row gets `x_source = "table=<table_id>;rid=<rid>"`.
- **Output.** `normalize` writes a canonical snapshot (§5) with these parts:
  - `scope.csv` from the obligations rows;
  - `scope_revision` and acceptance from the capture manifest;
  - `project.json` from the mapping;
  - the coverage declarations copied unchanged from the capture manifest;
  - `manifest.normalization = "normalization.json"`, a file holding `{format: "inspection-reconcile/normalization/v1", mapping: {mapping_id, version, sha256}, unmapped_values: [...], unresolved_references: [...], files: [{relative_path, original_name}]}`.

  Row order in each CSV is ascending by the canonical key, then by Record ID#.

### 12.4 The HTTP client (`qb_client.py`)

- **Allowlist.** These are the only operations the client can issue; anything else is a programming error raised before any I/O. **`DELETE` cannot be issued.**
  - `GET /fields?tableId=`
  - `GET /tables/{tableId}?appId=`
  - `POST /records/query`, which is classified as a read by its documented semantics, not by its HTTP verb
  - `GET /files/{tableId}/{recordId}/{fieldId}/{versionNumber}`, with `versionNumber` ≥ 1
- **Headers** on every request: `QB-Realm-Hostname`, `Authorization: QB-USER-TOKEN <token>`, `User-Agent: inspection-reconcile/<version>`, and `Content-Type: application/json`.
- **Timeouts and order.** 10 seconds to connect and 60 to read. Requests are sequential.
- **Rate limit.** A client-side sliding window allows at most `requests_per_10s` (default 90) requests in any 10 seconds, leaving headroom under the documented 100.
- **Retries.**
  - **429:** wait `retry-after` seconds, capped at `max_retry_wait_s`.
  - **5xx, connection errors and timeouts:** exponential backoff of 1, 2, 4, 8… seconds, capped at 30.
  - **Any other 4xx:** never retried. A 401 or 403 is reported as a permission error naming the operation, never the token.
  - **Exhaustion:** after `max_attempts` the run fails (exit 2).
  - **Tests** inject the clock, the sleep function and the transport (`httpx.MockTransport`).
- **The token** is read only from the environment variable named in the configuration. It is never accepted as an argument, written to disk, put in a URL, or logged. A logging filter and exception sanitizing replace it, and any `Authorization` value, with `***`.

### 12.5 Live capture (`capture-quickbase`)

**Configuration** (`qb-capture.yml`):

```yaml
schema: inspection-reconcile/qb-capture/v1
realm_hostname: example.quickbase.com
app_id: bxxxxxxxx
token_env: QB_USER_TOKEN
page_size: 1000
capture_files: true
scope: {scope_revision: S1, accepted: {by: "Name", at: "2026-10-01T00:00:00Z", reference: "REF"}}
limits: {max_file_bytes: 104857600, requests_per_10s: 90, max_attempts: 5, max_retry_wait_s: 60, max_pages: 10000}
operator_attestation: {full_read_access: true, attested_by: "Name", note: "Authorized test app; the token's role can read every record of the mapped tables"}
```

**Algorithm.**

1. **Schema verification (C2).** For every mapped table, `GET /fields`. Check that every mapped FID exists with the expected `fieldType` and mode, that field 3 exists, and that field 2 exists with `fieldType` `timestamp`. Save `fields.json`. A mismatch is a run error.
2. **Pass 1: full-table reads with keyset pagination.**
   - The obligations table uses the scope filter `{<fid>.EX.'<value>'}`. The dependent tables are read **in full**. Filtering a child table by its own copy of the project field would hide exactly the wrong-project records that R3 must see.
   - Each table runs this loop:

     ```
     select = sorted(mapped fids ∪ {2, 3});  sortBy = [{fieldId: 3, order: ASC}];  last = 0;  retrieved = 0
     repeat (at most max_pages times; exceeding it is a run error):
         where = (base ? base + "AND" : "") + "{3.GT." + last + "}"
         page  = POST /records/query {from, select, where, sortBy, options: {skip: 0, top: page_size}}
         save the page verbatim
         every selected fid must appear in page.fields, else run error FIELD_NOT_RETURNED (a permission or schema problem)
         first page:     total0 := page.metadata.totalRecords
         every page:     page.metadata.totalRecords must equal total0 − retrieved   (otherwise: changed)
         page.metadata.numRecords == 0  →  stop
         record ids must be strictly increasing and > last                          (otherwise: changed)
         retrieved += numRecords;  last := the largest record id on the page
     complete read  ⇔  retrieved = total0  and the last page was empty  and no accounting failure occurred
     ```
3. **Files.** In-scope current artifacts are the current artifact rows whose referenced inspection links to a captured obligation. For each one, download the highest file version, decode the base64, enforce `max_file_bytes`, write it under `files/`, and hash the decoded bytes. Other artifacts are recorded as `out_of_scope` and not downloaded. A URL alone is never evidence.
4. **Pass 2: an interleaved consistency check.** Only after pass 1 of **every** table has finished, re-run each table's query selecting fields 2 and 3 only. Compare the `{rid: date modified}` maps.

   Any change to any table between its pass-1 read and its pass-2 read is detected, including deletions and edits made while other tables were being read. This makes the per-table check also guard cross-table consistency.
5. **The obligations table** must be complete and stable, otherwise the run fails (exit 2): expected work must be reliable.
6. **Coverage declaration** for each of inspections, artifacts and approvals:
   - **Complete.** `complete_for_declared_scope`, with basis `[query_total_matched, two_pass_stable, operator_attestation]`, only when the read was complete, the table was stable, and `operator_attestation.full_read_access` is true.
   - **Otherwise.** `partial` with `[pagination_incomplete]` for an incomplete read, or `unverified` with the bases actually earned.
   - **Consistency** is `stable_verified` or `changed_during_capture`.
   - **`evidence_files`** is complete under the same conditions applied to the artifacts table, plus every in-scope current artifact file being captured. Deliberately skipped files make it `partial` with `[attachment_capture_skipped]`.
7. **Output.** Write `capture-manifest.json` and the files atomically.

### 12.6 The demonstration application *(informative)*

Appendix D specifies a four-table test app with the exact field order that yields the §12.3 FIDs, the relationships, a read-only role, and how to load the S01 data. Real applications usually differ: records updated in place, file versions instead of revision rows, and approvals without digests. §20 lists the questions to settle before mapping a real application.

### 12.7 Deferred: scoped capture

Very large tables may need child-table filters, through a lookup of the parent's project field combined with an OR on the child's own project field. Such a capture must then fetch the reference closure: every referenced parent Record ID that was not captured, fetched by record-ID array. Otherwise legitimate out-of-scope references look like `DANGLING_REFERENCE`. This mode is specified but not implemented in v3.0.

---

## 13 · Architecture and repository

### 13.1 Location and layout

The repository root is `C:/quickbase/inspection-reconcile/`, which is its own Git repository. Private planning material stays outside the repository, or in the gitignored `local/` directory.

```
inspection-reconcile/
├── CLAUDE.md                       Appendix F, verbatim
├── README.md                       §18.3
├── LICENSE                         MIT
├── pyproject.toml · uv.lock · .gitattributes · .gitignore · .python-version
├── .github/workflows/ci.yml        3 operating systems × Python 3.12 and 3.13
├── docs/
│   ├── SPEC.md                     the public part of this specification
│   ├── status.md                   step checklist, updated every session
│   ├── decisions.md                decisions and amendments; appended, never rewritten
│   └── limitations.md              verified, synthetic and unsupported parts
├── src/inspection_reconcile/
│   ├── __init__.py                 __version__ = "0.1.0"
│   ├── __main__.py                 python -m inspection_reconcile
│   ├── cli.py                      argparse, exit codes, top-level guard, UTF-8 console
│   ├── errors.py                   RunError(code, message)
│   ├── vocab.py                    outcomes, statuses, reason codes, enums, subject kinds
│   ├── grammar.py                  ID/REV/KIND/BOOL/TS/DIGEST/TEXT/PATH validators (§5.6)
│   ├── yamlsafe.py                 the restricted loader (§6.2)
│   ├── validate.py                 closed-world object validators for the JSON and YAML configs
│   ├── canonical.py                canonical JSON, digests, identities (§8)
│   ├── policy.py                   policy loading (§6)
│   ├── io/snapshot.py              manifest, project and CSV loading → LoadedSnapshot (raw rows plus provenance)
│   ├── io/evidence.py              EvidenceStore + FileSystemEvidence (the §7.10 probe)
│   ├── io/writer.py                atomic byte writes, --out protocol (§9.6)
│   ├── engine/integrity.py         R0 (§7.5)
│   ├── engine/coverage.py          effective coverage and R1 (§5.8, §7.6)
│   ├── engine/checks.py            R2–R7, one pure function each
│   ├── engine/registry.py          CHECKS: id → function (the fault-injection seam)
│   ├── engine/assess.py            orchestration, dependencies, status → Assessment
│   ├── report/templates.py         §10 templates
│   ├── report/json_out.py          assessment.json, run-manifest.json
│   ├── report/html.py + report.html.j2
│   ├── report/compare.py
│   ├── sqlexport.py                SQLite export (Appendix G)
│   ├── adapters/mapping.py         §12.3 model and validation
│   ├── adapters/qb_export.py       export → canonical snapshot (normalize)
│   ├── adapters/qb_client.py       §12.4
│   ├── adapters/qb_capture.py      §12.5
│   ├── demo.py
│   └── oracle.py                   oracle loading and comparison (used by demo and tests)
├── sql/                            Appendix G queries
├── tools/make_fixtures.py          deterministic generator with an INDEPENDENT digest implementation
├── policies/north-creek-demo.yml · policies/north-creek-revisions.yml
├── mappings/quickbase-demo.yml
├── fixtures/oracle.yaml            Appendix A, verbatim
├── fixtures/scenarios/<id>/{snapshot|export}/…
└── tests/                          unit/ vectors/ scenario/ property/ fault/ cli/ html/ sql/ adapter/ connector/ meta/
```

### 13.2 Key interfaces

```python
class EvidenceStore(Protocol):
    def probe(self, relative_path: str | None) -> FileProbe: ...
# FileProbe = Present(sha256, size) | Absent(case_hint) | NoPath() | Unreadable(detail) | TooLarge(size)

def load_snapshot(path: Path) -> LoadedSnapshot: ...                          # raises RunError
def load_policy(path: Path) -> Policy: ...                                    # raises RunError
def assess(snapshot: LoadedSnapshot, policy: Policy, evidence: EvidenceStore,
           as_of: datetime, engine_version: str) -> Assessment: ...          # pure apart from evidence.probe
def evidence_set_digest(entries: Sequence[EvidenceEntry]) -> str: ...         # §8.3
def render_html(assessment: Assessment, provenance: Provenance) -> bytes: ...
def compare(before: dict, after: dict) -> Comparison: ...
```

Checks are pure functions, `(ctx, obligation) -> Finding`, registered in `engine/registry.py`. Fault-injection tests monkeypatch the registry or the named seam.

### 13.3 Dependencies and tooling

- **Python:** `requires-python = ">=3.12"`; CI runs 3.12 and 3.13. The workstation uses 3.13.
- **Runtime dependencies:** `PyYAML>=6.0.1,<7` (restricted loader only) and `Jinja2>=3.1.4,<4`. Configuration validation is written explicitly in `validate.py`, without a model framework, so every error message and closed-world rule is under the spec's control.
- **The `quickbase` extra:** `httpx>=0.27,<1`.
- **The `dev` extra:** `pytest>=8`, `hypothesis>=6.100`, `ruff`, `mypy`, `types-PyYAML`.
- **Locking:** `uv`, with `uv.lock` committed. A new dependency is recorded in `docs/decisions.md` first.
- **`mypy --strict`** applies to `canonical.py`, `grammar.py` and `engine/`.

### 13.4 Cross-platform rules

`.gitattributes`:

```
* text=auto eol=lf
fixtures/** -text
*.pdf binary
*.png binary
*.sqlite binary
```

- **Paths.** All paths are handled with `pathlib` and serialized with `/`. Evidence resolution is exact-case everywhere (§7.10).
- **Outputs** are bytes with LF line endings (§9.6). Console streams are reconfigured to UTF-8.
- **CI** runs `ubuntu-latest`, `windows-latest` and `macos-latest`. It runs `demo --all` and uploads `evaluation_ids.json`. A final job asserts that the three files are identical.

---

## 14 · Fixtures

### 14.1 Baseline: North Creek (`S01-clean`)

`tools/make_fixtures.py` writes every scenario. It is deterministic: no clock, no randomness, no import from `src/`. Its output is committed.

**Records:**
- **Scope:** obligations `O-nnn`, n = 001…040, with asset `A-nnn`, activity `visual_inspection`, project `NC-001` and revision `S1`. Acceptance: by "Synthetic Client Records Dept", at 2026-08-15T14:00:00Z, reference `SYN-SCOPE-S1`.
- **Inspections:** `INS-nnn`, revision `1`, current, `completed`, linked to `O-nnn` with project `NC-001` and asset `A-nnn`. `completed_at` is `2026-09-<dd>T15:00:00Z`, where dd = 1 + ((n − 1) mod 25), zero-padded.
- **Artifacts:** two per inspection, both revision `1` and current, with project `NC-001` and asset `A-nnn`:
  - `ART-nnn-R`, `inspection_report`, `O-nnn/report.pdf`;
  - `ART-nnn-P`, `photo`, `O-nnn/photo.png`.
- **Approvals:** `APR-nnn` covering `INS-nnn` revision `1`, `approved`. `decided_at` is the completion date plus two days at 17:00:00Z. `decided_by` is `reviewer@example.invalid`. `evidence_digest` comes from the generator's own §8.3 implementation, which must reproduce Appendix B.
- **Rows** are ordered by key, and columns follow the §5.5 order.

**Evidence bytes** (byte-stable):
- **`report.pdf`:** a minimal single-page PDF 1.4 in Helvetica, ASCII only, with correct `xref` offsets and no `/CreationDate`, `/ModDate` or `/ID`. Its text lines are:
  - `SYNTHETIC DEMO DATA`
  - `North Creek NC-001`
  - `Obligation O-nnn / Asset A-nnn`
  - `Visual inspection report - revision <R>`
- **`photo.png`:** a 16×12 RGB PNG of one color, (37n mod 256, 73n mod 256, 109n mod 256). IDAT holds `zlib.compress(raw, 0)`, there are no ancillary chunks, and CRCs come from `zlib.crc32`.

**Manifest.** `snapshot_id` is `NC-001-<scenario id>` and `synthetic` is true. Capture runs from 2026-10-01T17:55:00Z to 18:00:00Z. All four datasets are complete, with basis `[synthetic_universe]` and consistency `not_applicable`.

### 14.2 Scenarios

Each scenario is the baseline plus the stated change. `fixture` and `policy` default to the scenario itself and `north-creek-demo`. An added or replaced row takes every column it does not name from its obligation's baseline pattern: project `NC-001`, asset `A-nnn`, inspection `INS-nnn`, `document_kind` from the artifact suffix (`-R` is `inspection_report`, `-P` is `photo`), `decided_by` `reviewer@example.invalid`, `inspection_revision` `1`, and `is_current` true unless stated (AM-1).

| id | change from S01 | demonstrates |
|---|---|---|
| S01-clean | none | READY_FOR_REVIEW with checked scope and evidence |
| S02-missing-inspection | remove INS-017, ART-017-R, ART-017-P, APR-017 and `evidence/O-017/` | a record that never existed is found from the scope |
| S03-wrong-project-evidence | ART-023-P `project_id` := `NC-002` | mismatched evidence identity |
| S04-revised-evidence | ART-031-R rev 1 `is_current` := false; add ART-031-R rev 2, current, `O-031/report-r2.pdf`, text "revision 2"; APR-031 unchanged | an approval covering an earlier evidence set |
| S05-incomplete-capture | remove the inspection, artifact and approval rows and the evidence files for O-037…O-040 (the scope rows remain); the four datasets become `partial`, basis `[extraction_interrupted]` | absence from a partial view is not absence |
| S06-corrected | the same records as S01; `snapshot_id` NC-001-S06-corrected; capture 2026-10-02T17:55:00Z → 18:00:00Z | the corrected rerun for `compare S02 S06` |
| S07-duplicate-current | add INS-012B rev 1: current, completed, O-012, A-012, `visual_inspection`, completed 2026-09-13T15:00:00Z | two current inspections for one obligation |
| S08-silent-byte-change | rewrite `evidence/O-009/photo.png` with color (1, 2, 3); revision label unchanged | changed bytes under the same revision label |
| S09-missing-file | delete `evidence/O-014/photo.png`; the row stays | an established absent file |
| S10-missing-document | remove the ART-027-P row and its file | a missing required document kind |
| S11-no-scope | remove `scope.csv` and the manifest's `scope` member | no vacuous pass without expected work |
| S12-attachment-not-captured | delete `evidence/O-019/report.pdf`; `evidence_files` becomes `partial`, basis `[attachment_capture_skipped]` | an uncaptured file is UNKNOWN, not FAIL |
| S13-conflicting-current-revisions | add ART-033-P rev 2: current, `O-033/photo-r2.png`, color (9, 9, 9); rev 1 stays current | an R0 integrity failure attributed to its obligation |
| S14-revoked-approval | add APR-021-X: INS-021 rev 1, APR-021's digest, `revoked`, decided one day after APR-021 | the latest decision wins |
| S15-out-of-scope-record | add INS-099 rev 1: current, completed, O-099, A-099, `visual_inspection`, completed 2026-09-20T15:00:00Z | an advisory finding that does not block |
| S16-quickbase-clean | S01 as a Quickbase-shaped export (§14.3) | normalization equivalence |
| S17-quickbase-unmapped-value | S16 with INS-006's status label `Completed - pending QA` | an unmapped value is never guessed |
| S18-revision-binding | every approval has a null `evidence_digest`; `approval_items.csv` binds (ART-nnn-R, 1) and (ART-nnn-P, 1) to APR-nnn; O-031 revised as in S04; O-009 bytes changed as in S08. Policy `north-creek-revisions` | revision binding detects a new revision but, as documented, not same-label byte changes |
| S19-binding-strength-insufficient | fixture `S18-revision-binding`; policy `north-creek-demo` | a digest-requiring pack refuses weaker bindings as UNKNOWN |
| S20-dangling-reference | add APR-777: INS-777 rev 1, APR-001's digest, `approved`, decided 2026-09-30T17:00:00Z, reviewer | a complete claim contradicted by a dangling reference |
| S21-invalid-path | ART-005-R `relative_path` := `../O-005/report.pdf` | unsafe paths are quarantined and never opened |
| S22-case-mismatch | ART-008-P `relative_path` := `O-008/Photo.png` (the file on disk stays `photo.png`) | identical behavior on case-insensitive and case-sensitive file systems |
| S23-duplicate-key | append a second, identical row for INS-004 rev 1 | duplicate records are an established defect and are quarantined |
| S24-scope-not-accepted | manifest `scope.accepted` := null | an unaccepted scope cannot be ready |

### 14.3 The Quickbase-shaped export (S16, S17)

- **Tables.** Table IDs `bsyn00001`–`bsyn00004`, with FIDs as in §12.3.
- **Record IDs.**
  - obligations: 1–40;
  - inspections: 101–140;
  - artifacts: 201–280, with ART-nnn-R = 200 + 2n − 1 and ART-nnn-P = 200 + 2n;
  - approvals: 301–340.
- **`fields.json`** holds a `fieldResponse` object for every mapped FID plus fields 2 and 3: `{id, label, fieldType, mode: "", required, unique, properties: {}}`, with the `fieldType` values of §12.3. Field 2 is `timestamp`, labeled "Date Modified"; field 3 is `recordid`, labeled "Record ID#".
- **Pages.** One page per table: `metadata {totalRecords, numRecords, numFields, skip: 0, top: 1000}`. `fields` holds `{id, label, type}` display labels, which normalization ignores.
- **Values** use the §12.1 shapes:
  - users: `{"email": "reviewer@example.invalid", "id": "000001.syn", "name": "Synthetic Reviewer", "userName": "reviewer"}`;
  - field 2: the record's synthetic modification time, `2026-10-01T12:00:00Z`;
  - files: `{"url": "/files/<table>/<rid>/13", "versions": [{"versionNumber": 1, "fileName": "report.pdf" | "photo.png", "uploaded": "2026-09-…Z", "creator": {…}}]}`.
- **Files** live at `files/<table_id>/<rid>/13/v1/<fileName>`, with the same bytes as S01.
- **The capture manifest** declares the same scope, coverage, basis, consistency and capture times as S01, so the default `as_of` is equal. `two_pass` is `not_run`.

---

## 15 · Tests

### 15.1 Categories

| category | content |
|---|---|
| unit | every grammar (including TS edge cases: fractions of 7 digits, lowercase `z`, `24:00`, leap second, missing offset); the CSV dialect (BOM, ragged rows, blank lines, header violations); the YAML loader (duplicate keys, an unquoted `Yes:`, aliases, tags); every truth table in §7 as a parametrized table, including the partial-coverage columns; every R0 step and its ordering; every R6 branch in both binding modes; templates; the path rules (traversal, absolute, backslash, `:`, reserved names, trailing dot, exact case, symlinks and, on Windows, junctions); the atomic writer |
| vectors | Appendix B reproduced exactly; `canonical()` rejects floats, big integers, non-ASCII keys and lone surrogates |
| meta | `fixtures/oracle.yaml` parses equal to Appendix A in `docs/SPEC.md`; oracle self-consistency (every scenario's counts sum to the total implied by §A.1, and its outcomes agree with its `non_pass` list); the generator regenerates every committed fixture byte for byte; a public-safety scan of tracked files against `local/private_terms.txt` when that file exists; `docs/SPEC.md` contains no private-preface text |
| scenario | each Appendix A scenario: the status, every non-PASS finding (`key`, `outcome`, `reason`, `blocked_by`, `caused_by`, `required`), the counts, and every other finding PASS; plus the headline wording checks |
| fault injection | FI-1: R2 treats zero candidates as PASS, so S02 MUST fail. FI-2: R6 treats every digest as matching, so S04 and S08 MUST fail. FI-3: effective coverage is forced to complete, so S05 and S12 MUST fail. FI-4: the probe ignores letter case, so S22 MUST fail. FI-5: duplicate detection is off, so S23 MUST fail |
| property | P1 order invariance: Hypothesis permutations of every CSV's rows leave `evaluation_id` and `assessment_semantic_sha256` unchanged. P2 idempotence: two runs give byte-identical `assessment.json` and `report.html`. P3 coverage monotonicity: for every complete-coverage scenario and each dataset, downgrading that dataset to `partial` never turns a non-PASS finding into PASS and never yields READY_FOR_REVIEW. Hypothesis runs with a fixed `max_examples` and `derandomize=True`, so CI is reproducible |
| equivalence | `normalize(S16)` then `assess` gives the same `evaluation_id` and `assessment_semantic_sha256` as S01 |
| CLI | the exit-code table; a non-empty `--out` without `--force`; a malformed policy gives exit 2 and no outputs; an injected internal exception gives exit 2; `--version`; `evidence-digest` on S01 INS-001 prints APR-001's digest |
| HTML | a hostile `decided_by` (`<script>…`) is escaped; no `http(s)://` resource references; the CSP meta is present; no JavaScript |
| sql | the Appendix G queries over `export-sqlite` agree with the engine for every R0-clean, complete-coverage scenario |
| adapter | mapping validation (every rule); conversions (every type, including the multiple-choice list form); reference resolution and placeholders; file sanitization; `fields.json` type mismatch |
| connector | mock transport: ≥ 3 pages including a short "intelligent" page; per-page total accounting; a 429 with `retry-after`; exhausted retries; 401 and 403; a missing selected field; a change detected by pass 2; base64 decoding; the size limit; the allowlist refusing a `DELETE` and version 0; the token absent from every output and log |

The expected results in scenario tests come only from `fixtures/oracle.yaml`. A test MUST NOT compute expected verdicts with `src/`.

### 15.2 Acceptance criteria

| id | criterion | proven by |
|---|---|---|
| AC-01 | the clean fixture is READY_FOR_REVIEW | S01 |
| AC-02 | a missing inspection with its obligation retained gives R2 FAIL and BLOCKED | S02, FI-1 |
| AC-03 | with no expected-work source the result is UNKNOWN, never a vacuous pass | S11 |
| AC-04 | wrong project on required evidence gives R3 FAIL | S03 |
| AC-05 | a missing required document in a complete inventory gives R4 FAIL | S10 |
| AC-06 | a missing file in a complete evidence set gives R5 FAIL | S09 |
| AC-07 | changed bytes under an unchanged revision label give R6 FAIL under digest binding | S08, FI-2 |
| AC-08 | a changed revision with the old approval gives R6 FAIL in both binding modes | S04, S18, FI-2 |
| AC-09 | an attachment that was not captured gives UNKNOWN | S12, FI-3 |
| AC-10 | two current inspections for one obligation give R2 FAIL | S07 |
| AC-11 | a partial capture yields no unsupported absence conclusion, and the status is UNKNOWN | S05, FI-3, P3 |
| AC-12 | a malformed policy or an internal exception gives exit 2 and no outputs | CLI tests |
| AC-13 | shuffled input order gives identical identities | P1 |
| AC-14 | the canonical fixture and the equivalent export give identical identities | S16 equivalence |
| AC-15 | conflicting current revisions are visible, with no silent row loss | S13 |
| AC-16 | a revoked approval gives R6 FAIL | S14 |
| AC-17 | an out-of-scope record is advisory only | S15 |
| AC-18 | an unmapped source value gives UNKNOWN and is never guessed | S17 |
| AC-19 | `compare S02 S06` reports exactly R2–R6 for O-017 changed, and BLOCKED → READY_FOR_REVIEW | compare test |
| AC-20 | each fault injection makes its target scenarios fail | FI-1 … FI-5 |
| AC-21 | identical outputs on Windows, macOS and Linux | CI matrix |
| AC-22 | hostile strings are escaped; no external resources | HTML tests |
| AC-23 | unsafe paths are never opened | S21, unit |
| AC-24 | the Appendix B digests are reproduced | vector tests |
| AC-25 | revision binding cannot see same-label byte changes, and the report says so | S18 wording check |
| AC-26 | a digest-requiring pack does not accept revision bindings | S19 |
| AC-27 | a dangling reference into a dataset declared complete downgrades that dataset | S20 |
| AC-28 | case-only path differences behave identically on every operating system | S22, FI-4, CI |
| AC-29 | duplicate rows are reported and quarantined | S23, FI-5 |
| AC-30 | an independent SQL implementation agrees with the engine | sql tests |
| AC-31 | the connector never issues a write and never leaks the token | connector tests |

Report measured results only. This specification makes no claim about runtime, accuracy, labor saved or production scale.

---

## 16 · Build plan for Claude Code

### 16.1 Session protocol

1. Read `CLAUDE.md`, `docs/status.md`, and the spec sections the step names.
2. State the step's definition of done in one paragraph before writing code.
3. Write or extend the tests first, from this spec's tables and Appendix A.
4. Implement until `pytest`, `ruff check`, `ruff format --check` and `mypy` are green.
5. Update `docs/status.md`, and `docs/decisions.md` if anything was decided. Commit with a message of the form `<step>: <summary> (SPEC §x)`.
6. **Stop condition.** If the spec is ambiguous or contradicts itself, write the question and a proposed resolution in `docs/decisions.md`. When working autonomously, adopt the proposed resolution that is most conservative under §3, record it as an amendment, and continue.

### 16.2 Machine rules: the author's Windows 11 workstation

These rules are part of the specification because the build runs on that machine. Several were learned from incidents.

- **Never kill or time-limit native Windows programs from Git Bash.** Do not use `timeout N cmd`, `kill` or `pkill` in Git Bash on a native `.exe`: `python`, `uv`, `git`, `es` and the like.
  - **Why.** On 2026-10-09, MSYS2's SIGTERM emulation walked the "process tree" of a recycled PID in an elevated session. It killed about 36 system services and crashed the machine with bugcheck 0xEF.
  - **Instead.** Bound runtime only with the harness tool's own `timeout` parameter, PowerShell `Start-Process -PassThru` plus `Wait-Process -Timeout`, or Python `subprocess.run(..., timeout=)`. Python's timeout terminates the one child through its process handle.
  - **No retry loops** against a hung tool: diagnose the hang instead. Never use `taskkill /T`.
- **No heredocs or here-strings.** A PreToolUse hook blocks them. Author files with the Write and Edit tools. Commit with `git commit -F <file>`, writing the message file with the Write tool first. Keep shell commands under about 8,000 characters (a second hook refuses longer ones).
- **Paths and encoding.**
  - Use forward slashes in every shell command (`C:/quickbase/...`); Git Bash eats unquoted backslashes.
  - Set `PYTHONIOENCODING=utf-8` when running Python in Git Bash, or the console's cp1252 encoding crashes on non-ASCII output.
  - Git Bash rewrites arguments that begin with `/` before passing them to Windows programs; set `MSYS_NO_PATHCONV=1` if one is needed.
- **The interpreter and the environment.**
  - Use `python` (3.13.2) or `uv run`. `python3` is the Microsoft Store stub, and does nothing.
  - `uv` is at `C:/Users/user/AppData/Local/hermes/bin/uv.exe`, on the `PATH`.
  - Never `pip install` into the system Python: other house tools share it. The project uses its own `.venv` through uv. In Git Bash the venv activates with `source .venv/Scripts/activate`; prefer `uv run`.
- **Stay off shared and heavy resources.** Do not use Everything, `es` or `facet` (Everything was hung on 2026-10-09). Leave the GPU, WSL training environments, Docker and the shared Intercom bus alone. Start no background servers. Kill no processes.
- **Outward-facing actions.** `gh` is authenticated as the owner. A remote repository is created **private**. Making it public and publishing a site (D1) are the owner's decisions. Never push secrets: tokens live only in environment variables.
- **Disk.** C: is nearly full. Keep generated outputs under the repository's `out/` (gitignored) and small.

### 16.3 Steps

| step | builds | done when |
|---|---|---|
| **A0** scaffold | the repository and layout (§13.1); `pyproject.toml`; uv lock; `.gitattributes`; `.gitignore` (`out/`, `local/`, `.venv/`); CI workflow; `CLAUDE.md` (Appendix F); `docs/SPEC.md` (the public part of this file); docs stubs; CLI `--version`; a smoke test | `uv run pytest` green; `git init`; first commit |
| **A1** foundations | `vocab`, `grammar`, `yamlsafe`, `validate`, `errors`, `policy`, `io/snapshot`, `io/evidence`, `io/writer` | unit tests for every grammar, path rule, loader rule and CSV rule; malformed configuration raises `RunError` |
| **A2** canonical | `canonical.py`: evidence-set, snapshot-semantic and identity digests | Appendix B vectors pass; guard tests pass |
| **A3** fixtures and oracle | `tools/make_fixtures.py` for S01–S15 and S18–S24, policies, `fixtures/oracle.yaml`; meta tests; the scenario tests written (marked `scenario`, deselected until A4) | generator byte-reproducibility, oracle tamper and oracle self-consistency tests green |
| **A4** engine | `engine/*`: R0–R7, effective coverage, dependencies, status | every §7 truth table as a parametrized test; every canonical scenario matches the oracle; FI-1 … FI-5; P1–P3 |
| **A5** surfaces | JSON outputs, `report.html`, `compare`, `demo`, `evidence-digest`, `validate`, CLI exit codes | CLI and HTML tests; `demo --all` exits 0. **Checkpoint A: a complete offline demonstration** |
| **A6** SQL | `sqlexport.py`, `sql/*.sql`, the `export-sqlite` command | the differential SQL tests pass |
| **B1** mapping | `adapters/mapping.py` | mapping unit tests for every rule |
| **B2** normalize | `adapters/qb_export.py`, the `normalize` command, `assess --export` | conversion, reference, file and sanitization tests |
| **B3** export fixtures | S16 and S17 generated; the equivalence test | S16 ≡ S01; S17 matches its oracle. **Checkpoint B** |
| **C1** client | `qb_client.py` | the client tests under the mock transport |
| **C2** capture | `qb_capture.py` and the `capture-quickbase` command | capture tests: a mock app built from the S01 records, captured, normalized and assessed, gives S01's `(key, outcome, reason)` for every finding and READY_FOR_REVIEW. The `evaluation_id` differs, because the declared coverage basis differs (D-005). **Checkpoint C-mock** |
| **C3** live verification *(operator-gated)* | run against an authorized test app (Appendix D); keep sanitized responses as fixtures; reconcile §12.1 with reality; update `docs/limitations.md` with the date and configuration | replaying the recorded responses reproduces the normalized snapshot. **Checkpoint C** |
| **D1** publication *(operator-gated)* | make the repository public; publish `demo --all` as a static site | the site serves the engine's own outputs; no credentials anywhere |
| **R** review | audit the code against §3 and §15.2; list every gap with file and line; fix them; record the audit in `docs/status.md` | no open gap |

C3 needs a Quickbase account, an authorized app and a user token that only the owner can create. An autonomous build completes at **A0–A6, B1–B3, C1–C2 and R**, and describes the project with the wording of §18.1 for Checkpoint C-mock.

### 16.4 Prompts *(informative)*

- **Kickoff:** "Read CLAUDE.md and docs/SPEC.md §0–§6, §13 and §16. Do step A0 exactly. Finish with every check green and docs/status.md updated."
- **Each later step:** "Continue with step <id> from docs/SPEC.md §16.3. Read the sections it depends on first. Tests first. Record any ambiguity in docs/decisions.md with the most conservative resolution."
- **Review:** "Audit the implementation against docs/SPEC.md §3 invariants and §15.2 acceptance criteria. List every gap with file and line, then fix them."

---
## 17 · Security and privacy

- **Tokens.**
  - Read only from the configured environment variable.
  - Never accepted as a command-line argument, written to disk, put in a URL, or logged.
  - Exceptions and log records are sanitized before display, and a test scans every output for the token.
- **Read-only access.** The client exposes only the §12.4 allowlist. `DELETE` cannot be issued, and file versions are always ≥ 1.
- **HTML.** Autoescaping, no JavaScript and no external resources. The CSP meta restricts loading, and source strings are never rendered as markup.
- **Files.** Evidence is read through the PATH grammar and exact-case resolution. Links and reparse points are never followed, nothing outside the evidence root is read, size limits apply, and hashing is streamed.
- **Parsers.** YAML goes through the restricted safe loader only, CSV through the standard library, and JSON through `json` with the closed-world validators.
- **Output.** Nothing is written outside `--out`, writes are atomic, and existing outputs are protected unless `--force` is given (§9.6).
- **Public data** is synthetic only, and example emails use the reserved `.invalid` domain. Real captures belong in the gitignored `local/` directory.

---

## 18 · Claims, limitations, README

### 18.1 What can be claimed, by checkpoint

| checkpoint | accurate description |
|---|---|
| A | "An offline prototype that checks synthetic inspection-documentation snapshots against an explicit requirement pack, with a hand-written oracle and fault-injection tests." |
| B | A, plus "a Quickbase-shaped export adapter built from the official API contract and synthetic data." |
| C-mock | B, plus "a read-only capture client tested against a mock transport that follows Quickbase's published OpenAPI contract; not yet run against a live app." |
| C | B, plus "a read-only connector tested against a controlled Quickbase test app on <date> (<configuration>)." |
| never | "production-ready"; "compliance"; any statement that an organization uses it; runtime, accuracy or labor-saving numbers that were not measured |

### 18.2 Known limitations

- **One problem at a time per obligation.** The dependency chain shows the first blocking problem for an obligation. A second, independent problem appears after the first is fixed.
- **Revision binding** cannot detect changed bytes under an unchanged revision label (S18).
- **No transactional snapshot.** Quickbase offers none. The interleaved two-pass check detects change; it does not prevent it.
- **Coverage is a claim.** It is an assertion with a basis. The tool reports it; it cannot prove that no hidden records exist.
- **A hash proves bytes only.** It does not prove authorship, truthfulness, or that the physical work was done.
- **Fixed cardinality.** Exactly one current completed inspection per obligation; repeat inspections need a supersession rule.
- **Full-table capture.** Scoped capture with reference closure is deferred (§12.7).

### 18.3 README outline *(informative)*

1. The problem, in two sentences.
2. A screenshot of the S02 report.
3. Quick start: `uv sync`, then `demo --all`.
4. The four-step demonstration: run S02, open the O-017 finding, run S06, then `compare S02 S06`.
5. The four outcomes, and why UNKNOWN is not PASS.
6. What each check does.
7. SQL cross-check, with example queries.
8. Quickbase adapter status, in the §18.1 wording.
9. Limitations.
10. The synthetic-data and trademark notice.
11. License.

---

## 19 · Extensions *(informative)*

| extension | concrete use | boundary |
|---|---|---|
| Qualification-evidence pack family | match assignments to dated evaluation and qualification evidence. It keeps course completion, evaluation, qualification, client acceptance, assignment and requirement change apart. A course completion never becomes a universal "qualified" flag | needs task mappings and operator acceptance rules; never asserts regulatory qualification |
| Time-to-billing reconciler | explain differences between approved time, payroll input and proposed invoice lines, using synthetic records | a separate rule family; no inferred payroll policy; separation of duties respected |
| QBL change-impact analyzer | from exported QBL, find the explicit dependencies affected by a field or workflow change; flag `!BadRef`; state what the analysis could not see | a separate repository; no Quickbase emulator; export completeness is version-specific |
| Quickbase application inventory | readable documentation of accessible tables, fields, relationships and reports, from `GET /fields` and the table list | permissions decide what is visible; an API listing does not reveal business intent |
| Client-format adapter | transform the canonical dataset into a defined client export and validate it | only once a real target format exists |
| AI assistance | propose mappings from field names; draft requirement packs from supplied instructions; explain findings in plain language | proposals must pass the oracle tests and human review; a model never changes a verdict or supplies missing evidence |

---

## 20 · Questions before real-world use

1. What authoritative source defines expected work, and who accepts each scope revision?
2. Which deliverable is assessed, what makes it acceptable, and who accepts it?
3. Where do inspections, attachments, revisions and approvals live? Are revisions stored as records, as file versions, or overwritten in place?
4. Can an approval be tied to immutable record and file versions? Can the review process store an evidence digest (`evidence-digest` computes it), or only artifact revisions?
5. Can the required tables be read completely by an identity whose permissions are known, and who can attest to that?
6. Which checks already exist natively (custom data rules, summary-field reports; Appendix E), and where do the recurring exceptions come from?
7. Would an external snapshot review help, or would a native report solve the need?

If no reliable expected-work source exists, start with scope reconciliation and accept UNKNOWN coverage. If the main pain is changing undocumented applications, build the QBL analyzer or the application inventory first.

---

## 21 · Sources

Primary sources were checked on 2026-10-08 and 2026-10-09. Product availability, licensing and exact API behavior must be re-verified in the test environment.

- **[QB-OAS]** Quickbase, *RESTful JSON API*, OpenAPI 3.0.1 document, 67 operations. https://developer.quickbase.com/qb-openapi-v3.json
- **[QB-FT]** Quickbase API portal, *Field type details*. https://developer.quickbase.com/fieldInfo
- **[QB-PG]** Quickbase API portal, *Pagination*. https://developer.quickbase.com/pagination
- **[QB-ER]** Quickbase API portal, *Errors*. https://developer.quickbase.com/errors
- **[QB-AU]** Quickbase API portal, *Authorization*. https://developer.quickbase.com/auth
- **[S2]** Quickbase, *Custom data rules*. https://help.quickbase.com/docs/custom-data-rules
- **[S3]** Quickbase, *Document Creation overview*. https://help.quickbase.com/docs/document-creation-overview
- **[S5]** Quickbase, *Create and use user tokens*. https://help.quickbase.com/docs/create-and-use-user-tokens
- **[S6]** Quickbase, *Rate limiting overview*. https://help.quickbase.com/docs/rate-limiting-overview
- **[S7]** Quickbase, *Solution APIs*. https://help.quickbase.com/docs/solution-apis
- **[S8]** Quickbase, *QBL version 0.15 overview*. https://help.quickbase.com/docs/qbl-version-015-overview
- **[S9]** Quickbase, *QBL definition, structure, and syntax*. https://help.quickbase.com/docs/qbl-definition-structure-and-syntax
- **[S10]** Quickbase, *Pipelines in QBL v0.15*. https://help.quickbase.com/docs/pipelines-qbl-v015
- **[S12]** Quickbase, *Upload and download files*. https://help.quickbase.com/docs/upload-and-download-files
- **RFC 8785** (JCS), **RFC 3339** (timestamps), **RFC 2119** (keywords), **RFC 2606** (`.invalid`), **RFC 4180** (CSV).

---

## Appendix A · Scenario oracle (normative)

Copy this block verbatim to `fixtures/oracle.yaml`. A test asserts that the two parse to equal values.

### A.1 Interpretation

- **Ranges.** In a `key`, `O-NNN..MMM` expands to every zero-padded obligation number from NNN to MMM. In `blocked_by` and `caused_by`, `{id}` is replaced by the expanded subject id.
- **Unlisted findings** MUST be PASS.
- **`required`** defaults to `false` for R7 entries and `true` for every other entry, unless the entry sets it.
- **`reason`** defaults to `BLOCKED_BY_UPSTREAM` for NOT_EVALUATED entries; every other outcome states its reason (AM-1).
- **`blocked_by` and `caused_by`** default to `[]`. Every listed field is compared exactly (AM-1).
- **Expected total.** The number of findings is:
  - the number of listed R0 entries (1 if none: the `R0:snapshot:all` PASS);
  - plus 5;
  - plus 5 × `obligations`;
  - plus the number of listed R7 entries (1 if none).
- **`counts`** are the by-outcome counts over all findings.
- **Defaults.** `fixture` defaults to the scenario id and `policy` to `north-creek-demo`. `equivalent_to` requires identical `evaluation_id` and `assessment_semantic_sha256`.

```yaml
oracle_version: 3
expansion: "A token NNN..MMM inside a key expands to every zero-padded integer in [NNN, MMM]; '{id}' in blocked_by or caused_by is replaced by the subject id of the expanded key."
scenarios:
  S01-clean:
    status: READY_FOR_REVIEW
    obligations: 40
    non_pass: []
    counts: {PASS: 207, FAIL: 0, UNKNOWN: 0, NOT_EVALUATED: 0}
  S02-missing-inspection:
    status: BLOCKED
    obligations: 40
    non_pass:
      - {key: "R2:obligation:O-017", outcome: FAIL, reason: NO_CURRENT_INSPECTION}
      - {key: "R3:obligation:O-017", outcome: NOT_EVALUATED, blocked_by: ["R2:obligation:O-017"]}
      - {key: "R4:obligation:O-017", outcome: NOT_EVALUATED, blocked_by: ["R3:obligation:O-017"]}
      - {key: "R5:obligation:O-017", outcome: NOT_EVALUATED, blocked_by: ["R3:obligation:O-017"]}
      - {key: "R6:obligation:O-017", outcome: NOT_EVALUATED, blocked_by: ["R4:obligation:O-017", "R5:obligation:O-017"]}
    counts: {PASS: 202, FAIL: 1, UNKNOWN: 0, NOT_EVALUATED: 4}
  S03-wrong-project-evidence:
    status: BLOCKED
    obligations: 40
    non_pass:
      - {key: "R3:obligation:O-023", outcome: FAIL, reason: ARTIFACT_PROJECT_MISMATCH}
      - {key: "R4:obligation:O-023", outcome: NOT_EVALUATED, blocked_by: ["R3:obligation:O-023"]}
      - {key: "R5:obligation:O-023", outcome: NOT_EVALUATED, blocked_by: ["R3:obligation:O-023"]}
      - {key: "R6:obligation:O-023", outcome: NOT_EVALUATED, blocked_by: ["R4:obligation:O-023", "R5:obligation:O-023"]}
    counts: {PASS: 203, FAIL: 1, UNKNOWN: 0, NOT_EVALUATED: 3}
  S04-revised-evidence:
    status: BLOCKED
    obligations: 40
    non_pass:
      - {key: "R6:obligation:O-031", outcome: FAIL, reason: EVIDENCE_CHANGED_SINCE_APPROVAL}
    counts: {PASS: 206, FAIL: 1, UNKNOWN: 0, NOT_EVALUATED: 0}
  S05-incomplete-capture:
    status: UNKNOWN
    obligations: 40
    non_pass:
      - {key: "R1:dataset:inspections", outcome: UNKNOWN, reason: COVERAGE_PARTIAL}
      - {key: "R1:dataset:artifacts", outcome: UNKNOWN, reason: COVERAGE_PARTIAL}
      - {key: "R1:dataset:approvals", outcome: UNKNOWN, reason: COVERAGE_PARTIAL}
      - {key: "R1:dataset:evidence_files", outcome: UNKNOWN, reason: COVERAGE_PARTIAL}
      - {key: "R2:obligation:O-001..036", outcome: UNKNOWN, reason: CARDINALITY_UNCONFIRMED, caused_by: ["R1:dataset:inspections"]}
      - {key: "R3:obligation:O-001..036", outcome: UNKNOWN, reason: IDENTITY_UNCONFIRMED, caused_by: ["R1:dataset:artifacts"]}
      - {key: "R5:obligation:O-001..036", outcome: UNKNOWN, reason: AVAILABILITY_UNCONFIRMED, caused_by: ["R1:dataset:artifacts"]}
      - {key: "R6:obligation:O-001..036", outcome: NOT_EVALUATED, blocked_by: ["R5:obligation:{id}"]}
      - {key: "R2:obligation:O-037..040", outcome: UNKNOWN, reason: ABSENCE_UNCONFIRMED, caused_by: ["R1:dataset:inspections"]}
      - {key: "R3:obligation:O-037..040", outcome: NOT_EVALUATED, blocked_by: ["R2:obligation:{id}"]}
      - {key: "R4:obligation:O-037..040", outcome: NOT_EVALUATED, blocked_by: ["R3:obligation:{id}"]}
      - {key: "R5:obligation:O-037..040", outcome: NOT_EVALUATED, blocked_by: ["R3:obligation:{id}"]}
      - {key: "R6:obligation:O-037..040", outcome: NOT_EVALUATED, blocked_by: ["R4:obligation:{id}", "R5:obligation:{id}"]}
    counts: {PASS: 39, FAIL: 0, UNKNOWN: 116, NOT_EVALUATED: 52}
  S06-corrected:
    status: READY_FOR_REVIEW
    obligations: 40
    non_pass: []
    counts: {PASS: 207, FAIL: 0, UNKNOWN: 0, NOT_EVALUATED: 0}
  S07-duplicate-current:
    status: BLOCKED
    obligations: 40
    non_pass:
      - {key: "R2:obligation:O-012", outcome: FAIL, reason: MULTIPLE_CURRENT}
      - {key: "R3:obligation:O-012", outcome: NOT_EVALUATED, blocked_by: ["R2:obligation:O-012"]}
      - {key: "R4:obligation:O-012", outcome: NOT_EVALUATED, blocked_by: ["R3:obligation:O-012"]}
      - {key: "R5:obligation:O-012", outcome: NOT_EVALUATED, blocked_by: ["R3:obligation:O-012"]}
      - {key: "R6:obligation:O-012", outcome: NOT_EVALUATED, blocked_by: ["R4:obligation:O-012", "R5:obligation:O-012"]}
    counts: {PASS: 202, FAIL: 1, UNKNOWN: 0, NOT_EVALUATED: 4}
  S08-silent-byte-change:
    status: BLOCKED
    obligations: 40
    non_pass:
      - {key: "R6:obligation:O-009", outcome: FAIL, reason: EVIDENCE_CHANGED_SINCE_APPROVAL}
    counts: {PASS: 206, FAIL: 1, UNKNOWN: 0, NOT_EVALUATED: 0}
  S09-missing-file:
    status: BLOCKED
    obligations: 40
    non_pass:
      - {key: "R5:obligation:O-014", outcome: FAIL, reason: FILE_ABSENT}
      - {key: "R6:obligation:O-014", outcome: NOT_EVALUATED, blocked_by: ["R5:obligation:O-014"]}
    counts: {PASS: 205, FAIL: 1, UNKNOWN: 0, NOT_EVALUATED: 1}
  S10-missing-document:
    status: BLOCKED
    obligations: 40
    non_pass:
      - {key: "R4:obligation:O-027", outcome: FAIL, reason: KIND_MISSING}
      - {key: "R6:obligation:O-027", outcome: NOT_EVALUATED, blocked_by: ["R4:obligation:O-027"]}
    counts: {PASS: 205, FAIL: 1, UNKNOWN: 0, NOT_EVALUATED: 1}
  S11-no-scope:
    status: UNKNOWN
    obligations: 0
    non_pass:
      - {key: "R1:project:NC-001", outcome: UNKNOWN, reason: SCOPE_MISSING}
      - {key: "R7:snapshot:all", outcome: NOT_EVALUATED, blocked_by: ["R1:project:NC-001"]}
    counts: {PASS: 5, FAIL: 0, UNKNOWN: 1, NOT_EVALUATED: 1}
  S12-attachment-not-captured:
    status: UNKNOWN
    obligations: 40
    non_pass:
      - {key: "R1:dataset:evidence_files", outcome: UNKNOWN, reason: COVERAGE_PARTIAL}
      - {key: "R5:obligation:O-019", outcome: UNKNOWN, reason: NOT_CAPTURED, caused_by: ["R1:dataset:evidence_files"]}
      - {key: "R6:obligation:O-019", outcome: NOT_EVALUATED, blocked_by: ["R5:obligation:O-019"]}
    counts: {PASS: 204, FAIL: 0, UNKNOWN: 2, NOT_EVALUATED: 1}
  S13-conflicting-current-revisions:
    status: BLOCKED
    obligations: 40
    non_pass:
      - {key: "R0:artifact:ART-033-P#MULTIPLE_CURRENT_REVISIONS", outcome: FAIL, reason: MULTIPLE_CURRENT_REVISIONS}
      - {key: "R2:obligation:O-033", outcome: NOT_EVALUATED, blocked_by: ["R0:artifact:ART-033-P#MULTIPLE_CURRENT_REVISIONS"]}
      - {key: "R3:obligation:O-033", outcome: NOT_EVALUATED, blocked_by: ["R2:obligation:O-033"]}
      - {key: "R4:obligation:O-033", outcome: NOT_EVALUATED, blocked_by: ["R3:obligation:O-033"]}
      - {key: "R5:obligation:O-033", outcome: NOT_EVALUATED, blocked_by: ["R3:obligation:O-033"]}
      - {key: "R6:obligation:O-033", outcome: NOT_EVALUATED, blocked_by: ["R4:obligation:O-033", "R5:obligation:O-033"]}
    counts: {PASS: 201, FAIL: 1, UNKNOWN: 0, NOT_EVALUATED: 5}
  S14-revoked-approval:
    status: BLOCKED
    obligations: 40
    non_pass:
      - {key: "R6:obligation:O-021", outcome: FAIL, reason: APPROVAL_REVOKED}
    counts: {PASS: 206, FAIL: 1, UNKNOWN: 0, NOT_EVALUATED: 0}
  S15-out-of-scope-record:
    status: READY_FOR_REVIEW
    obligations: 40
    non_pass:
      - {key: "R7:inspection:INS-099", outcome: FAIL, reason: UNMATCHED_INSPECTION}
    counts: {PASS: 206, FAIL: 1, UNKNOWN: 0, NOT_EVALUATED: 0}
  S16-quickbase-clean:
    status: READY_FOR_REVIEW
    obligations: 40
    equivalent_to: S01-clean
    non_pass: []
    counts: {PASS: 207, FAIL: 0, UNKNOWN: 0, NOT_EVALUATED: 0}
  S17-quickbase-unmapped-value:
    status: UNKNOWN
    obligations: 40
    non_pass:
      - {key: "R0:inspection:INS-006@1#UNMAPPED_VALUE", outcome: UNKNOWN, reason: UNMAPPED_VALUE}
      - {key: "R2:obligation:O-006", outcome: NOT_EVALUATED, blocked_by: ["R0:inspection:INS-006@1#UNMAPPED_VALUE"]}
      - {key: "R3:obligation:O-006", outcome: NOT_EVALUATED, blocked_by: ["R2:obligation:O-006"]}
      - {key: "R4:obligation:O-006", outcome: NOT_EVALUATED, blocked_by: ["R3:obligation:O-006"]}
      - {key: "R5:obligation:O-006", outcome: NOT_EVALUATED, blocked_by: ["R3:obligation:O-006"]}
      - {key: "R6:obligation:O-006", outcome: NOT_EVALUATED, blocked_by: ["R4:obligation:O-006", "R5:obligation:O-006"]}
    counts: {PASS: 201, FAIL: 0, UNKNOWN: 1, NOT_EVALUATED: 5}
  S18-revision-binding:
    status: BLOCKED
    obligations: 40
    policy: north-creek-revisions
    non_pass:
      - {key: "R6:obligation:O-031", outcome: FAIL, reason: EVIDENCE_CHANGED_SINCE_APPROVAL}
    counts: {PASS: 206, FAIL: 1, UNKNOWN: 0, NOT_EVALUATED: 0}
  S19-binding-strength-insufficient:
    status: UNKNOWN
    obligations: 40
    fixture: S18-revision-binding
    non_pass:
      - {key: "R6:obligation:O-001..040", outcome: UNKNOWN, reason: BINDING_STRENGTH_INSUFFICIENT}
    counts: {PASS: 167, FAIL: 0, UNKNOWN: 40, NOT_EVALUATED: 0}
  S20-dangling-reference:
    status: UNKNOWN
    obligations: 40
    non_pass:
      - {key: "R0:approval:APR-777#DANGLING_REFERENCE", outcome: UNKNOWN, reason: DANGLING_REFERENCE, required: false}
      - {key: "R1:dataset:inspections", outcome: UNKNOWN, reason: COVERAGE_CONTRADICTED}
      - {key: "R2:obligation:O-001..040", outcome: UNKNOWN, reason: CARDINALITY_UNCONFIRMED, caused_by: ["R1:dataset:inspections"]}
    counts: {PASS: 165, FAIL: 0, UNKNOWN: 42, NOT_EVALUATED: 0}
  S21-invalid-path:
    status: UNKNOWN
    obligations: 40
    non_pass:
      - {key: "R0:artifact:ART-005-R@1#INVALID_PATH", outcome: UNKNOWN, reason: INVALID_PATH}
      - {key: "R2:obligation:O-005", outcome: NOT_EVALUATED, blocked_by: ["R0:artifact:ART-005-R@1#INVALID_PATH"]}
      - {key: "R3:obligation:O-005", outcome: NOT_EVALUATED, blocked_by: ["R2:obligation:O-005"]}
      - {key: "R4:obligation:O-005", outcome: NOT_EVALUATED, blocked_by: ["R3:obligation:O-005"]}
      - {key: "R5:obligation:O-005", outcome: NOT_EVALUATED, blocked_by: ["R3:obligation:O-005"]}
      - {key: "R6:obligation:O-005", outcome: NOT_EVALUATED, blocked_by: ["R4:obligation:O-005", "R5:obligation:O-005"]}
    counts: {PASS: 201, FAIL: 0, UNKNOWN: 1, NOT_EVALUATED: 5}
  S22-case-mismatch:
    status: BLOCKED
    obligations: 40
    non_pass:
      - {key: "R5:obligation:O-008", outcome: FAIL, reason: FILE_ABSENT}
      - {key: "R6:obligation:O-008", outcome: NOT_EVALUATED, blocked_by: ["R5:obligation:O-008"]}
    counts: {PASS: 205, FAIL: 1, UNKNOWN: 0, NOT_EVALUATED: 1}
  S23-duplicate-key:
    status: BLOCKED
    obligations: 40
    non_pass:
      - {key: "R0:inspection:INS-004@1#DUPLICATE_KEY", outcome: FAIL, reason: DUPLICATE_KEY}
      - {key: "R2:obligation:O-004", outcome: NOT_EVALUATED, blocked_by: ["R0:inspection:INS-004@1#DUPLICATE_KEY"]}
      - {key: "R3:obligation:O-004", outcome: NOT_EVALUATED, blocked_by: ["R2:obligation:O-004"]}
      - {key: "R4:obligation:O-004", outcome: NOT_EVALUATED, blocked_by: ["R3:obligation:O-004"]}
      - {key: "R5:obligation:O-004", outcome: NOT_EVALUATED, blocked_by: ["R3:obligation:O-004"]}
      - {key: "R6:obligation:O-004", outcome: NOT_EVALUATED, blocked_by: ["R4:obligation:O-004", "R5:obligation:O-004"]}
    counts: {PASS: 201, FAIL: 1, UNKNOWN: 0, NOT_EVALUATED: 5}
  S24-scope-not-accepted:
    status: UNKNOWN
    obligations: 40
    non_pass:
      - {key: "R1:project:NC-001", outcome: UNKNOWN, reason: SCOPE_NOT_ACCEPTED}
      - {key: "R2:obligation:O-001..040", outcome: NOT_EVALUATED, blocked_by: ["R1:project:NC-001"]}
      - {key: "R3:obligation:O-001..040", outcome: NOT_EVALUATED, blocked_by: ["R2:obligation:{id}"]}
      - {key: "R4:obligation:O-001..040", outcome: NOT_EVALUATED, blocked_by: ["R3:obligation:{id}"]}
      - {key: "R5:obligation:O-001..040", outcome: NOT_EVALUATED, blocked_by: ["R3:obligation:{id}"]}
      - {key: "R6:obligation:O-001..040", outcome: NOT_EVALUATED, blocked_by: ["R4:obligation:{id}", "R5:obligation:{id}"]}
      - {key: "R7:snapshot:all", outcome: NOT_EVALUATED, blocked_by: ["R1:project:NC-001"]}
    counts: {PASS: 5, FAIL: 0, UNKNOWN: 1, NOT_EVALUATED: 201}
```

### A.2 Headline wording and value checks

The generator writes independently computed values to `fixtures/scenarios/<id>/expected.json`.
- **S02:** the R2 explanation for O-017 contains `O-017`, `A-017`, `visual_inspection` and `scope S1`. Its evidence includes the locator `scope.csv#row=18`.
- **S03:** R3 `observed.mismatches` contains `{subject: "artifact:ART-023-P@1", field: "project_id", expected: "NC-001", observed: "NC-002"}`.
- **S04:**
  - `expected.approved_evidence_digests` equals `[APR-031.evidence_digest]`;
  - `observed.current_evidence_digest` equals the generator's independent digest of {ART-031-R rev 2, ART-031-P rev 1};
  - the two differ.
- **S18:** the R6 explanation for O-009 contains "undetectable". The R6 failure for O-031 has `observed.binding = "revisions"`.
- **S20:** the R1 inspections finding has `observed.reasons = ["COVERAGE_CONTRADICTED"]`.
- **S22:** the R5 explanation for O-008 contains `O-008/photo.png`.
- **S23:** the R0 finding has `observed = {count: 2, identical: true}`.
- **`evidence-digest`** on S01 for INS-001 prints APR-001's `evidence_digest`.

---

## Appendix B · Digest test vectors (normative)

Re-verified independently on 2026-10-09 with Python's `hashlib` and `json`.

**Content digests**
- `sha256("report bytes v2")` = `b2a0ee266b0d91a8f336f2670788660f9af02f7786731f8e48cdd8f1c91a2f75`
- `sha256("photo bytes v1")` = `bef35f09d275a850ccf60737b083c23dcc2f92bc29e269f0fad972e490dedbed`
- `sha256("photo bytes v2")` = `328ddc0fd972786ab7db817f25a87074ceca8f03117594a01ab456d234915d2f`

**V1:** two entries. `ART-T-01` is `inspection_report`, revision `2`, report bytes v2; `ART-T-02` is `photo`, revision `1`, photo bytes v1. The canonical bytes are 359 bytes of UTF-8 on one line:

```
{"artifacts":[{"artifact_id":"ART-T-01","document_kind":"inspection_report","revision":"2","sha256":"b2a0ee266b0d91a8f336f2670788660f9af02f7786731f8e48cdd8f1c91a2f75"},{"artifact_id":"ART-T-02","document_kind":"photo","revision":"1","sha256":"bef35f09d275a850ccf60737b083c23dcc2f92bc29e269f0fad972e490dedbed"}],"scheme":"inspection-reconcile/evidence-set/v1"}
```

The evidence-set digest is `sha256:e1c7a825b339db9678f73f6c0bfd9139daaca6fab91d74d366ff0d8b3a4c9d2a`.

**V2: the input order reversed.** The digest is identical to V1.

**V3: V1 with the photo bytes replaced by "photo bytes v2"**, revision label still `1`. The digest is `sha256:331e31150872399fa8304a7f051deb6d095e58267e2cc65f290cf682b96cfdad`.

**V4: the empty set.** The canonical bytes are `{"artifacts":[],"scheme":"inspection-reconcile/evidence-set/v1"}`, and the digest is `sha256:9266ce7ec70da3bf0e8cafb3931d0aab65a4d43e1df48de2791afbb844a812dc`. The engine never digests an empty set (R6 requires R4 to PASS); the vector pins the serializer.

---

## Appendix C · Glossary

| term | meaning |
|---|---|
| obligation | one required activity on one asset, taken from the accepted scope |
| accepted scope | a revisioned obligation list with an acceptance record: the independent statement of expected work |
| candidate | the single current inspection that R2 found for an obligation |
| E(O) | the candidate's current artifacts whose kinds the pack requires |
| coverage (declared, effective) | a dataset's completeness claim, and that claim after downgrades (§5.8) |
| completion | any superset of the captured records consistent with the effective coverage (§7.3) |
| quarantine | exclusion of an uninterpretable or ambiguous record from evaluation, with an R0 finding |
| attribution | the scope obligations an R0 finding concerns (§7.5.7) |
| root finding | a required FAIL or UNKNOWN finding with no `caused_by` |
| evidence-set digest | §8.3; what a digest-binding approval stores |
| `evaluation_id` | the semantic identity of a run (§8.5) |
| `provenance_id` | the identity of a run that also covers the exact input bytes |

---

## Appendix D · Quickbase test-app blueprint *(normative for step C3)*

Build the app in an account and realm you are authorized to use, by hand as described here or with one command, `tools/qb_build_test_app.py` (§22.17). Create the fields **in the order listed**: new fields take the next free ID starting at 6, so this order reproduces the §12.3 FIDs. When you create a relationship, add **no** lookup or summary fields in that dialog. Step C2 verifies every FID against `GET /fields`; any difference goes into a live mapping kept in the gitignored `local/quickbase-live.yml` (it holds the real table IDs), never into the demo mapping.

| table | fields in creation order (type) |
|---|---|
| Obligations | Obligation ID (Text, unique, required) · Project ID (Text) · Scope Revision (Text) · Asset ID (Text) · Activity (Text – Multiple Choice: `Visual Inspection`) |
| Inspections | Inspection ID (Text) · Revision (Numeric) · Is Current (Checkbox) · *relationship Obligations → Inspections* (creates the reference "Related Obligation", FID 9) · Project ID (Text) · Asset ID (Text) · Activity (Text – Multiple Choice) · Status (Text – Multiple Choice: `Complete`, `In Progress`, `Not Started`, `Cancelled`) · Completed At (Date/Time) |
| Artifacts | Artifact ID (Text) · Revision (Numeric) · Is Current (Checkbox) · *relationship Inspections → Artifacts* (FID 9) · Project ID (Text) · Asset ID (Text) · Document Kind (Text – Multiple Choice: `Inspection Report`, `Photo`) · File (File Attachment) |
| Approvals | Approval ID (Text) · *relationship Inspections → Approvals* (FID 7) · Inspection Revision (Numeric) · Evidence Digest (Text) · Decision (Text – Multiple Choice: `Approved`, `Rejected`, `Revoked`) · Decided At (Date/Time) · Decided By (User) |

- **Role.** Create a role, "Reconcile Reader", with **view** permission on all four tables, no add, modify or delete, and no admin rights. Assign it to the user that owns the API token. Create a user token for that user, assign it to this app only, and store it in the `QB_USER_TOKEN` environment variable.
- **Data.** `tools/make_fixtures.py --quickbase-import DIR` writes import CSVs for S01, with display labels and choice values. Import them in the order obligations, inspections, artifacts, approvals, mapping each reference column to the parent's Record ID#. Then upload the `report.pdf` and `photo.png` files to the Artifacts File field. At minimum, upload them for the obligations you intend to read.
- **Capture.** Write `local/qb-capture.yml` (§12.5) with the realm, the app ID and an attestation that the token's role reads every record. Run `capture-quickbase`, then `normalize`, then `assess`, and compare the result with S01's findings. The Record IDs differ; the identities match when the data matches.

---

## Appendix E · Native Quickbase parity *(informative)*

The checks can be partly approximated with native constructs. The table shows where a native build fits and why the external snapshot check remains distinct. Verify formula syntax in the test app.

| check | native construct | what native cannot express |
|---|---|---|
| R0 duplicate keys | a field's "Unique" property (prevents new duplicates); a summary report grouped by key with a count above 1 (finds existing ones) | quarantine semantics; duplicates in exported or merged data |
| R2 cardinality | a summary field on Obligations: Count of Inspections where Is Current is checked and Status is Complete; a formula checkbox `[# Current Complete] = 1`; a report on the checkbox | partial-coverage reasoning (UNKNOWN vs FAIL); a reproducible, versioned snapshot |
| R3 identity | lookups of the parent's Project ID and Asset ID onto the child; a formula checkbox comparing them; a custom data rule preventing mismatches on save | retroactive checking (custom data rules apply to added or modified records only, S2) |
| R4 required kinds | per-kind summary fields on Inspections (Count of current Artifacts where Document Kind = Photo) | requirement packs versioned outside the app |
| R5 availability | a formula on the File field testing for an attachment | reading and hashing the bytes |
| R6 binding | a lookup of the approved revision compared with the current revision | byte-level binding: same-label content changes are invisible |
| coverage | none | completeness as a claim with a basis |

---

## Appendix F · Repository `CLAUDE.md` (normative; copied verbatim at A0)

```markdown
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
```

---

## Appendix G · SQL cross-check (normative)

`export-sqlite` writes the snapshot's **step-1-valid rows**, those passing §7.5.1 (duplicates are included), and the pack's requirements to SQLite. Booleans are stored as 0 or 1, and timestamps in canonical UTC form.

```sql
CREATE TABLE obligations    (obligation_id TEXT, project_id TEXT, scope_revision TEXT, asset_id TEXT, activity_kind TEXT);
CREATE TABLE inspections    (inspection_id TEXT, revision TEXT, is_current INTEGER, obligation_id TEXT, project_id TEXT,
                             asset_id TEXT, activity_kind TEXT, completion_status TEXT, completed_at TEXT);
CREATE TABLE artifacts      (artifact_id TEXT, revision TEXT, is_current INTEGER, inspection_id TEXT, project_id TEXT,
                             asset_id TEXT, document_kind TEXT, relative_path TEXT);
CREATE TABLE approvals      (approval_id TEXT, inspection_id TEXT, inspection_revision TEXT, evidence_digest TEXT,
                             decision TEXT, decided_at TEXT, decided_by TEXT);
CREATE TABLE approval_items (approval_id TEXT, artifact_id TEXT, artifact_revision TEXT);
CREATE TABLE required_kinds (activity_kind TEXT, document_kind TEXT);
```

**`sql/r2_cardinality.sql`:**

```sql
SELECT o.obligation_id,
       COUNT(i.inspection_id) AS current_count,
       CASE WHEN COUNT(i.inspection_id) = 0 THEN 'NO_CURRENT_INSPECTION'
            WHEN COUNT(i.inspection_id) > 1 THEN 'MULTIPLE_CURRENT'
            WHEN SUM(i.completion_status = 'completed') = 1 THEN 'SINGLE_CURRENT_COMPLETED'
            ELSE 'NOT_COMPLETED' END AS r2_reason
FROM obligations o
LEFT JOIN inspections i ON i.obligation_id = o.obligation_id AND i.is_current = 1
GROUP BY o.obligation_id
ORDER BY o.obligation_id;
```

**`sql/r3_identity.sql`** lists, for single-candidate obligations, every identity mismatch:

```sql
WITH cand AS (
  SELECT o.obligation_id, o.project_id AS o_project, o.asset_id AS o_asset, o.activity_kind AS o_activity,
         i.inspection_id, i.project_id AS i_project, i.asset_id AS i_asset, i.activity_kind AS i_activity
  FROM obligations o JOIN inspections i ON i.obligation_id = o.obligation_id AND i.is_current = 1
  WHERE (SELECT COUNT(*) FROM inspections x WHERE x.obligation_id = o.obligation_id AND x.is_current = 1) = 1)
SELECT obligation_id, 'inspection' AS subject, inspection_id AS subject_id, 'project_id' AS field FROM cand WHERE i_project <> o_project
UNION ALL SELECT obligation_id, 'inspection', inspection_id, 'asset_id' FROM cand WHERE i_asset <> o_asset
UNION ALL SELECT obligation_id, 'inspection', inspection_id, 'activity_kind' FROM cand WHERE i_activity <> o_activity
UNION ALL SELECT c.obligation_id, 'artifact', a.artifact_id, 'project_id' FROM cand c
          JOIN artifacts a ON a.inspection_id = c.inspection_id AND a.is_current = 1 WHERE a.project_id <> c.o_project
UNION ALL SELECT c.obligation_id, 'artifact', a.artifact_id, 'asset_id' FROM cand c
          JOIN artifacts a ON a.inspection_id = c.inspection_id AND a.is_current = 1 WHERE a.asset_id <> c.o_asset
ORDER BY 1, 2, 3, 4;
```

**`sql/r4_missing_kinds.sql`:**

```sql
WITH cand AS (
  SELECT o.obligation_id, i.inspection_id, i.activity_kind
  FROM obligations o JOIN inspections i ON i.obligation_id = o.obligation_id AND i.is_current = 1
  WHERE (SELECT COUNT(*) FROM inspections x WHERE x.obligation_id = o.obligation_id AND x.is_current = 1) = 1
    AND i.completion_status = 'completed')
SELECT c.obligation_id, rk.document_kind AS missing_kind
FROM cand c JOIN required_kinds rk ON rk.activity_kind = c.activity_kind
WHERE NOT EXISTS (SELECT 1 FROM artifacts a WHERE a.inspection_id = c.inspection_id AND a.is_current = 1
                  AND a.document_kind = rk.document_kind)
ORDER BY 1, 2;
```

**`sql/r7_unmatched.sql`:**

```sql
SELECT DISTINCT i.inspection_id
FROM inspections i
WHERE i.is_current = 1
  AND (i.obligation_id IS NULL OR i.obligation_id NOT IN (SELECT obligation_id FROM obligations))
ORDER BY 1;
```

**`sql/r0_duplicate_keys.sql`:**

```sql
SELECT 'inspection' AS entity, inspection_id || '@' || revision AS key, COUNT(*) AS n FROM inspections GROUP BY inspection_id, revision HAVING n > 1
UNION ALL SELECT 'artifact', artifact_id || '@' || revision, COUNT(*) FROM artifacts GROUP BY artifact_id, revision HAVING COUNT(*) > 1
UNION ALL SELECT 'approval', approval_id, COUNT(*) FROM approvals GROUP BY approval_id HAVING COUNT(*) > 1
UNION ALL SELECT 'scope_row', obligation_id, COUNT(*) FROM obligations GROUP BY obligation_id HAVING COUNT(*) > 1
ORDER BY 1, 2;
```

**The differential test** runs over every R0-clean, complete-coverage scenario (S01–S04, S06–S10, S14, S15, S18, S22). It checks that:
- the R2 reason equals the engine's wherever R2 was evaluated;
- the R3 mismatch set is non-empty exactly when the engine's R3 FAILs;
- the R4 missing kinds equal the engine's `missing_kinds`;
- the R7 set equals the engine's unmatched inspections;
- `r0_duplicate_keys` is empty.

The test also runs over S23, where the duplicate query must return exactly `INS-004@1`.

---

## 22 · Amendments (normative)

Every change adopted after v3.0 is recorded here, each with its decision in `docs/decisions.md`. Where an amendment and an earlier section differ, the amendment wins. Among amendments, the later one wins.

| amendment | sections | decision | source |
|---|---|---|---|
| AM-1 | §22.1–§22.7 | D-003 | an independent re-derivation of Appendix A |
| AM-2 | §22.8 | D-004 | a rule-level test |
| AM-3 | §22.9 | D-007 | implementing B1/B2 |
| AM-4 | §22.10 | D-008, D-010, D-013 | implementing C2, the skip fallback and the streamed download |
| AM-5 | §22.11 | D-009 | a spec-only engine review |
| AM-6 | §22.12 | D-011 | preparing C3 |
| AM-7 | §22.13 | D-012 | a spec-only adapter review |
| AM-8 | §22.14 | D-014 | a spec-only review of the foundations |
| AM-9 | §22.15 | D-015 | a spec-only review of the output surfaces |
| AM-10 | §22.16 | D-016 | the pre-publication go/no-go review |
| AM-11 | §22.17 | D-017 | preparing C3: a builder for Appendix D |

AM-1 was adopted on 2026-10-09. The re-derivation found no oracle errors. AM-1 defines values and orders that the oracle relied on implicitly.

### 22.1 `expected` and `observed`

Every finding uses the values below; any field not listed is `{}`. NOT_EVALUATED findings have `{}` for both. Lists are sorted by the canonical JSON of their elements unless an order is stated. In that byte order `null` sorts after strings, numbers, arrays and `false`, and before `true` and objects (AM-5). R0 findings that merge several rows carry `count`, the number of merged rows.

| finding | `expected` | `observed` |
|---|---|---|
| R0 `INVALID_VALUE`, `UNMAPPED_VALUE`, `INVALID_PATH` | `{}` | `{violations: [{field, value, rule}], count}` |
| R0 `MALFORMED_ROW` | `{}` | `{dataset, field_count, header_count, count}` |
| R0 `DUPLICATE_KEY` | `{}` | `{count, identical}` |
| R0 `MULTIPLE_CURRENT_REVISIONS` | `{}` | `{current_revisions}` |
| R0 `COMPLETED_WITHOUT_TIMESTAMP` | `{}` | `{count}` |
| R0 `TIMESTAMP_AFTER_AS_OF` | `{}` | `{field, value, count}` (no `as_of`, so `compare` across capture times is unaffected) |
| R0 `DANGLING_REFERENCE` | `{}` | `{missing: [{field, target, target_key}], count}` |
| R1 project | `{project_id, scope_revision}` (from the policy) | `{scope_present, accepted, obligation_count, manifest_scope_revision, row_scope_revisions, row_project_ids}` |
| R1 dataset | `{effective: "complete_for_declared_scope"}` | `{declared, basis, consistency, effective, reasons}` |
| R2 | `{current_completed_inspections: 1}` | `{current_inspections: ["<id>@<rev>", …], superseded_revisions}` |
| R3 | `{project_id, asset_id, activity_kind}` (the obligation's) | `{mismatches: [{subject, field, expected, observed}]}`. `subject` is `inspection:<id>@<rev>` or `artifact:<id>@<rev>`; sorted by (subject, field); `[]` when there is no counterexample |
| R4 | `{document_kinds: sorted K}`, or `{document_kinds: null}` for `REQUIREMENT_UNDEFINED` | `{kinds: {kind: [sorted artifact ids]}, missing_kinds: [sorted]}` |
| R5 | `{files: <size of E(O)>}` | `{files: [{artifact: "<id>@<rev>", status, sha256?}]}`, sorted by `artifact`; `sha256` only when present |
| R6 | `{approved_evidence_digests, approved_artifact_revisions}`: the MISMATCH approvals' bindings for `EVIDENCE_CHANGED_SINCE_APPROVAL`, otherwise both `[]` | `{binding, current_evidence_digest, current_artifact_revisions, decision_approvals}`, plus `approved_revisions` for `REVISION_NOT_APPROVED` |
| R7 FAIL | `{}` | `{current_revisions, obligation_ids}` |

R1 project details:
- `scope_present`: the scope member and its file both exist.
- `accepted`: whether `scope.accepted` is non-null.
- `obligation_count`: \|S\|.
- `manifest_scope_revision`: a string or null.
- `row_scope_revisions` and `row_project_ids`: the sorted distinct values among non-quarantined scope rows.

R6 details:
- **Revision lists.** `current_artifact_revisions` is a sorted list of `{artifact_id, revision}` objects. `approved_artifact_revisions` is a sorted list of such lists, one per revision-mode MISMATCH approval (its set J). `approved_evidence_digests` lists the distinct non-null digests of the digest-mode MISMATCH approvals.
- **`binding`.**
  - `digest` or `revisions`, when every approval in Acur classified MATCH or MISMATCH used that binding;
  - `mixed`, when they used both;
  - the pack's `required_binding`, when none was classified (steps 1–5, or only UNDETERMINED approvals).
- **`decision_approvals`.**
  - steps 11–13: the sorted ids of L;
  - steps 8 and 10 (UNKNOWN from U): the deciding approval;
  - `EVIDENCE_CHANGED_SINCE_APPROVAL`: the sorted ids of the MISMATCH approvals in Acur;
  - otherwise `[]`.

### 22.2 `evidence`

Locators per finding: `{system, dataset, locator}`, where `system` is the manifest's `source.system` and `dataset` is `scope`, `inspections`, `artifacts`, `approvals` or `approval_items`. Locators are deduplicated, then sorted by (dataset, locator).

| finding | rows cited |
|---|---|
| R0 | the subject's rows |
| R1 project | every scope row (none when the scope is missing) |
| R1 dataset | none |
| R2 | O's scope row(s), the rows of Cur(O), and the non-current, non-quarantined inspection rows linked to O |
| R3 | O's scope row(s), I's row, and the rows of I's current artifacts |
| R4, R5 | I's row and the rows of E(O) |
| R6 | I's row, the rows of A, and their approval-item rows |
| R7 | the entity's unmatched current rows (AM-5) |
| NOT_EVALUATED | none |

### 22.3 Orders and reasons

- **`blocked_by` and `caused_by`** are sorted in code-point order of the key strings.
- **R6 step 10:** when several later UNDETERMINED approvals qualify, the reason is that of the first in (`decided_at`, `approval_id`) order, as in step 8.
- **Exclusive classification.** A cell listed as unmapped in the normalization file reports `UNMAPPED_VALUE` only, never also `INVALID_VALUE`.
- **PATH rules.** A value reports exactly one rule: the first violated in this order. It is absolute; it is too long; characters scanned left to right report a control or forbidden character. Then, segment by segment from the left: empty, dot segment (`.` and `..` report only this), trailing dot or space, segment too long, reserved name. The reserved-name test uses the part of the segment before its first `.`, with trailing spaces removed, case-insensitively.

### 22.4 Templates

- **Several qualifying subjects.** A template that names one artifact or approval names the first qualifying subject in sorted order, followed by ` (and N more)` when N > 0.
- **No path.** A missing path renders as `no path recorded`.
- **Placeholders.**
  - `{entity}` is "Inspection" or "Artifact".
  - `{key}` is the human key form: "INS-004 revision 1", "APR-001", "O-005" or "APR-001 / ART-001-R".
  - `{dataset}` is the dataset name.

### 22.5 Rows in identities

- **Quarantined `raw`** holds schema columns only, never `x_` columns. A quarantined artifact row keeps `relative_path` in `raw`, because the path is part of the defect; this is the one exception to the path exclusion of §8.4.
- **An `unattributable_row` subject** digests `{"dataset", "cells"}`, where `cells` is the object of schema columns (raw string or null, no `x_` columns). For `MALFORMED_ROW`, `cells` is the list of raw cells.

### 22.6 Probe without an evidence root

When `evidence_files` is omitted, every probe returns Absent without touching the file system. The `DATASET_MISSING` coverage then makes R5 UNKNOWN `NOT_CAPTURED`.

### 22.7 Intended consequences *(informative)*

- **A dangling reference on the attribution path** (an artifact or approval naming a missing inspection, S20) is attributed to the empty set, because its target does not exist, so the finding is advisory. Its uncertainty is carried by `COVERAGE_CONTRADICTED` on the target dataset. It is not unattributable. An approval item whose artifact reference dangles while its approval resolves keeps the approval's attribution (§7.5.7), so that finding can be required (AM-5).
- **A row whose key cannot be read** is unattributable. It downgrades its whole dataset to partial, so every dependent check becomes UNKNOWN. This blast radius is deliberate under I-2.

### 22.8 Amendment AM-2: entity references resolve by the entity id

An artifact's or approval's `inspection_id` refers to an inspection **entity**, so it resolves to any inspection row whose `inspection_id` cell is readable. The row's revision cell may be unreadable, and the row may be quarantined. A garbled revision on the inspection therefore yields one unattributable R0 finding (and the coverage downgrade), not false `DANGLING_REFERENCE` findings on every artifact and approval of that inspection. Item references to `(artifact_id, artifact_revision)` still require that exact key (decision D-004).

---

### 22.9 Amendment AM-3: mapping and export completions (decision D-007)

Found while implementing B1/B2:

- **Type and column compatibility.** `file` maps only to `relative_path`, and `relative_path` only from `file`. BOOL columns accept `checkbox` or `text`. TS columns accept `timestamp` or `text`. `reference` maps only to link columns (§5.5) and targets the parent table. A multiple-choice field feeding a KIND or enum column needs a `values` map, and its targets are validated against that column's grammar or enum.
- **Scope filter.** `scope_filter.fid` is the obligations table's `project_id` field, and its value equals `project.project_id`.
- **Derived fields.** `allow_derived` is a table-level boolean.
- **Long file names.** The 8-hex-digit suffix is taken from the SHA-256 of the original (unsanitized) name in UTF-8.
- **Export manifest.** `two_pass` is `not_run`, `stable` or `changed`. A `files[]` entry that is not `captured` has `path`, `bytes` and `sha256` set to null (except `bytes` on a `too_large` entry, AM-4); a captured entry has all three and a safe relative path.
- **`normalize` refuses an inconsistent export** (`EXPORT_INVALID`): page files that differ from `tables[role].pages`, a record count that differs from `retrieved`, captured bytes that differ from the declared size or SHA-256, an unmapped table or a mapped table missing, or a record lacking a mapped field. AM-7 adds two refusals: a record's file version with no `files[]` entry, and a repeated Record ID# in a table whose read the manifest declares clean.

---

### 22.10 Amendment AM-4: capture details (decision D-008)

Found while implementing C2:

- **Two-pass result.** A capture writes `two_pass` as `stable` or `changed`. `not_run` appears only in fixture exports. A table is `stable` only when three things hold: its pass-1 accounting held, its pass-2 read was complete, and both passes saw the same `{record id: date modified}` map.
- **File entries.** The files of in-scope current artifacts are `captured`, `not_captured` (with `capture_files: false`), `too_large` or `error` (a failed download). Every other artifact file is `out_of_scope` and is not downloaded.
  - An entry that is not `captured` has `path` and `sha256` null.
  - Its `bytes` is null too, except on a `too_large` entry. That entry records the decoded size only when the whole body was read (decision D-013).
  - The client streams a file body and never holds more than its base64 size limit: 4·⌈`max_file_bytes`/3⌉ payload characters, whitespace excluded. A download stopped at that limit leaves `bytes` null, because the full size is unknown.
  - `normalize` refuses any other combination (`EXPORT_INVALID`).
- **Coverage declarations** (§12.5 step 6):
  - **An incomplete read** is `partial` with `[pagination_incomplete]`.
  - **A complete read** that is not stable, or not attested, is `unverified`. Its basis lists only the bases earned: `query_total_matched`, plus `two_pass_stable` and `operator_attestation` when earned.
  - **Approval items.** The approvals declaration needs both the approvals and the approval-items tables to qualify.
  - **`evidence_files`** copies the artifacts declaration while that is not complete. Otherwise it is `partial` in two cases: `[attachment_capture_skipped]` when `capture_files: false`, and `[extraction_interrupted]` when an in-scope file is `too_large` or `error`.
- **Names and times.**
  - `export_id` is `<project_id>-qb-<capture start in UTC as YYYYMMDDTHHMMSSZ>`.
  - `source.description` is "Read-only capture of Quickbase app <app_id>".
  - `capture.started_at` and `capture.ended_at` are canonical TS values.
- **No keyset progress.** A non-empty page whose largest record ID does not exceed the previous `last` is an accounting failure. The read stops there, incomplete, instead of running on to `max_pages`.
- **Stored responses.** `fields.json`, the page files and `pass2.json` hold the parsed responses, re-serialized as 2-space-indented UTF-8 JSON, not the raw response bytes. The HTTP client returns parsed JSON.
- **The skip fallback (§12.1; decision D-010).**
  - **Trigger.** A table falls back when its first keyset query is refused with HTTP status 400. The decision uses the status, never the message text. A refusal after the first page, or any other status, is a run error.
  - **The read.** Capture re-reads that table with skip paging: `where` is the base filter only, the sort is Record ID# ascending, and `options.skip` is the number of records retrieved so far. The read runs until an empty page; a short page does not end it.
  - **Completeness.** The read is complete only when every page reports the first page's `totalRecords`, record IDs strictly increase across all pages, and `retrieved` equals that total. Otherwise the table is `partial` with `[pagination_incomplete]`. A page with no record ID above the previous maximum stops the read as an accounting failure, as in C-5.
  - **Pass 2** uses the paging mode of pass 1.
  - **The manifest** records `tables[].paging` as `keyset` or `skip`. The member is optional in the export schema, since fixture exports omit it, and it has no semantic effect.

---

### 22.11 Amendment AM-5: engine review corrections (decision D-009)

A third reviewer read every engine module against §7, §8 and §22 and reproduced each defect below with a controlled snapshot. Each one now has a regression test.

- **The scope member in identities (§8.4).** `"scope"` is `null` only when the manifest has no scope member. A member whose file is absent digests as `{"accepted", "rows": null, "scope_revision"}`. R1 reads the member's `accepted` and `scope_revision` even without the file, so such snapshots must not share an `evaluation_id` with each other or with a snapshot that has no member.
- **`identical` in `DUPLICATE_KEY` (§7.5.2)** compares schema cells only. Otherwise a normalized export's per-record `x_source` would make equal records differ, and an export would stop being equivalent to its canonical snapshot.
- **Attribution through an inspection entity (§7.5.7)** uses every inspection row whose `inspection_id` cell is readable, whatever its revision cell, as AM-2 does for references. This was a code defect; the text already said so.
- **Canonical order of `null` (§22.1).** Canonical-JSON byte order places `null` after strings, numbers, arrays and `false`, and before `true` and objects. The sentence "Canonical JSON sorts `null` first" was wrong and is replaced.
- **Wording only, no behavior change:**
  - §7.11: `approved_artifact_revisions` is not deduplicated, as §22.1 states.
  - §22.7: the dangling-reference sentence covers only the attribution path.
  - §22.2: R7 cites the unmatched current rows.
  - §7.5.7: a `MULTIPLE_CURRENT_REVISIONS` entity unions its current rows.
  - §8.4: a malformed row's `raw_cells` keeps file order.

---

### 22.12 Amendment AM-6: Appendix D import files (decision D-011)

Found while preparing C3. The import files written by `--quickbase-import` could not have built the test app:

- **References.** A new, empty table numbers its records 1, 2, … in import order. Each reference column therefore holds the parent's 1-based row position in the parent's import file. Before, it held the record IDs of the synthetic S16 export (101 and up), which do not exist in a fresh table.
- **Decided By.** `approvals.csv` includes `Decided By`, because `decided_by` is required (§5.5). A User field accepts only users of the realm, so `--decided-by EMAIL` sets the value; the default is the synthetic reviewer.
- **The procedure.** `docs/c3-runbook.md` turns Appendix D into commands. Its check is that the live assessment adds, removes and changes no finding's outcome or reason. `compare` still reports differences in `observed`, because the coverage basis legitimately differs (D-005).

---

### 22.13 Amendment AM-7: adapter review corrections (decision D-012)

A spec-only review of the four adapter modules reproduced each defect below through the mock app or an edited S16 export. Each turned a capture or export artifact into a source-data FAIL or PASS that the data does not establish. Each now has a regression test that fails on the old code.

- **A repeated Record ID#.** Record IDs are unique within a Quickbase table, so a record delivered twice is a capture artifact, for example from a page that re-delivered records before the stall guard stopped the read. `normalize` keeps the first occurrence when the table's manifest entry already reports a failed read (`retrieved ≠ total_records`, or `two_pass: changed`); the coverage declaration carries the uncertainty. Otherwise the export is refused (`EXPORT_INVALID`). Before, the repeat became a `DUPLICATE_KEY` FAIL, so an UNKNOWN status turned into BLOCKED.
- **A listed unmapped cell is always a violation (§7.5.1).** It reports `UNMAPPED_VALUE` even when it satisfies the column grammar. Before, a label such as `photo`, when the map knows only `Photo`, passed as a valid kind.
- **Unexpected JSON types (§12.3 Conversions).** They are listed as unmapped too. Before, `123` in a text field became a valid ID, and `1.5` a valid revision. The rule still holds at the source: `verify_fields` pins each field type, and Quickbase returns the documented JSON types.
- **The file list is closed-world.** A record's latest file version with no `files[]` entry is refused (`EXPORT_INVALID`). Before, it became a `FILE_ABSENT` FAIL under complete evidence coverage.

---

### 22.14 Amendment AM-8: foundations review corrections (decision D-014)

A spec-only review covered the canonical JSON, the grammars, the YAML loader and validators, the policy, the snapshot loader and the evidence probe. It found everything exact except two defects, each reproduced and each now with regression tests:

- **A defective CSV record is never a run error (§5.4, §7.14).** Before, one record the csv module could not parse aborted the whole run (exit 2) with `CSV_UNREADABLE`. Examples are text after a closing quote, an unterminated quoted field, and a cell over the module's default 128 KiB limit. Now such a record is R0 `MALFORMED_ROW` with its verbatim text as its one raw cell, and reading resumes at the next line. Cells have no length limit at reading time. Only bad UTF-8, a NUL byte and header violations, including a parse error in the header row, make a CSV unreadable.
- **Configuration integers are plain decimal (§6.2).** YAML 1.1 silently read `fid: 010` as field 8 and `0100` as 64. Every other integer form now loads as a string, which the validators reject where an integer is expected.
- **Clarifications:**
  - Locators count CSV records, not lines (§5.4).
  - An unparseable record reports `field_count` 1 (§7.5.1).
  - The TS offset `-00:00` is accepted and means UTC (RFC 3339's "unknown local offset").
  - YAML diagnostics name the file's full path.

---

### 22.15 Amendment AM-9: output-surface review corrections (decision D-015)

A spec-only review of the JSON outputs, the report, the templates, `compare`, the runner, `demo` and the CLI reproduced each item below. The templates, member orders, exit codes, escaping, CSP and determinism were exact.

- **`--force` for `normalize` and `capture-quickbase` (§9.6).** Before, it could never replace the command's own previous output, and a refused run could delete files. Each command now has its own output names, the check comes before anything is deleted, and the new output is staged and swapped in only on success.
- **Provenance of export runs (§9.3).** The run manifest listed the deleted temporary snapshot paths and omitted the export and the mapping. Now:
  - those snapshot inputs keep their digests with `path: null`;
  - the export's `capture-manifest.json`, its table files and the mapping file are inputs;
  - all of them enter `provenance_id`.
- **The demo's export scenarios** write their normalized snapshot to `<out>/<scenario>/snapshot`, as §11 already said.
- **`--log-json`** covers library log records, and is accepted before or after the command (§11).
- **The report.**
  - It carries the literal "READY_FOR_REVIEW is not approval" (§9.4).
  - Every root shows how many findings it blocks, even 0.
  - `index.html` does not link a report that a run error prevented.
- **`compare`.**
  - A malformed assessment is `COMPARE_INPUT_INVALID`, not an internal error.
  - `--out` refuses to replace a file without `--force`, and never replaces one of its own inputs (§11).
- **Small items:**
  - `assess --snapshot` with `--mapping` is a usage error.
  - A failed write also removes the directories it created.
  - The policy file is read once, so its recorded size and digest are of the parsed bytes.
  - The digest notation of `inputs[].sha256` is stated (§9.3).
  - The standard-output rule names `assess` (§11).

---

### 22.16 Amendment AM-10: an all-or-nothing `--force` swap (decision D-016)

The pre-publication review reproduced a breach of AM-9's own promise that "a refused or failed run leaves `--out` as it was". The swap deleted the previous output first and only then moved the new output in. On Windows, a file held open by another program can be neither deleted nor renamed, so `normalize --force` with one CSV open in a spreadsheet left `--out` with 1 of its 87 previous files. For `capture-quickbase` that loses a capture that cannot be taken again.

- **The swap** is now two phases of renames inside `--out` (§9.6). Every completed rename is undone on any failure, and the previous output is deleted only once the new one is in place.
- **A `.previous-*` directory** that a failed rollback leaves behind is never deleted by a later `--force`, because it may be the only copy.

Six tests cover it: fault injection at each rename, and the real Windows lock.

---

### 22.17 Amendment AM-11: a create-only builder for the Appendix D test app (decision D-017)

Building Appendix D by hand takes 29 fields (three of them relationships), 200 records and 80 uploads, and every step is a chance to break the field ids. `tools/qb_build_test_app.py` builds it with one command. It is the only code in the repository that writes to Quickbase, and it is bounded as follows:

- **It only creates, and its client enforces that before any request.**
  - The operation must be one of six: `createApp`, `createTable`, `createField`, `createRelationship`, `upsert` and `getFields`. Only GET and POST are sent.
  - An `upsert` carrying `mergeFieldId` or Record ID# is refused, and every table it creates must be keyed on Record ID#. So an `upsert` can only add records.
  - Nothing can update or delete.
- **It writes only into the app it creates.**
  - Every app, table, field and record id in a request must have come from a response in the same run. The one exception is the optional `--decided-by-id` user id. Any other id is refused before any request.
  - A field id Quickbase returns must be new: above 5, and not already used in that table.
  - Reference values are matched by key through `fieldsToReturn`, never assumed to be 1, 2, ….
- **It stays outside the package.** It lives in `tools/`, and the package never imports it. I-7 governs the package and every source it reads, which it never writes to. The builder writes only synthetic data, and only into an app it has just created. The read-only client and its allowlist (§12.4) are unchanged.
- **The token** comes from an environment variable or a file, never from the command line, and appears in no output. A value given where a variable name or a file path belongs is not echoed, because it may be the token itself.
- **A failed write is never repeated**, because it may have taken effect. Only an HTTP 429 is waited out and repeated, and a 5xx only for `getFields`.
- **It deletes nothing.** A failure after `createApp` stops the build and names the app. When `createApp`'s own outcome is unknown, the message says that an app may exist.
- **Without `--yes`** it prints the plan and sends nothing.

Before sending any record, it checks every table with `GET /fields` exactly as the capture does (§12.5 step 1). Then it writes two files, both validated with the package's own loaders:
- `local/quickbase-live.yml`, with the ids Quickbase assigned;
- `local/qb-capture.yml`.

The API forces three differences from Appendix D:
- **Obligation ID** is neither unique nor required. `createField` cannot set either property, and the allowlist has no update. Neither property affects capture or assessment: R0 finds duplicate keys itself (§7.5.2).
- **Decided By** is the token's own user. The JSON API documents only `{"id"}` as the write form of a User field. The builder reads that id from the Record Owner (field 4) of its first created record, and `--decided-by-id` overrides it. No finding's outcome or reason depends on `decided_by`.
- **The attestation** written to `local/qb-capture.yml` is true for the token that built the app and owns it. The least-privilege "Reconcile Reader" role and its token stay manual.

The tool's tests build against a fake realm that enforces the OpenAPI request shapes and the documented write formats. They then capture, normalize and assess what was built. The result is S01's findings as the oracle states them, including when the realm assigns other field ids. This checks the builder and the reader against each other, not against Quickbase. C3 remains "not yet run against a live Quickbase app" until a live capture is compared (§18.1).

One question only a live realm can answer: the builder creates Date/Time fields as `timestamp`, which the OpenAPI enum allows and §12.3 expects, while the API portal's example uses `datetime`. If a realm refuses `timestamp` or reports another type, the build stops at that field, before any record exists, and says why.

The same review found one defect in the package. `capture-quickbase` echoed `token_env` in `QB_TOKEN_MISSING` and in its validation error, so a token pasted there in place of a variable's name was printed. A user token is lower case. A `token_env` value is now shown only when it is an upper-case name, and a validation error never shows it (I-7).

*End of specification v3.0.*
