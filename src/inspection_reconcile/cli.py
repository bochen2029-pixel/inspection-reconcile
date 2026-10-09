"""Command-line contract (SPEC §11): subcommands, exit codes, the top-level guard."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from inspection_reconcile import __version__
from inspection_reconcile.errors import RunError
from inspection_reconcile.grammar import parse_ts
from inspection_reconcile.io.writer import (
    CAPTURE_DIRS,
    CAPTURE_OUTPUTS,
    NORMALIZE_DIRS,
    NORMALIZE_OUTPUTS,
    json_bytes,
    prepare_out,
    replacing_out,
    write_files,
)
from inspection_reconcile.vocab import (
    EXIT_COMPARE_DIFF,
    EXIT_OK,
    EXIT_RUN_ERROR,
    STATUS_EXIT,
)

LOG_JSON = False
LOG_JSON_HELP = "write diagnostics to stderr as JSON lines"


def _configure_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8")
            except (ValueError, OSError):
                pass


def diag(level: str, code: str, message: str) -> None:
    if LOG_JSON:
        sys.stderr.write(
            json.dumps({"level": level, "code": code, "message": message}, ensure_ascii=False) + "\n"
        )
    else:
        sys.stderr.write(f"{level}: {code}: {message}\n")


class JsonLogFormatter(logging.Formatter):
    """--log-json for library log records: the members diag() writes, plus the logger's name (SPEC §11)."""

    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(
            {
                "level": record.levelname.lower(),
                "code": "LOG",
                "logger": record.name,
                "message": record.getMessage(),
            },
            ensure_ascii=False,
        )


def _as_of(text: str | None) -> datetime | None:
    if text is None:
        return None
    ts = parse_ts(text)
    if ts is None:
        raise RunError("USAGE", f"--as-of {text!r} is not an RFC 3339 timestamp with an offset")
    return ts


def _summary(status: str, counts: dict[str, dict[str, int]], evaluation_id: str) -> str:
    roots = counts["roots"]
    return (
        f"{status}  roots: FAIL={roots['FAIL']} UNKNOWN={roots['UNKNOWN']}  "
        f"not_evaluated={counts['required']['NOT_EVALUATED']}  evaluation_id={evaluation_id[:19]}…"
    )


# ------------------------------------------------------------------------------------------- commands


def cmd_validate(args: argparse.Namespace) -> int:
    from inspection_reconcile.runner import evaluate

    evaluation = evaluate(Path(args.snapshot), Path(args.policy))
    defects = [f for f in evaluation.assessment.findings if f.check_id == "R0" and f.outcome != "PASS"]
    for f in defects:
        print(f"{f.key}  {f.outcome}  {f.reason}  {'required' if f.required else 'advisory'}")
    print(f"assessable: {len(defects)} record-level defect(s) would be reported under R0")
    return EXIT_OK


def cmd_assess(args: argparse.Namespace) -> int:
    from inspection_reconcile.runner import evaluate, evaluate_export

    if args.snapshot and args.mapping:
        raise RunError("USAGE", "--mapping applies only to --export")
    if args.export and not args.mapping:
        raise RunError("USAGE", "--export needs --mapping")
    out = Path(args.out)
    if not args.force:
        prepare_out(out, force=False)
    as_of = _as_of(args.as_of)
    if args.snapshot:
        evaluation = evaluate(Path(args.snapshot), Path(args.policy), as_of)
    else:
        evaluation, _ = evaluate_export(Path(args.export), Path(args.mapping), Path(args.policy), as_of)
    files = evaluation.outputs()
    prepare_out(out, args.force)
    write_files(out, files)
    a = evaluation.assessment
    print(_summary(a.status, a.counts(), a.evaluation_id))
    return STATUS_EXIT[a.status]


def cmd_normalize(args: argparse.Namespace) -> int:
    try:
        from inspection_reconcile.adapters.mapping import load_mapping
        from inspection_reconcile.adapters.qb_export import normalize
    except ImportError as exc:  # pragma: no cover - present once step B2 is merged
        raise RunError("NOT_AVAILABLE", f"the Quickbase export adapter is not installed: {exc}") from exc
    mapping = load_mapping(Path(args.mapping))
    out = Path(args.out)
    with replacing_out(out, args.force, NORMALIZE_OUTPUTS, NORMALIZE_DIRS) as target:
        normalize(Path(args.export), mapping, target)
    print(f"normalized snapshot written to {out.as_posix()}")
    return EXIT_OK


def cmd_compare(args: argparse.Namespace) -> int:
    from inspection_reconcile.report.compare import assessment_file, compare, has_differences, load_assessment

    before, after = Path(args.before), Path(args.after)
    target = Path(args.out) if args.out else None
    if target is not None:
        if target.resolve() in {assessment_file(before).resolve(), assessment_file(after).resolve()}:
            raise RunError(
                "COMPARE_OUT_IS_INPUT", f"{target.as_posix()}: --out names an input of this comparison"
            )
        if target.is_dir():
            raise RunError("OUT_INVALID", f"{target.as_posix()}: is a directory; --out names a file")
        if target.exists() and not args.force:
            raise RunError("OUT_EXISTS", f"{target.as_posix()}: exists (use --force)")
    comparison = compare(load_assessment(before), load_assessment(after))
    if target is not None:
        write_files(target.parent, {target.name: json_bytes(comparison)})
    print(
        f"{comparison['before']['status']} -> {comparison['after']['status']}  "
        f"changed={len(comparison['changed'])} added={len(comparison['added'])} "
        f"removed={len(comparison['removed'])} unchanged={comparison['unchanged_count']}"
    )
    return EXIT_COMPARE_DIFF if has_differences(comparison) else EXIT_OK


def cmd_demo(args: argparse.Namespace) -> int:
    from inspection_reconcile.demo import run_demo

    return run_demo(Path(args.fixtures), Path(args.out), args.scenario, args.force)


def cmd_evidence_digest(args: argparse.Namespace) -> int:
    from inspection_reconcile.engine.evidence_digest import evidence_digest
    from inspection_reconcile.io.snapshot import load_snapshot
    from inspection_reconcile.policy import load_policy

    snapshot = load_snapshot(Path(args.snapshot))
    policy = load_policy(Path(args.policy))
    digest, entries = evidence_digest(snapshot, policy, args.inspection, args.revision)
    print(digest)
    print(json.dumps(entries, indent=2, ensure_ascii=False))
    return EXIT_OK


def cmd_export_sqlite(args: argparse.Namespace) -> int:
    try:
        from inspection_reconcile.sqlexport import export_sqlite
    except ImportError as exc:  # pragma: no cover - present once step A6 is merged
        raise RunError("NOT_AVAILABLE", f"the SQLite export is not installed: {exc}") from exc
    from inspection_reconcile.io.snapshot import load_snapshot
    from inspection_reconcile.policy import load_policy

    out = Path(args.out)
    if out.exists() and not args.force:
        raise RunError("OUT_EXISTS", f"{out}: exists (use --force)")
    export_sqlite(load_snapshot(Path(args.snapshot)), load_policy(Path(args.policy)), out)
    print(f"SQLite export written to {out.as_posix()}")
    return EXIT_OK


def cmd_capture(args: argparse.Namespace) -> int:
    try:
        from inspection_reconcile.adapters.mapping import load_mapping
        from inspection_reconcile.adapters.qb_capture import capture, load_capture_config, make_client
    except ImportError as exc:  # pragma: no cover - the quickbase extra (httpx) is not installed
        raise RunError("NOT_AVAILABLE", f"the Quickbase connector is not installed: {exc}") from exc
    config = load_capture_config(Path(args.config))
    mapping = load_mapping(Path(args.mapping))
    out = Path(args.out)
    with replacing_out(out, args.force, CAPTURE_OUTPUTS, CAPTURE_DIRS) as target:
        client = make_client(config, user_agent=f"inspection-reconcile/{__version__}")  # QB_TOKEN_MISSING
        try:
            capture(client, config, mapping, target, now=lambda: datetime.now(UTC))
        finally:
            client.close()
    print(f"capture written to {out.as_posix()}")
    return EXIT_OK


# --------------------------------------------------------------------------------------------- parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="inspection-reconcile",
        description="Check inspection documentation readiness against an explicit requirement pack.",
    )
    parser.add_argument("--version", action="version", version=f"inspection-reconcile {__version__}")
    parser.add_argument("--log-json", action="store_true", help=LOG_JSON_HELP)
    # Every command also accepts --log-json after its name. Its default is SUPPRESS so that a command given
    # without the flag does not reset one given before it (argparse copies a subparser's defaults over).
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--log-json", action="store_true", default=argparse.SUPPRESS, help=LOG_JSON_HELP)
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    p = sub.add_parser("validate", parents=[common], help="check configuration and list record-level defects")
    p.add_argument("--snapshot", required=True)
    p.add_argument("--policy", required=True)
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("assess", parents=[common], help="assess a snapshot (or a Quickbase-shaped export)")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--snapshot")
    src.add_argument("--export")
    p.add_argument("--mapping")
    p.add_argument("--policy", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--as-of", dest="as_of")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_assess)

    p = sub.add_parser(
        "normalize", parents=[common], help="turn a Quickbase-shaped export into a canonical snapshot"
    )
    p.add_argument("--export", required=True)
    p.add_argument("--mapping", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_normalize)

    p = sub.add_parser("compare", parents=[common], help="compare two assessments by finding key")
    p.add_argument("--before", required=True)
    p.add_argument("--after", required=True)
    p.add_argument("--out")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_compare)

    p = sub.add_parser("demo", parents=[common], help="run the fixture scenarios against the oracle")
    which = p.add_mutually_exclusive_group(required=True)
    which.add_argument("--scenario")
    which.add_argument("--all", action="store_true")
    p.add_argument("--out", required=True)
    p.add_argument("--fixtures", default="fixtures/scenarios")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_demo)

    p = sub.add_parser(
        "evidence-digest", parents=[common], help="print the evidence-set digest a review should store"
    )
    p.add_argument("--snapshot", required=True)
    p.add_argument("--policy", required=True)
    p.add_argument("--inspection", required=True)
    p.add_argument("--revision")
    p.set_defaults(func=cmd_evidence_digest)

    p = sub.add_parser(
        "export-sqlite", parents=[common], help="export the snapshot's valid rows to SQLite (Appendix G)"
    )
    p.add_argument("--snapshot", required=True)
    p.add_argument("--policy", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_export_sqlite)

    p = sub.add_parser(
        "capture-quickbase", parents=[common], help="read-only capture from a Quickbase app (operator-gated)"
    )
    p.add_argument("--config", required=True)
    p.add_argument("--mapping", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_capture)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    global LOG_JSON
    _configure_console()
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # argparse usage errors exit 2; --help and --version exit 0
        return int(exc.code) if isinstance(exc.code, int) else EXIT_RUN_ERROR
    LOG_JSON = bool(args.log_json)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        JsonLogFormatter() if LOG_JSON else logging.Formatter("%(levelname)s: %(name)s: %(message)s")
    )
    logging.basicConfig(level=logging.WARNING, handlers=[handler])
    if not getattr(args, "func", None):
        parser.print_help()
        return EXIT_RUN_ERROR
    try:
        return int(args.func(args))
    except RunError as exc:
        diag("error", exc.code, exc.message)
        return EXIT_RUN_ERROR
    except Exception as exc:  # the top-level guard: never exit 1, never print secrets or a traceback
        diag("error", "INTERNAL_ERROR", f"{type(exc).__name__}: {exc}")
        if os.environ.get("INSPECTION_RECONCILE_DEBUG") == "1":
            import traceback

            traceback.print_exc()
        return EXIT_RUN_ERROR


if __name__ == "__main__":
    sys.exit(main())
