"""Command line interface: ``yaml12`` / ``python -m yaml12bridge``.

Subcommands
-----------
``check``    parse files with the 1.2 schema and report problems
``diff``     show what each file *means* under 1.1 vs 1.2, without writing
``migrate``  rewrite files so a 1.2 parser preserves the 1.1 meaning
``explain``  explain what one scalar means under each schema
``roundtrip`` prove that a file survives load -> dump -> load

Every command exits non-zero on a real problem, so this can gate CI.
"""

from __future__ import annotations

import argparse
import difflib
import json
import sys
from pathlib import Path
from typing import Iterable, Sequence

import yaml

from . import __version__, dumper, loader, migrate, schema


def _expand(paths: Iterable[str]) -> list[Path]:
    """Accept files, directories and globs; deduplicate; keep input order."""
    import glob as globlib

    seen: dict[Path, None] = {}
    for raw in paths:
        candidate = Path(raw)
        if candidate.is_dir():
            for child in sorted(candidate.rglob("*")):
                if child.suffix in (".yml", ".yaml") and child.is_file():
                    seen.setdefault(child, None)
        elif candidate.is_file():
            seen.setdefault(candidate, None)
        else:
            # Path.glob() rejects absolute patterns; glob.glob() handles both
            # absolute and relative ones, which is what a CLI user will type.
            matches = sorted(globlib.glob(raw, recursive=True))
            if not matches:
                print(f"yaml12: no such file: {raw}", file=sys.stderr)
            for match in matches:
                path = Path(match)
                if path.is_file() and path.suffix in (".yml", ".yaml"):
                    seen.setdefault(path, None)
    return list(seen)


def _read(path: Path) -> str:
    # newline="" so CRLF survives a read/write cycle untouched.
    with open(path, "r", encoding="utf-8", newline="") as handle:
        return handle.read()


def cmd_check(args: argparse.Namespace) -> int:
    paths = _expand(args.files)
    if not paths:
        print("yaml12: nothing to check", file=sys.stderr)
        return 2
    options = loader.LoaderOptions(
        strict_duplicates=not args.allow_duplicates,
        timestamps=args.timestamps,
        version=args.schema,
    )
    failures = 0
    for path in paths:
        try:
            text = _read(path)
            loader.load(text, options=options)
        except yaml.YAMLError as exc:
            failures += 1
            print(f"{path}: FAIL")
            for line in str(exc).rstrip().splitlines():
                print(f"  {line}")
            continue
        except OSError as exc:
            failures += 1
            print(f"{path}: FAIL ({exc})")
            continue
        print(f"{path}: ok")
    if failures:
        print(f"\n{failures} of {len(paths)} file(s) failed", file=sys.stderr)
        return 1
    return 0


def _values_under(text: str, timestamps: bool) -> tuple[object, object]:
    legacy = yaml.safe_load(text)
    core = loader.load(
        text, options=loader.LoaderOptions(timestamps=timestamps, strict_duplicates=False)
    )
    return legacy, core


def _walk_differences(
    a: object, b: object, path: str = "", out: list[tuple[str, object, object]] | None = None
) -> list[tuple[str, object, object]]:
    if out is None:
        out = []
    if type(a) is not type(b):
        out.append((path or "<root>", a, b))
        return out
    if isinstance(a, dict) and isinstance(b, dict):
        for key in dict.fromkeys(list(a) + list(b)):
            child = f"{path}.{key}" if path else str(key)
            if key not in a:
                out.append((child, "<absent>", b[key]))
            elif key not in b:
                out.append((child, a[key], "<absent>"))
            else:
                _walk_differences(a[key], b[key], child, out)
        return out
    if isinstance(a, list) and isinstance(b, list):
        for index in range(max(len(a), len(b))):
            child = f"{path}[{index}]"
            if index >= len(a):
                out.append((child, "<absent>", b[index]))
            elif index >= len(b):
                out.append((child, a[index], "<absent>"))
            else:
                _walk_differences(a[index], b[index], child, out)
        return out
    if a != b:
        out.append((path or "<root>", a, b))
    return out


def cmd_diff(args: argparse.Namespace) -> int:
    paths = _expand(args.files)
    if not paths:
        print("yaml12: nothing to diff", file=sys.stderr)
        return 2
    differences = 0
    for path in paths:
        try:
            legacy, core = _values_under(_read(path), args.timestamps)
        except yaml.YAMLError as exc:
            print(f"{path}: cannot parse -- {str(exc).splitlines()[0]}", file=sys.stderr)
            differences += 1
            continue
        found = _walk_differences(legacy, core)
        if not found:
            if args.verbose:
                print(f"{path}: identical under 1.1 and 1.2")
            continue
        differences += 1
        print(f"{path}: {len(found)} value(s) differ between 1.1 and 1.2")
        for where, before, after in found:
            print(f"  {where}\n      1.1: {before!r}\n      1.2: {after!r}")
    if differences:
        print(
            f"\n{differences} file(s) would change meaning. "
            f"Run `yaml12 migrate` to pin the 1.1 meaning with quotes.",
            file=sys.stderr,
        )
        return 1
    return 0


def cmd_migrate(args: argparse.Namespace) -> int:
    paths = _expand(args.files)
    if not paths:
        print("yaml12: nothing to migrate", file=sys.stderr)
        return 2
    total_changes = 0
    failures = 0
    for path in paths:
        try:
            report = migrate.migrate_file(
                str(path),
                destination=args.output,
                direction=args.direction,
                mode=args.mode,
                timestamps=args.timestamps,
                in_place=args.in_place,
            )
        except yaml.YAMLError as exc:
            failures += 1
            print(f"{path}: FAIL -- {str(exc).splitlines()[0]}", file=sys.stderr)
            continue
        except OSError as exc:
            failures += 1
            print(f"{path}: FAIL -- {exc}", file=sys.stderr)
            continue

        total_changes += len(report.changes)
        if args.quiet and not report.changed:
            continue
        print(report.summary())
        if args.show_diff and report.changed:
            diff = difflib.unified_diff(
                report.original.splitlines(True),
                report.migrated.splitlines(True),
                fromfile=f"a/{path.name}",
                tofile=f"b/{path.name}",
                n=args.context,
            )
            sys.stdout.writelines(diff)
        if report.changed and not args.in_place and not args.output and not args.check:
            print("  (dry run -- nothing written; pass --in-place or --output to apply)")

    if failures:
        return 1
    if args.check and total_changes:
        # In --check mode a needed migration is a failure, for CI use.
        return 1
    return 0


def cmd_explain(args: argparse.Namespace) -> int:
    value = args.scalar
    legacy = schema.classify_legacy(value)
    core = schema.classify_core(value, timestamps=args.timestamps)
    print(f"scalar: {value!r}")
    print(f"  YAML 1.1 (PyYAML today): {legacy or 'tag:yaml.org,2002:str'}")
    print(f"  YAML 1.2 (this package): {core or 'tag:yaml.org,2002:str'}")
    if legacy != core:
        print("  -> schemas disagree; quote it if you need one fixed meaning")
    else:
        print("  -> both schemas agree")
    if args.json:
        print(
            json.dumps(
                {"scalar": value, "legacy_tag": legacy, "core_tag": core}, ensure_ascii=False
            )
        )
    return 0


def cmd_roundtrip(args: argparse.Namespace) -> int:
    paths = _expand(args.files)
    if not paths:
        print("yaml12: nothing to round-trip", file=sys.stderr)
        return 2
    failures = 0
    for path in paths:
        text = _read(path)
        try:
            first = loader.load(text, options=loader.LoaderOptions(timestamps=args.timestamps))
            emitted = dumper.dump(first)
            second = loader.load(
                emitted, options=loader.LoaderOptions(timestamps=args.timestamps)
            )
        except yaml.YAMLError as exc:
            failures += 1
            print(f"{path}: FAIL -- {str(exc).splitlines()[0]}", file=sys.stderr)
            continue
        if first != second:
            failures += 1
            print(f"{path}: FAIL -- value changed through dump/load")
            for where, before, after in _walk_differences(first, second):
                print(f"  {where}: {before!r} -> {after!r}")
            continue
        print(f"{path}: ok ({len(emitted)} bytes emitted)")
    return 1 if failures else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="yaml12",
        description="Read, audit and migrate YAML under the 1.2 Core Schema.",
    )
    parser.add_argument("--version", action="version", version=f"yaml12-bridge {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("files", nargs="+", help="files, directories or globs")
        p.add_argument(
            "--timestamps",
            action="store_true",
            help="treat ISO dates as timestamps (PyYAML-compatible)",
        )

    p_check = sub.add_parser("check", help="parse with the 1.2 schema")
    common(p_check)
    p_check.add_argument(
        "--allow-duplicates", action="store_true", help="do not fail on duplicate keys"
    )
    p_check.add_argument(
        "--schema",
        default="1.2",
        choices=("1.1", "1.2", "auto"),
        help="schema to parse with (default 1.2)",
    )
    p_check.set_defaults(func=cmd_check)

    p_diff = sub.add_parser("diff", help="compare 1.1 and 1.2 readings")
    common(p_diff)
    p_diff.add_argument("-v", "--verbose", action="store_true")
    p_diff.set_defaults(func=cmd_diff)

    p_mig = sub.add_parser("migrate", help="quote scalars to pin one meaning")
    common(p_mig)
    p_mig.add_argument(
        "--direction",
        default="to_1_2",
        choices=("to_1_2", "to_1_1"),
        help="to_1_2 keeps the 1.1 meaning for 1.2 readers (default); "
        "to_1_1 keeps the 1.1 string meaning",
    )
    p_mig.add_argument(
        "--mode",
        default="text",
        choices=("text", "value"),
        help="text: quote the scalar so its characters survive (default). "
        "value: rewrite to the 1.2 spelling so its parsed VALUE survives",
    )
    p_mig.add_argument("--in-place", action="store_true", help="rewrite the input file")
    p_mig.add_argument("--output", help="write to this path (single input only)")
    p_mig.add_argument("--show-diff", action="store_true", help="print a unified diff")
    p_mig.add_argument("--context", type=int, default=3)
    p_mig.add_argument(
        "--check",
        action="store_true",
        help="exit 1 if any file needs migration (CI gate; writes nothing)",
    )
    p_mig.add_argument("-q", "--quiet", action="store_true", help="only report changes")
    p_mig.set_defaults(func=cmd_migrate)

    p_exp = sub.add_parser("explain", help="explain one scalar")
    p_exp.add_argument("scalar")
    p_exp.add_argument("--timestamps", action="store_true")
    p_exp.add_argument("--json", action="store_true")
    p_exp.set_defaults(func=cmd_explain)

    p_rt = sub.add_parser("roundtrip", help="prove load -> dump -> load is lossless")
    common(p_rt)
    p_rt.set_defaults(func=cmd_roundtrip)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
