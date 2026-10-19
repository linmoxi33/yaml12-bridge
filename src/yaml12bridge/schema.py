"""YAML 1.2 Core Schema resolution rules, derived from the spec text.

This module is the single source of truth for "what does YAML 1.2 Core Schema
say this plain scalar means".  Everything else in the package (the loader, the
dumper, the migration CLI) reads its verdicts from here.

Design note -- why not copy PyYAML's resolver table?
----------------------------------------------------
PyYAML's table is the YAML 1.1 table.  Reusing it and patching a few entries is
how most "yaml 1.2 mode" hacks die: they fix `on`/`off` and leave `12:30`,
`0644` and `1e3` broken.  We instead build the table from the 1.2 Core Schema
regexps and keep the 1.1 table only for *detecting* divergence, never for
producing values.

Spec references (YAML 1.2.2, chapter 10 "Recommended Schemas"):
  null     : `null | Null | NULL | ~` and the empty scalar
  bool     : `true | True | TRUE | false | False | FALSE`
  int      : base 10 `[-+]?[0-9]+`, base 8 `0o[0-7]+`, base 16 `0x[0-9a-fA-F]+`
  float    : `[-+]? ( . [0-9]+ | [0-9]+ ( . [0-9]* )? ) ( [eE] [-+]? [0-9]+ )?`
             plus `.inf`, `-.inf`, `.nan` (case-insensitive on the word part)

Resolution order is null -> bool -> int -> float.  The order is load-bearing:
`0644` matches both the int and the float pattern, and must stay an int.
"""

from __future__ import annotations

import re
from typing import NamedTuple

__all__ = [
    "Tag",
    "Rule",
    "CORE_RULES",
    "NULL_TAG",
    "BOOL_TAG",
    "INT_TAG",
    "FLOAT_TAG",
    "STR_TAG",
    "TIMESTAMP_TAG",
    "MERGE_TAG",
    "classify_core",
    "classify_legacy",
    "looks_like_core_type",
    "is_merge_key",
]

# --- tags -------------------------------------------------------------------
STR_TAG = "tag:yaml.org,2002:str"
NULL_TAG = "tag:yaml.org,2002:null"
BOOL_TAG = "tag:yaml.org,2002:bool"
INT_TAG = "tag:yaml.org,2002:int"
FLOAT_TAG = "tag:yaml.org,2002:float"
TIMESTAMP_TAG = "tag:yaml.org,2002:timestamp"
MERGE_TAG = "tag:yaml.org,2002:merge"


class Tag:
    """String constants, kept as a namespace for readability at call sites."""

    STR = STR_TAG
    NULL = NULL_TAG
    BOOL = BOOL_TAG
    INT = INT_TAG
    FLOAT = FLOAT_TAG
    TIMESTAMP = TIMESTAMP_TAG
    MERGE = MERGE_TAG


class Rule(NamedTuple):
    """A single implicit-resolution rule.

    Attributes
    ----------
    tag:
        The tag this rule assigns.
    pattern:
        Compiled regexp, always fully anchored with ``fullmatch`` semantics.
    first_chars:
        Characters this rule can possibly start with.  ``None`` means "any"
        (wildcard).  Used to bucket rules the way PyYAML does, so lookups stay
        O(number of rules for one character).
    note:
        Human explanation shown by the ``explain`` CLI command.
    """

    tag: str
    pattern: re.Pattern[str]
    first_chars: tuple[str, ...] | None
    note: str


# --- 1.2 Core Schema table --------------------------------------------------
# Order matters: the first match wins.  null/bool/float/int do not overlap by
# construction, but keeping a fixed order makes the table auditable.

CORE_RULES: tuple[Rule, ...] = (
    Rule(
        MERGE_TAG,
        # `<<` is resolved implicitly in PyYAML (bucket '<'), not by a path
        # resolver as its name suggests.  Replacing the resolver table without
        # carrying this entry silently turns merges into literal keys.
        re.compile(r"(?:<<)"),
        ("<",),
        "merge key '<<' (structural, not a scalar type)",
    ),
    Rule(
        NULL_TAG,
        # The empty alternative is required: PyYAML routes an empty scalar
        # through the "" resolver bucket, and `\A(...)\Z` with no empty branch
        # never matches it, so `k:` would come back as '' instead of None.
        re.compile(r"(?:~|null|Null|NULL|)"),
        ("~", "n", "N", ""),
        "YAML 1.2 null: '~', 'null' (any of three casings), or the empty scalar",
    ),
    Rule(
        BOOL_TAG,
        re.compile(r"(?:true|True|TRUE|false|False|FALSE)"),
        ("t", "T", "f", "F"),
        "YAML 1.2 bool: only true/True/TRUE/false/False/FALSE. "
        "'yes'/'no'/'on'/'off'/'y'/'n' are strings in 1.2",
    ),
    # int MUST precede float: the spec resolves in the order
    # null -> bool -> int -> float, and the two regexps overlap on inputs like
    # "0644" and "007" (both are valid 1.2 floats and valid 1.2 ints).
    # Getting this order wrong silently turns file modes into floats.
    Rule(
        INT_TAG,
        re.compile(
            r"(?:[-+]?[0-9]+"
            r"|0o[0-7]+"
            r"|[-+]?0x[0-9a-fA-F]+)"
        ),
        ("-", "+", "0", "1", "2", "3", "4", "5", "6", "7", "8", "9"),
        "YAML 1.2 int: base 10, '0o' octal, '0x' hex. A leading-zero decimal "
        "like 0644 is base-10 644, NOT octal 420",
    ),
    Rule(
        FLOAT_TAG,
        re.compile(
            r"(?:[-+]?(?:\.[0-9]+|[0-9]+(?:\.[0-9]*)?)(?:[eE][-+]?[0-9]+)?"
            r"|[-+]?\.(?:inf|Inf|INF)"
            r"|\.(?:nan|NaN|NAN))"
        ),
        ("-", "+", ".", "0", "1", "2", "3", "4", "5", "6", "7", "8", "9"),
        "YAML 1.2 float: JSON-style numbers (dot in mantissa optional, "
        "exponent sign optional), plus .inf/.nan. No '1_000' underscores",
    ),
)

# 1.2 Core Schema does not define a timestamp type.  Implementations differ:
# ruamel and PyYAML construct dates; js-yaml and the `yaml` npm package keep
# them as strings.  We follow the "keep as string" reading because the Core
# Schema table simply has no timestamp entry, and we expose the choice so users
# can flip it.  See docs/CONFORMANCE.md for the three-way comparison.
CORE_TIMESTAMP_RULE = Rule(
    TIMESTAMP_TAG,
    re.compile(
        r"(?:[0-9]{4}-[0-9]{2}-[0-9]{2}"
        r"|[0-9]{4}-[0-9]{1,2}-[0-9]{1,2}"
        r"(?:[Tt]|[ \t]+)[0-9]{1,2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]*)?"
        r"(?:[ \t]*(?:Z|[-+][0-9]{1,2}(?::[0-9]{2})?))?)"
    ),
    ("0", "1", "2", "3", "4", "5", "6", "7", "8", "9"),
    "timestamp: NOT part of the 1.2 Core Schema. Off by default; enable with "
    "timestamps=True for PyYAML-compatible behaviour",
)

# --- 1.1 table, used only to *detect* what legacy code would have produced ---
# These regexps mirror PyYAML's own resolver so that "what would PyYAML do"
# stays answerable without importing PyYAML into the hot path.

_LEGACY_RULES: tuple[Rule, ...] = (
    Rule(
        NULL_TAG,
        re.compile(r"(?:~|null|Null|NULL|)"),
        ("~", "n", "N", ""),
        "1.1 null",
    ),
    Rule(
        BOOL_TAG,
        re.compile(
            r"(?:yes|Yes|YES|no|No|NO|true|True|TRUE|false|False|FALSE"
            r"|on|On|ON|off|Off|OFF)"
        ),
        ("y", "Y", "n", "N", "t", "T", "f", "F", "o", "O"),
        "1.1 bool includes yes/no/on/off",
    ),
    # NOTE: the YAML 1.1 *spec* also lists single-letter y/Y/n/N as booleans, and
    # older PyYAML honoured that. PyYAML 6.0.3 does not: `safe_load("k: y")`
    # returns the string 'y'. Since PyYAML is our 1.1 oracle (it is what the
    # affected projects actually run), the table follows the implementation, not
    # the spec. Verified against 6.0.3 in tests/test_schema.py.
    Rule(
        FLOAT_TAG,
        re.compile(
            r"(?:[-+]?(?:[0-9][0-9_]*)\.[0-9_]*(?:[eE][-+][0-9]+)?"
            r"|\.[0-9][0-9_]*(?:[eE][-+][0-9]+)?"
            r"|[-+]?\.(?:inf|Inf|INF)"
            r"|\.(?:nan|NaN|NAN))"
        ),
        ("-", "+", ".", "0", "1", "2", "3", "4", "5", "6", "7", "8", "9"),
        "1.1 float requires a dot in the mantissa and a sign in the exponent",
    ),
    Rule(
        INT_TAG,
        re.compile(
            r"(?:[-+]?0b[0-1_]+"
            r"|[-+]?0[0-7_]+"
            r"|[-+]?(?:0|[1-9][0-9_]*)"
            r"|[-+]?0x[0-9a-fA-F_]+"
            r"|[-+]?[1-9][0-9_]*(?::[0-5]?[0-9])+)"
        ),
        ("-", "+", "0", "1", "2", "3", "4", "5", "6", "7", "8", "9"),
        "1.1 int includes 0b binary, leading-zero octal, and sexagesimal (12:30)",
    ),
    Rule(
        TIMESTAMP_TAG,
        re.compile(
            r"(?:[0-9]{4}-[0-9]{2}-[0-9]{2}"
            r"|[0-9]{4}-[0-9]{1,2}-[0-9]{1,2}"
            r"(?:[Tt]|[ \t]+)[0-9]{1,2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]*)?"
            r"(?:[ \t]*(?:Z|[-+][0-9]{1,2}(?::[0-9]{2})?))?)"
        ),
        ("0", "1", "2", "3", "4", "5", "6", "7", "8", "9"),
        "1.1 timestamp",
    ),
)


def _bucket(rules: tuple[Rule, ...]) -> dict[str, list[Rule]]:
    table: dict[str, list[Rule]] = {}
    for rule in rules:
        chars = rule.first_chars
        if chars is None:
            table.setdefault("", []).append(rule)
            continue
        for ch in chars:
            table.setdefault(ch, []).append(rule)
    return table


_CORE_TABLE = _bucket(CORE_RULES)
_LEGACY_TABLE = _bucket(_LEGACY_RULES)


def _classify(value: str, table: dict[str, list[Rule]]) -> str | None:
    """Return the tag the table assigns to ``value``, or None if it stays str."""
    if value == "":
        candidates = table.get("", [])
    else:
        candidates = table.get(value[0], [])
    for rule in candidates:
        if rule.pattern.fullmatch(value):
            return rule.tag
    return None


def classify_core(value: str, *, timestamps: bool = False) -> str | None:
    """Tag a plain scalar under the YAML 1.2 Core Schema.

    Returns the resolved tag, or ``None`` when the scalar is a plain string.
    An empty scalar resolves to null, as the spec requires.
    """
    if value == "":
        return NULL_TAG
    hit = _classify(value, _CORE_TABLE)
    if hit is None and timestamps and CORE_TIMESTAMP_RULE.pattern.fullmatch(value):
        return TIMESTAMP_TAG
    return hit


def classify_legacy(value: str) -> str | None:
    """Tag a plain scalar the way PyYAML (YAML 1.1) would.

    Only used for reporting divergences; never used to produce output values.
    """
    return _classify(value, _LEGACY_TABLE)


def looks_like_core_type(value: str, *, timestamps: bool = True) -> bool:
    """True when ``value`` would be read as a non-string under 1.2.

    ``timestamps`` defaults to True here: for *dumping* safety we care whether
    any mainstream 1.2 parser might retype the scalar, and two of the three we
    tested do construct dates.  Quoting a date string is always safe.
    """
    return classify_core(value, timestamps=timestamps) is not None


def is_merge_key(value: str) -> bool:
    """``<<`` is the merge key in 1.1 and remains special-cased by parsers.

    It must never be quoted: quoting turns a merge into a literal string key.
    """
    return value == "<<"
