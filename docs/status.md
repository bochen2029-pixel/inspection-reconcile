# Status

Step plan: SPEC §16.3. Each step ends green (`pytest`, `ruff check`, `ruff format --check`, `mypy`).

| step | state | notes |
|---|---|---|
| A0 scaffold | done | layout, uv lock, CI matrix, CLAUDE.md, docs |
| A1 foundations | done | grammars, restricted YAML, validators, snapshot loading, evidence probe, atomic writer |
| A2 canonical | done | canonical JSON with guards; Appendix B vectors reproduced |
| A3 fixtures and oracle | done | independent generator (1,977 files), oracle equals Appendix A, self-consistency, byte reproducibility |
| A4 engine | done | all 22 canonical scenarios match the oracle; FI-1..FI-5; P1-P3; AM-1 and AM-2 |
| A5 surfaces | done | JSON, HTML, compare (AC-19), demo, evidence-digest, CLI. **Checkpoint A** |
| A6 SQL | in progress | helper task T4 (sqlexport, Appendix G, differential test) |
| B1 mapping | in progress | helper task T3 |
| B2 normalize | in progress | helper task T3 |
| B3 export fixtures | in progress | fixtures S16/S17 exist; equivalence test in T3. **Checkpoint B** |
| C1 client | done | helper task T2, merged |
| C2 capture | in progress | helper task T3 (mock transport). **Checkpoint C-mock** |
| C3 live verification | operator-gated | needs a Quickbase account, app and token |
| D1 publication | operator-gated | the repository is private; publication is the owner's decision |
| R review | pending | |
