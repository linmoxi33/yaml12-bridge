"""End-to-end demonstration on real config files.

Run this to see the whole story in one screen: what PyYAML thinks each file
means, what a 1.2 parser thinks, and what the migration does about it.

    python examples/demo.py
"""

from __future__ import annotations

import pathlib
import sys

import yaml

from yaml12bridge import LoaderOptions, load
from yaml12bridge.dumper import dump
from yaml12bridge.migrate import migrate_text

SAMPLES = pathlib.Path(__file__).resolve().parent.parent / "samples"


def show(title: str) -> None:
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")


def demo_norway() -> None:
    show("1. The Norway problem, and three of its siblings")
    for scalar in ("NO", "on", "12:30", "0644", "3900e128"):
        source = f"k: {scalar}"
        legacy = yaml.safe_load(source)["k"]
        core = load(source)["k"]
        flag = "  <-- silently different" if legacy != core else ""
        print(f"  {source:18} PyYAML 1.1 -> {legacy!r:14} YAML 1.2 -> {core!r}{flag}")


def demo_keys() -> None:
    show("2. The CI trigger key: not a value, a KEY")
    source = "on:\n  push:\n    branches: [main]\n"
    print(f"  source:\n{source}")
    print(f"  PyYAML 1.1 -> {yaml.safe_load(source)}")
    print(f"  YAML 1.2   -> {load(source)}")


def demo_file(path: pathlib.Path) -> None:
    show(f"3. Real file: {path.name}")
    text = path.read_text(encoding="utf-8")
    before = yaml.safe_load(text)
    report = migrate_text(text, mode="value", path=path.name)
    after = load(report.migrated)

    print(report.summary())

    # The only structural difference left is the CI trigger key: PyYAML reads
    # `on:` as boolean True, and after migration it reads as the string "on" in
    # both schemas. That is the fix, so normalise it before claiming equality --
    # but say so, rather than silently massaging the comparison.
    def normalise(obj):
        if isinstance(obj, dict):
            out = {}
            for key, value in obj.items():
                if key is True:
                    key = "on"
                elif key is False:
                    key = "off"
                out[key] = normalise(value)
            return out
        if isinstance(obj, list):
            return [normalise(item) for item in obj]
        return obj

    same = normalise(before) == normalise(after)
    print(f"\n  values identical after migration: {same}")
    if "on" in normalise(after) and True in before:
        print(
            "    (the `on:` key moved from boolean True to the string 'on' --\n"
            "     that is the fix, and now both schemas agree on it)"
        )

    print("\n  --- diff (text mode, what a human would review) ---")
    text_report = migrate_text(text, path=path.name)
    import difflib

    for line in difflib.unified_diff(
        text_report.original.splitlines(True),
        text_report.migrated.splitlines(True),
        fromfile="before",
        tofile="after",
        n=0,
    ):
        sys.stdout.write("  " + line if line.strip() else line)


def demo_roundtrip() -> None:
    show("4. The write-side trap: PyYAML writes YAML it alone can read")
    data = {"githash": "3900e128", "version": "1.10", "country": "NO"}
    naive = yaml.safe_dump(data, default_flow_style=False)
    fixed = dump(data)
    print("  yaml.safe_dump (what most code does):")
    for line in naive.splitlines():
        print(f"    {line}")
    print("  read back by a YAML 1.2 parser:")
    for key, value in load(naive).items():
        print(f"    {key}: {value!r}   (was {data[key]!r})")
    print("\n  yaml12bridge.dump:")
    for line in fixed.splitlines():
        print(f"    {line}")
    print("  read back by a YAML 1.2 parser:")
    for key, value in load(fixed).items():
        print(f"    {key}: {value!r}   {'OK' if value == data[key] else 'WRONG'}")


def demo_options() -> None:
    show("5. Timestamps: the spec has no such type, PyYAML does")
    source = "d: 2001-12-14"
    print(f"  default (spec-literal): {load(source)['d']!r}")
    print(f"  timestamps=True:        {load(source, options=LoaderOptions(timestamps=True))['d']!r}")


def main() -> int:
    demo_norway()
    demo_keys()
    demo_file(SAMPLES / "github-actions.yml")
    demo_roundtrip()
    demo_options()
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
