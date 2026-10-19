"""A YAML 1.2 dumper that quotes strings a 1.2 parser would otherwise retype.

The write-side gap
------------------
PyYAML's dumper decides whether a string needs quotes by asking *its own*
resolver -- the 1.1 one.  So the string ``"3900e128"`` looks like a plain
string to PyYAML, is emitted bare, and then a YAML 1.2 parser reads it as the
float ``3.9e+131``.  Round-trip through PyYAML alone never reveals this,
because the same 1.1 resolver reads it back as a string; the corruption only
appears once the file crosses into another implementation (kubectl, Node,
Go, Rust).  Issue #173 on yaml/pyyaml is exactly this accident.

The fix is to ask the *1.2* question when deciding to quote: "would any
mainstream 1.2 parser read this scalar as something other than a string?"
If yes, quote it.

What we deliberately do not touch
---------------------------------
* ``<<`` is the merge key. Quoting it would silently turn a merge into a
  literal ``"<<"`` key, so it is always emitted bare.
* Explicitly tagged scalars keep their tag; the user already said what they
  mean.
* Non-ASCII text, block scalars and multi-line strings keep PyYAML's
  behaviour -- they are never ambiguous in 1.2.
"""

from __future__ import annotations

import re
from typing import Any

import yaml

from . import schema

__all__ = ["YAML12Dumper", "dump", "dump_all", "DumpOptions", "needs_quotes"]

# Scalars that are strings in 1.2 but would be retyped by a 1.2 parser.  This
# is computed rather than hardcoded so that the dumper and the loader can never
# disagree about the schema.
_PLAIN_SAFE = re.compile(r"^[A-Za-z_][A-Za-z0-9_\-./ ]*$")


def needs_quotes(value: str, *, timestamps: bool = True) -> bool:
    """True when ``value`` must be quoted to survive a 1.2 parser as a string.

    The question is "would *any* parser retype this?", not "would a 1.2 parser
    retype this?". ``NO``/``on``/``12:30`` are strings to a 1.2 reader but
    booleans and integers to a 1.1 one, so a file containing them bare is
    ambiguous across the ecosystem. Quoting is cheap and removes the question.

    ``timestamps=True`` because ruamel and PyYAML both construct dates from
    plain scalars.
    """
    if value == "":
        return True  # an empty plain scalar is null in every schema
    if schema.is_merge_key(value):
        return False  # must stay bare; quoting breaks merge semantics
    # Retyped by a 1.2 reader?
    if schema.looks_like_core_type(value, timestamps=timestamps):
        return True
    # Retyped by a 1.1 reader? (NO, on, 12:30, 0644, 1_000...)
    return schema.classify_legacy(value) is not None


class DumpOptions:
    """Formatting knobs; defaults match PyYAML's so diffs stay small."""

    __slots__ = ("timestamps", "indent", "sort_keys", "allow_unicode", "explicit_start")

    def __init__(
        self,
        *,
        timestamps: bool = True,
        indent: int = 2,
        sort_keys: bool = False,
        allow_unicode: bool = True,
        explicit_start: bool = False,
    ) -> None:
        self.timestamps = timestamps
        self.indent = indent
        self.sort_keys = sort_keys
        self.allow_unicode = allow_unicode
        self.explicit_start = explicit_start


class YAML12Dumper(yaml.SafeDumper):
    """SafeDumper with 1.2-aware quoting for ``str``."""


def _represent_str(dumper: yaml.SafeDumper, data: str) -> yaml.ScalarNode:
    if needs_quotes(data, timestamps=getattr(dumper, "_yaml12_timestamps", True)):
        return dumper.represent_scalar(schema.STR_TAG, data, style="'")
    return dumper.represent_scalar(schema.STR_TAG, data)


YAML12Dumper.add_representer(str, _represent_str)


def dump(
    data: Any,
    *,
    options: DumpOptions | None = None,
    **kwargs: Any,
) -> str:
    """Serialise ``data`` so that a YAML 1.2 parser reads it back unchanged."""
    opts = options or DumpOptions()
    dumper = type("_ConfiguredYAML12Dumper", (YAML12Dumper,), {})
    dumper._yaml12_timestamps = opts.timestamps  # type: ignore[attr-defined]
    merged = {
        "indent": opts.indent,
        "sort_keys": opts.sort_keys,
        "allow_unicode": opts.allow_unicode,
        "explicit_start": opts.explicit_start,
        "default_flow_style": False,
    }
    merged.update(kwargs)
    return yaml.dump(data, Dumper=dumper, **merged)


def dump_all(
    documents: list[Any],
    *,
    options: DumpOptions | None = None,
    **kwargs: Any,
) -> str:
    """Serialise a multi-document stream."""
    opts = options or DumpOptions()
    dumper = type("_ConfiguredYAML12DumperAll", (YAML12Dumper,), {})
    dumper._yaml12_timestamps = opts.timestamps  # type: ignore[attr-defined]
    merged = {
        "indent": opts.indent,
        "sort_keys": opts.sort_keys,
        "allow_unicode": opts.allow_unicode,
        "explicit_start": True,
        "default_flow_style": False,
    }
    merged.update(kwargs)
    return yaml.dump_all(documents, Dumper=dumper, **merged)
