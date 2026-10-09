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
| Quickbase JSON API facts | Checked on 2026-10-09 against Quickbase's official OpenAPI document and API portal pages (SPEC §12.1). The facts marked unverified there are checked by the connector's schema verification at run time. |
| Quickbase export adapter (normalize) | Built from the published contract and synthetic exports. S16 normalizes to S01's exact identities. |
| Quickbase capture client | Tested only against a mock transport. **Not yet run against a live Quickbase app** (step C3, which needs an authorized test app and a user token). |

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

## Never claimed
"Production-ready", "compliance", that any organization uses the tool, or any runtime, accuracy or labor-saving
figure that was not measured.
