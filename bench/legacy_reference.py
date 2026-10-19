#!/usr/bin/env python
"""Produce the reference 1.1 reading of each sample, using PyYAML.

Kept separate from the Node checker so the comparison is genuinely
cross-implementation: Python (PyYAML) decides what the file meant under 1.1,
Node decides what the migrated file means under 1.2. If the two agree, the
migration worked across an implementation boundary, not just inside one
library.
"""

from __future__ import annotations

import json
import pathlib
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "samples"


def flatten(obj: object, prefix: str = "", acc: dict | None = None) -> dict:
    if acc is None:
        acc = {}
    if not isinstance(obj, (dict, list)):
        acc[prefix] = obj
        return acc
    if isinstance(obj, list):
        for index, item in enumerate(obj):
            flatten(item, f"{prefix}[{index}]", acc)
        return acc
    for key, value in obj.items():
        # Keys are stringified: PyYAML turns `on:` into True, and the whole
        # point is to capture that as data, not to hide it.
        child = f"{prefix}.{key}" if prefix else str(key)
        flatten(value, child, acc)
    return acc


def main() -> int:
    out: dict[str, dict] = {}
    for path in sorted(SAMPLES.glob("*.y*ml")):
        text = path.read_text(encoding="utf-8")
        try:
            out[path.name] = flatten(yaml.safe_load(text))
        except yaml.YAMLError as exc:
            out[path.name] = {"<error>": str(exc).splitlines()[0]}
    json.dump(out, sys.stdout, ensure_ascii=False, indent=1, default=str)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
