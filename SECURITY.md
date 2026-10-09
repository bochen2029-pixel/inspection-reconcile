# Security policy

## Reporting a vulnerability

Please report security problems privately, through GitHub's **Report a vulnerability** button on this repository's
Security tab. Do not open a public issue for a vulnerability. You should receive an answer within a week.

## Supported versions

Only the latest release on `main` receives fixes.

## What the tool is designed to guarantee

These properties are deliberate, and a way around any of them is a vulnerability:

- **Read-only Quickbase access.** The capture client's allowlist holds four read operations and is checked before
  any I/O; no write or delete can be issued (SPEC §12.4, §17).
- **The token stays secret.** It is read only from the environment variable named in the capture configuration.
  It never appears in files, URLs, logs, reports or exceptions, and a test scans every output for it.
- **Evidence files are read safely.** Paths pass a strict grammar, resolution is exact-case on every operating
  system, symbolic links, junctions and reparse points are never followed, nothing outside the evidence root is
  read, and size limits apply while streaming (SPEC §7.10).
- **The parsers are closed.** YAML goes through a restricted loader (no aliases, anchors, tags or merge keys;
  integers are plain decimal), and every configuration and manifest is validated closed-world.
- **The reports are inert.** Each `report.html` is autoescaped, carries a Content-Security-Policy that allows no
  scripts and no external requests, and contains no JavaScript.
- **Outputs stay inside `--out`.** Writes are atomic, and `--force` deletes only the command's own output names.

## Out of scope

The demonstration data is synthetic. The tool does not authenticate users and holds no secrets of its own. Running
it against a Quickbase app requires a user token that the operator creates and controls.
