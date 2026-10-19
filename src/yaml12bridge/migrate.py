"""Migrate YAML files across the 1.1 / 1.2 schema boundary.

The problem in one sentence
---------------------------
``country: NO`` is the string "NO" to whoever wrote it, but PyYAML reads it as
``False``. ``at: 12:30`` is a time to a human and the integer 750 to PyYAML.
``x: 1e3`` is a string to PyYAML and the float 1000.0 to every 1.2 parser.
Change the reader and values move, silently, with no error anywhere.

There is no single "correct" rewrite
------------------------------------
The same text can be pinned two ways, and they are not interchangeable:

``country: NO``
    *keep text*   -> ``country: 'NO'``     the literal characters survive
    *keep value*  -> ``country: false``    the boolean False survives

Only the author knows which one they meant. A country code wants the text; a
feature flag wants the boolean. So this module implements both and makes the
caller choose:

``mode="text"`` (default)
    Quote the scalar. The file now says exactly what it always said, byte for
    byte, and every parser agrees on it. Safe, but a boolean that was
    implicitly false stays false only if you quote... the *string* "off", which
    is not false. This mode is for files where the text is the payload.

``mode="value"``
    Rewrite the scalar into its 1.2 spelling with the same *value*:
    ``off`` -> ``false``, ``12:30`` -> ``750``, ``0644`` -> ``644``.
    Use this when the consumer actually wanted the number or the boolean.

Migration has to preserve intent, not just syntax
-------------------------------------------------
Re-serialising the parsed tree would "fix" the file and destroy it: comments
gone, anchors renumbered, key order changed, block scalars reflowed, quoting
normalised. For a Kubernetes manifest or a CI config that is an unreviewable
diff.

So we do not re-serialise. We compose the document, decide per scalar what it
should become, and splice the replacement into the original text at exactly the
byte offsets PyYAML reports. Everything else -- comments, blank lines,
indentation, anchors, aliases, flow style, block scalars -- is untouched by
construction, because it is never copied.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Literal

import yaml

from . import schema

__all__ = [
    "Change",
    "MigrationReport",
    "plan",
    "migrate_text",
    "migrate_file",
    "Direction",
    "Mode",
]

Direction = Literal["to_1_2", "to_1_1"]
Mode = Literal["text", "value"]


@dataclass(frozen=True)
class Change:
    """One scalar that will be (or was) quoted.

    ``legacy_value`` / ``core_value`` are the *Python values* each schema
    produces, rendered compactly.  They are the point of the report: a table of
    ``bool -> str`` says nothing useful, while ``False -> 'NO'`` shows the bug
    the migration is preventing.

    ``ambiguous_numeric`` marks the cases where both schemas read a number but
    disagree on which number (``0644`` is octal 420 in 1.1, decimal 644 in
    1.2).  Quoting makes the file mean the same thing everywhere -- the literal
    text -- but if the author really meant the *number*, they should look at
    these lines by hand.
    """

    line: int
    column: int
    path: str
    value: str
    legacy_tag: str
    core_tag: str
    legacy_value: str
    core_value: str
    ambiguous_numeric: bool
    action: str
    replacement: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "line": self.line,
            "column": self.column,
            "path": self.path,
            "value": self.value,
            "legacy_tag": self.legacy_tag,
            "core_tag": self.core_tag,
            "legacy_value": self.legacy_value,
            "core_value": self.core_value,
            "ambiguous_numeric": self.ambiguous_numeric,
            "action": self.action,
            "replacement": self.replacement,
        }


@dataclass
class MigrationReport:
    """What happened, in a shape that is easy to print or assert on."""

    path: str
    direction: Direction
    mode: Mode = "text"
    changes: list[Change] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    original: str = ""
    migrated: str = ""

    @property
    def changed(self) -> bool:
        return bool(self.changes)

    def summary(self) -> str:
        if not self.changes:
            return f"{self.path}: no schema-sensitive scalars ({self.direction}, {self.mode})"
        head = (
            f"{self.path}: {len(self.changes)} scalar(s) rewritten "
            f"({self.direction}, {self.mode})"
        )
        lines = [head]
        for change in self.changes:
            mark = "  [check: numeric re-read]" if change.ambiguous_numeric else ""
            lines.append(
                f"  line {change.line}:{change.column}  {change.path}\n"
                f"      {change.legacy_value} -> {change.replacement}   "
                f"(text {change.value!r}){mark}"
            )
        if self.mode == "text":
            lines.append(
                "  mode=text: quotes pin the literal characters. A scalar that "
                "1.1 read as false/number keeps its TEXT, not its value."
            )
        else:
            lines.append(
                "  mode=value: scalars are rewritten to their 1.2 spelling so the "
                "parsed VALUE is unchanged. Review numeric rewrites by hand."
            )
        if self.skipped:
            lines.append(f"  skipped {len(self.skipped)} scalar(s):")
            lines.extend(f"    {item}" for item in self.skipped)
        return "\n".join(lines)


def _tag_name(tag: str | None) -> str:
    if not tag:
        return "str"
    return tag.rsplit(":", 1)[-1]


def _render_value(tag: str, raw: str, *, schema_name: str) -> str:
    """Render the Python value a schema produces, compactly, for reports.

    Kept dependency-free on purpose: the report is what users read to decide
    whether to run the migration, so it must not be able to raise.
    """
    if tag == "str":
        return f"'{raw}'"
    if tag == "null":
        return "None"
    if tag == "bool":
        return "True" if raw.lower() in ("true", "yes", "on", "y") else "False"
    if tag == "int":
        text = raw.replace("_", "")
        if schema_name == "core" and len(text.lstrip("+-")) > 1 and text.lstrip("+-").startswith("0") and not text.lstrip("+-").startswith(("0o","0O","0x","0X")):
            # 1.2: a leading zero is still base 10, so 0644 is six hundred
            # forty-four, not octal 420.
            return repr(int(text, 10))
        try:
            sign = -1 if text.startswith("-") else 1
            body = text.lstrip("+-")
            if body.startswith(("0o", "0O")):
                return repr(sign * int(body[2:], 8))
            if body.startswith(("0x", "0X")):
                return repr(sign * int(body[2:], 16))
            if body.startswith(("0b", "0B")):
                return repr(sign * int(body[2:], 2))
            if ":" in body:  # 1.1 sexagesimal: 12:30 -> 750
                total = 0
                for part in body.split(":"):
                    total = total * 60 + int(part)
                return repr(sign * total)
            if len(body) > 1 and body.startswith("0"):
                return repr(sign * int(body, 8))  # 1.1 leading-zero octal
            return repr(sign * int(body, 10))
        except ValueError:
            return raw
    if tag == "float":
        try:
            return repr(float(raw.replace("_", "").lower()))
        except ValueError:
            return raw
    return raw


def _iter_scalars(node: Any, path: str, out: list[tuple[Any, str]]) -> None:
    """Walk a composed node tree, recording every scalar with a JSON-ish path.

    Keys are included, not just values. A key like ``on:`` is the single most
    damaging 1.1/1.2 difference in the wild -- PyYAML reads it as the boolean
    ``True``, so ``workflow[True]`` is where the CI config ends up -- and it is a
    *key*, so a value-only walk would never see it.
    """
    if isinstance(node, yaml.ScalarNode):
        out.append((node, path))
        return
    if isinstance(node, yaml.MappingNode):
        for key_node, value_node in node.value:
            key = key_node.value if isinstance(key_node, yaml.ScalarNode) else "?"
            child = f"{path}.{key}" if path else str(key)
            # Record the key node itself, then recurse into the value.
            if isinstance(key_node, yaml.ScalarNode):
                out.append((key_node, f"{child} (key)"))
            _iter_scalars(value_node, child, out)
        return
    if isinstance(node, yaml.SequenceNode):
        for index, item in enumerate(node.value):
            _iter_scalars(item, f"{path}[{index}]", out)


def _is_quoted_style(style: str | None) -> bool:
    return style in ("'", '"', "|", ">", "|+", "|-")


def _replacement_for(tag: str, raw: str, mode: Mode, core_tag: str) -> str:
    """Compute the new scalar text for one hit.

    ``mode="text"``  -> wrap in single quotes; the literal characters survive.
    ``mode="value"`` -> emit the 1.2 spelling of the same value.
    """
    if mode == "text":
        return f"'{raw}'"

    # mode == "value"
    if tag == "bool":
        return "true" if raw.lower() in ("true", "yes", "on", "y") else "false"
    if tag == "str":
        # 1.1 read it as a string but 1.2 would retype it (1e3, 3900e128,
        # 0o14...). The value IS the text, so quoting is the value-preserving
        # move here.
        return f"'{raw}'"
    if tag in ("int", "float"):
        rendered = _render_value(tag, raw, schema_name="legacy")
        # 1.1 sexagesimal collapses to an integer; keep it an integer.
        if tag == "int" and rendered.lstrip("-").isdigit():
            return rendered
        if tag == "int":
            return rendered
        # A float needs a dot or an exponent to stay a float in 1.2.
        if "." not in rendered and "e" not in rendered.lower():
            return f"{rendered}.0"
        return rendered
    if tag == "null":
        return "null"
    return f"'{raw}'"


def _collect_changes(
    text: str,
    direction: Direction,
    *,
    mode: Mode,
    timestamps: bool,
) -> tuple[list[tuple[Any, Change]], list[str]]:
    """Find scalars to rewrite. Returns (node, change) pairs plus skip reasons."""
    node = yaml.compose(text)
    if node is None:
        return [], []

    scalars: list[tuple[Any, str]] = []
    _iter_scalars(node, "", scalars)

    hits: list[tuple[Any, Change]] = []
    skipped: list[str] = []
    seen_offsets: set[tuple[int, int]] = set()

    for scalar, path in scalars:
        style = getattr(scalar, "style", None)
        value = scalar.value
        where = f"line {scalar.start_mark.line + 1}:{scalar.start_mark.column + 1} {path}"

        # Already quoted or literal: the author (or a previous run) settled it.
        if _is_quoted_style(style):
            continue
        # The merge key is structural; touching it changes meaning.
        if schema.is_merge_key(value):
            continue
        # Explicit non-string tags mean the author already decided the type.
        current = _tag_name(scalar.tag)
        if not scalar.tag.startswith("tag:yaml.org,2002:"):
            continue

        # IMPORTANT: classify from the *source text*, never from the tag.
        # `yaml.compose` resolves with the 1.1 schema, so by the time we look at
        # the node, `NO` already carries tag:bool. Reading the tag would tell us
        # what 1.1 thought, not what the author typed -- and the author typed a
        # plain scalar whose 1.2 meaning is what we need to compare.
        legacy = _tag_name(schema.classify_legacy(value))
        core = _tag_name(schema.classify_core(value, timestamps=timestamps))

        # Compare *values*, not tags.  Tag-only comparison misses the nastiest
        # class of breakage: `0644` is int in both schemas but means 420 in 1.1
        # and 644 in 1.2.  Same tag, different number, no error anywhere.
        legacy_val = _render_value(legacy, value, schema_name="legacy")
        core_val = _render_value(core, value, schema_name="core")
        numeric = ("int", "float")

        # The single question: do the two schemas disagree about this scalar?
        # If they agree, touching it would be gratuitous churn (and would turn
        # a perfectly good `replicas: 3` into `replicas: '3'`).
        disagrees = legacy != core or legacy_val != core_val

        if direction == "to_1_2":
            # Preparing a file for 1.2 readers: neutralise every disagreement,
            # because the 1.2 reader is the one that will now see the file.
            should = disagrees
        else:
            # Preparing a file for 1.1 readers: only 1.2-introduced typing can
            # surprise them, i.e. scalars 1.1 read as strings.
            should = disagrees and legacy == "str"

        if not should:
            continue

        ambiguous_numeric = legacy in numeric and core in numeric and legacy_val != core_val

        # A scalar that already carries an explicit non-string tag was authored
        # as that type; leave it alone. (Plain scalars never land here because
        # the check above skips anything whose tag is not a YAML core tag.)
        if current not in ("str", "bool", "int", "float", "null", "timestamp"):
            skipped.append(f"{where}: explicit !!{current}")
            continue

        if "\n" in value:
            skipped.append(f"{where}: multi-line scalar")
            continue

        offset = (scalar.start_mark.index, scalar.end_mark.index)
        if offset in seen_offsets:
            # An alias pointed at a node we already scheduled.
            continue
        seen_offsets.add(offset)

        replacement = _replacement_for(legacy, value, mode, core)

        # Keys are always pinned as text, never rewritten by value.
        # `on:` in a CI file must stay the key "on"; rewriting it to `true:`
        # would produce a config no workflow engine can read, and rewriting a
        # numeric key to a number would silently change the document's shape.
        is_key = path.endswith(" (key)")
        if is_key:
            replacement = f"'{value}'"

        hits.append(
            (
                scalar,
                Change(
                    line=scalar.start_mark.line + 1,
                    column=scalar.start_mark.column + 1,
                    path=path or "<root>",
                    value=value,
                    legacy_tag=legacy,
                    core_tag=core,
                    legacy_value=legacy_val,
                    core_value=core_val,
                    ambiguous_numeric=ambiguous_numeric,
                    action="quote" if (mode == "text" or is_key) else "rewrite",
                    replacement=replacement,
                ),
            )
        )

    return hits, skipped


def plan(
    text: str,
    *,
    direction: Direction = "to_1_2",
    mode: Mode = "text",
    timestamps: bool = True,
) -> list[Change]:
    """Return the changes :func:`migrate_text` would make, without applying them."""
    hits, _ = _collect_changes(text, direction, mode=mode, timestamps=timestamps)
    return [change for _, change in hits]


def _apply(text: str, hits: Iterable[tuple[Any, Change]]) -> str:
    """Splice replacements into ``text`` at the scalars' recorded offsets.

    Offsets come from PyYAML's marks and are stable under splicing as long as
    we work back-to-front, which is why the list is sorted in reverse here.

    Aliases make the same scalar reachable more than once: ``&a`` defines a node
    and ``*a`` references it, and a composed tree hands back the *same* node
    object with the *same* marks.  Without deduplication the same offsets get
    spliced twice and ``1`` becomes ``'''1'``.  Keyed by (start, end) because
    that is the identity that matters for splicing.
    """
    unique: dict[tuple[int, int], Change] = {}
    for scalar, change in hits:
        unique.setdefault((scalar.start_mark.index, scalar.end_mark.index), change)

    pieces = list(text)
    for (start, end), change in sorted(unique.items(), reverse=True):
        # Guard: the slice must be the scalar we think it is.  If a future
        # PyYAML changes mark semantics, fail loudly instead of corrupting.
        if text[start:end] != change.value:
            raise RuntimeError(
                f"mark/slice mismatch at {start}:{end}: "
                f"expected {change.value!r}, found {text[start:end]!r}"
            )
        pieces[start:end] = [change.replacement]
    return "".join(pieces)


def migrate_text(
    text: str,
    *,
    direction: Direction = "to_1_2",
    mode: Mode = "text",
    timestamps: bool = True,
    path: str = "<string>",
) -> MigrationReport:
    """Migrate YAML source in memory.

    Raises whatever PyYAML raises for malformed input -- a migration tool must
    not "repair" a file it cannot parse.
    """
    if direction not in ("to_1_2", "to_1_1"):
        raise ValueError(f"direction must be 'to_1_2' or 'to_1_1', got {direction!r}")
    if mode not in ("text", "value"):
        raise ValueError(f"mode must be 'text' or 'value', got {mode!r}")
    hits, skipped = _collect_changes(text, direction, mode=mode, timestamps=timestamps)
    migrated = _apply(text, hits) if hits else text
    return MigrationReport(
        path=path,
        direction=direction,
        mode=mode,
        changes=[change for _, change in hits],
        skipped=skipped,
        original=text,
        migrated=migrated,
    )


def migrate_file(
    source: str,
    *,
    destination: str | None = None,
    direction: Direction = "to_1_2",
    mode: Mode = "text",
    timestamps: bool = True,
    in_place: bool = False,
    encoding: str = "utf-8",
) -> MigrationReport:
    """Migrate a file on disk.

    ``in_place=False`` and no ``destination`` means "dry run": the report is
    produced and nothing is written.  That is the default on purpose.
    """
    with open(source, "r", encoding=encoding, newline="") as handle:
        text = handle.read()

    report = migrate_text(
        text, direction=direction, mode=mode, timestamps=timestamps, path=source
    )

    target = source if in_place else destination
    if target and report.changed:
        # newline="" keeps CRLF files CRLF; rewriting them would create a
        # whole-file diff for no reason.
        with open(target, "w", encoding=encoding, newline="") as handle:
            handle.write(report.migrated)
    return report
