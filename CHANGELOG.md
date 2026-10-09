# Changelog

## 0.1.0 · 2026-10-09

The first public release. Every requirement below is specified in [docs/SPEC.md](docs/SPEC.md). Decisions and
amendments AM-1 to AM-10 are in [docs/decisions.md](docs/decisions.md).

### The engine
- **Eight checks in a dependency chain:** R0 input integrity, R1 scope and coverage, R2 cardinality, R3 identity,
  R4 required artifacts, R5 availability, R6 approval binding (by digest or by revision), and R7 unmatched records
  (advisory).
- **Open-world outcomes.** PASS, FAIL, UNKNOWN or NOT_EVALUATED, rolled up into BLOCKED, UNKNOWN or
  READY_FOR_REVIEW. An absence-based FAIL needs complete, declared coverage.
- **Stable identities.** `evaluation_id`, `assessment_semantic_sha256` and `provenance_id` are byte-identical
  across row order and across Windows, macOS and Linux.

### Outputs
- `assessment.json`, a self-contained `report.html` (no scripts, no external requests, light and dark), and a run
  manifest recording which input bytes were read.
- `compare` diffs two runs finding by finding. `demo --all` runs 24 scenarios against a hand-written oracle.
- `export-sqlite` exports the snapshot, and the SQL in `sql/` cross-checks the engine independently.

### Quickbase
- **A field-ID mapping**, verified against the live field list by `fieldType` and mode.
- **`normalize`** turns a Quickbase export into a canonical snapshot with the same identities.
- **A read-only capture client:**
  - a four-operation allowlist and rate limiting;
  - keyset pagination with a skip-paging fallback, per-page total accounting and an interleaved two-pass
    consistency check;
  - streamed file downloads with a size cap;
  - token redaction.
- **Tested against a mock app** built to the published OpenAPI contract. It has not yet run against a live
  Quickbase app; [docs/c3-runbook.md](docs/c3-runbook.md) describes that step.

### Verification
- **Tests:** 816, with five fault injections and three properties (row-order invariance, idempotence and coverage
  monotonicity).
- **CI:** Windows, macOS and Linux on Python 3.12 and 3.13, with a cross-platform identity comparison.
- **Independent reviews:** separate passes re-derived the oracle and reviewed the engine, the adapters, the
  foundations and the outputs against the specification alone. Every confirmed code defect has a regression test.
