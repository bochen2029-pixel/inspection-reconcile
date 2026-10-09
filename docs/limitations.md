# Limitations

What is verified, what is synthetic, and what is unsupported (SPEC §18).

## Data
All fixtures, examples and reports describe the fictional North Creek project (NC-001). No real organization's data
is used anywhere in the repository.

## Verification status

| part | status |
|---|---|
| Engine semantics (R0–R7, coverage, status, identities) | Verified against a hand-written oracle of 24 scenarios, which was independently re-derived from the specification. Also covered by rule-level tests, fault injection and property tests. |
| Digests | Appendix B vectors reproduced. Fixture digests come from an independent implementation in the generator. |
| Cross-platform identity | Locally verified on Windows. CI compares Linux, Windows and macOS. |
| Quickbase JSON API facts | Checked on 2026-10-09 against Quickbase's official OpenAPI document and API portal pages (SPEC §12.1). The facts that the documents left open were then verified against a live realm in step C3 (SPEC §22.18). |
| Quickbase export adapter (normalize) | Built from the published contract and synthetic exports. S16 normalizes to S01's exact identities. |
| Quickbase capture client | **Verified against a live Quickbase test app on 2026-10-09** (step C3, SPEC §22.18). The app was built by `tools/qb_build_test_app.py` in a free-trial realm owned by the operator. The capture paged by keyset, got 80 of 80 files, and earned completeness on all three bases. The live assessment is READY_FOR_REVIEW, 207 of 207 PASS, and comparing it with S01 shows no finding added, removed or changed in outcome or reason (Checkpoint C). Before that, it was tested against a mock app that follows the published contract: pagination, rate limits, retries, permission errors, schema drift, mid-capture changes, oversized files and token redaction (Checkpoint C-mock, D-005). |
| Quickbase keyset filter | The `{3.GT.<n>}` comparison on Record ID# is not confirmed by the published documentation, but the live realm in step C3 accepted it. If a realm refuses it (HTTP 400 on a table's first query), the capture falls back to skip paging with per-page total accounting (SPEC §12.1, D-010). Both modes are tested against the mock, and the manifest records which one ran. |
| Live paths not yet exercised | The C3 run covered one realm, with the app owner's token. These paths are tested only against the mock: the least-privilege "Reconcile Reader" role and its own token, the skip fallback, tables longer than one page, files near `max_file_bytes`, and throttling (HTTP 429). |

## Semantics
- **One problem at a time per obligation.** The dependency chain shows the first blocking problem; a second,
  independent one appears after the first is fixed.
- **Revision binding** cannot detect changed bytes under an unchanged revision label.
- **No transactional snapshot.** Quickbase offers none; the two-pass check detects change but cannot prevent it.
- **Coverage is a claim.** It is an assertion with a basis; the tool reports it and cannot prove that no hidden
  records exist.
- **A hash proves bytes only.** It proves nothing about authorship, truthfulness, or whether the physical work was
  done.
- **Fixed cardinality.** Exactly one current, completed inspection per obligation; repeat inspections need a
  supersession rule.
- **Full-table capture.** Scoped capture with reference closure is specified but not implemented (SPEC §12.7).
- **A local, trusted evidence root.** The probe refuses links found while it resolves a path. A file that is swapped
  for a link between resolution and reading would still be followed. The evidence root is assumed to be a local
  directory that only the operator controls.

## Never claimed
"Production-ready", "compliance", that any organization uses the tool, or any runtime, accuracy or labor-saving
figure that was not measured.
