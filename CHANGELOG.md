# Changelog

## Unreleased

### Live verification (step C3)
- **`tools/qb_build_test_app.py`** builds the Appendix D test app in your own Quickbase realm with one command:
  - the app, the four tables, their fields and relationships, and the 200 S01 records with their 80 files;
  - then `local/quickbase-live.yml` and `local/qb-capture.yml`.

  It is the only code in the repository that writes to Quickbase. It only creates, only inside the app it creates,
  and the package never imports it (SPEC AM-11, decision D-017). Without `--yes` it sends nothing.
- **`docs/c3-runbook.md`** gains a fast path built on it.
- **The guides** are rebuilt reproducibly. Word files are byte-identical from the same commit, and the PDFs are
  identical in text, layout and appearance.

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

### Documentation
- **The guides.** A User Guide (35 pages) and an Administrator Guide (32 pages), as designed PDFs, Word documents
  and Markdown sources, in [docs/guide/](docs/guide/). `tools/build_guides.py` rebuilds them.
- **The guides are tested.** `tests/meta/test_guides.py` runs every annotated command in them and checks its exit
  code. It also checks that every image exists, and that the run-error catalog matches the codes the package raises.
- **Screenshots.** `tools/make_screenshots.py` regenerates every screenshot from the tool's own output.

### Verification
- **Tests:** 824, with five fault injections and three properties (row-order invariance, idempotence and coverage
  monotonicity).
- **CI:** Windows, macOS and Linux on Python 3.12 and 3.13, with a cross-platform identity comparison.
- **Independent reviews:** separate passes re-derived the oracle and reviewed the engine, the adapters, the
  foundations and the outputs against the specification alone. Every confirmed code defect has a regression test.
