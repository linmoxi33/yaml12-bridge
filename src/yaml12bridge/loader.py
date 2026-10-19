"""A YAML 1.2 Core Schema loader built on top of PyYAML's scanner.

Why subclass PyYAML instead of forking it
-----------------------------------------
PyYAML's *scanner and parser* are correct and battle-tested; its *resolver* is
where the 1.1 assumptions live.  The resolver is a pure function
``(node kind, scalar text) -> tag``, so replacing it is enough to change the
schema without touching tokenisation, anchors, block scalars, or error
messages.  That keeps the compatibility layer small and auditable: everything
we changed is in this file plus :mod:`yaml12bridge.schema`.

Two hard constraints discovered by testing (both are regression-tested):

1. ``BaseResolver.resolve`` never looks at the document's version directive.
   ``%YAML 1.2`` is parsed and then ignored, so a loader cannot "just honour
   the directive".  We therefore choose the schema at construction time and
   *report* what the document claimed.
2. ``CSafeLoader`` inherits the same Python-level ``resolve``, so a custom
   resolver table works with the C accelerator too -- no pure-Python fallback
   is needed for correctness, only for speed differences.
"""

from __future__ import annotations

import re
import warnings
from typing import Any

import yaml

from . import schema

__all__ = [
    "YAML12Loader",
    "YAML12SafeLoader",
    "VersionMismatchWarning",
    "DuplicateKeyError",
    "load",
    "load_all",
    "LoaderOptions",
]


class VersionMismatchWarning(UserWarning):
    """The document declared a YAML version we are not parsing it as."""


class DuplicateKeyError(yaml.YAMLError):
    """A mapping repeats a key. YAML 1.2 makes this an error, 1.1 did not."""

    def __init__(self, key: Any, mark: Any) -> None:
        self.key = key
        self.mark = mark
        where = ""
        if mark is not None:
            where = f" at line {mark.line + 1}, column {mark.column + 1}"
        super().__init__(f"duplicate key {key!r}{where}")


class LoaderOptions:
    """Knobs that change what counts as a conformance error.

    Parameters
    ----------
    strict_duplicates:
        Raise :class:`DuplicateKeyError` on repeated mapping keys.  YAML 1.2
        says keys must be unique; PyYAML silently keeps the last one, which is
        how ``a: 1\\na: 2`` loses data.  Default True -- silent loss is worse
        than a loud failure.
    timestamps:
        Also resolve ISO-like dates to ``datetime.date``/``datetime.datetime``.
        Off by default because the 1.2 Core Schema has no timestamp type; turn
        it on for byte-compatible behaviour with PyYAML.
    version:
        ``"1.2"`` (default), ``"1.1"``, or ``"auto"`` (use the document's
        ``%YAML`` directive, defaulting to 1.2).
    """

    __slots__ = ("strict_duplicates", "timestamps", "version")

    def __init__(
        self,
        *,
        strict_duplicates: bool = True,
        timestamps: bool = False,
        version: str = "1.2",
    ) -> None:
        if version not in ("1.1", "1.2", "auto"):
            raise ValueError(f"version must be '1.1', '1.2' or 'auto', got {version!r}")
        self.strict_duplicates = strict_duplicates
        self.timestamps = timestamps
        self.version = version

    def __repr__(self) -> str:
        return (
            f"LoaderOptions(version={self.version!r}, "
            f"strict_duplicates={self.strict_duplicates}, "
            f"timestamps={self.timestamps})"
        )


def _anchor(pattern: re.Pattern[str]) -> re.Pattern[str]:
    """Force whole-scalar matching.

    ``BaseResolver.resolve`` calls ``regexp.match(value)`` -- a *prefix* match.
    Our spec-derived patterns are written to be whole-scalar (they are used with
    ``fullmatch`` in :mod:`schema`), so handing them to PyYAML unanchored makes
    ``12:30`` resolve as int 12, ``1e3`` as int 1, and ``truex`` as bool.
    Anchoring at table-build time keeps schema.py readable and the loader
    correct.
    """
    return re.compile(rf"\A(?:{pattern.pattern})\Z")


def _build_core_resolvers(timestamps: bool) -> dict[str, list[tuple[str, re.Pattern[str]]]]:
    """Bucketed resolver table for 1.2, in PyYAML's expected shape."""
    rules = list(schema.CORE_RULES)
    if timestamps:
        rules.append(schema.CORE_TIMESTAMP_RULE)
    table: dict[str, list[tuple[str, re.Pattern[str]]]] = {}
    for rule in rules:
        compiled = _anchor(rule.pattern)
        chars = rule.first_chars
        if chars is None:
            table.setdefault("", []).append((rule.tag, compiled))
            continue
        for ch in chars:
            if ch == "":
                # The empty scalar is resolved through the "" bucket, which
                # PyYAML consults only when value == "".  Putting it in a
                # character bucket would never match.
                table.setdefault("", []).append((rule.tag, compiled))
            else:
                table.setdefault(ch, []).append((rule.tag, compiled))
    return table


# --- constructors -----------------------------------------------------------
# Replacing the resolver alone is NOT enough, and the failure is instructive:
# PyYAML's int constructor accepts "0o14" by calling int(value, 10) after a
# naive strip, and accepts "12:30" by summing sexagesimal parts.  So a 1.2 tag
# arriving at a 1.1 constructor either raises (ValueError on "3900e128") or
# silently produces 1.1 numbers (0644 -> 420).  The constructors below are the
# other half of the schema.

_INT_DEC = re.compile(r"[-+]?[0-9]+")
_INT_OCT = re.compile(r"0o([0-7]+)")
_INT_HEX = re.compile(r"[-+]?0x([0-9a-fA-F]+)")


def _construct_core_int(loader: Any, node: Any) -> int:
    text = loader.construct_scalar(node).replace("_", "")
    if _INT_DEC.fullmatch(text):
        return int(text, 10)
    match = _INT_OCT.fullmatch(text)
    if match:
        return int(match.group(1), 8)
    match = _INT_HEX.fullmatch(text)
    if match:
        body = match.group(1)
        value = int(body, 16)
        return -value if text.startswith("-") else value
    raise yaml.constructor.ConstructorError(
        None, None, f"could not parse {text!r} as a YAML 1.2 int", node.start_mark
    )


def _construct_core_float(loader: Any, node: Any) -> float:
    text = loader.construct_scalar(node).replace("_", "").lower()
    if text in (".inf", "+.inf"):
        return float("inf")
    if text == "-.inf":
        return float("-inf")
    if text == ".nan":
        return float("nan")
    try:
        return float(text)
    except ValueError as exc:
        raise yaml.constructor.ConstructorError(
            None, None, f"could not parse {text!r} as a YAML 1.2 float", node.start_mark
        ) from exc


_BOOL_TRUE = frozenset({"true", "True", "TRUE"})
_BOOL_FALSE = frozenset({"false", "False", "FALSE"})


def _construct_core_bool(loader: Any, node: Any) -> bool:
    text = loader.construct_scalar(node)
    if text in _BOOL_TRUE:
        return True
    if text in _BOOL_FALSE:
        return False
    raise yaml.constructor.ConstructorError(
        None,
        None,
        f"{text!r} is not a YAML 1.2 bool (1.2 dropped yes/no/on/off/y/n)",
        node.start_mark,
    )


def _construct_core_null(loader: Any, node: Any) -> None:
    return None


def _install_core_constructors(cls: type) -> None:
    """Attach 1.2 constructors to a loader class.

    PyYAML resolves constructors through ``yaml_constructors`` on the class
    (or its parents), so setting entries here shadows the 1.1 ones without
    mutating PyYAML's own classes.
    """
    table = dict(getattr(cls, "yaml_constructors", {}))
    table[schema.INT_TAG] = _construct_core_int
    table[schema.FLOAT_TAG] = _construct_core_float
    table[schema.BOOL_TAG] = _construct_core_bool
    table[schema.NULL_TAG] = _construct_core_null
    cls.yaml_constructors = table


_VERSION_DIRECTIVE = re.compile(r"^%YAML[ 	]+([0-9]+)\.([0-9]+)[ 	]*$", re.MULTILINE)


def _declared_version_of(stream: Any) -> tuple[int, int] | None:
    """Read the ``%YAML`` directive from the raw text.

    Needed because libyaml's parser (``CSafeLoader``) never exposes
    ``yaml_version`` while the pure-Python parser does.  Relying on the
    attribute would make the two loaders behave differently on the very
    documents this package exists for.  Scanning the prologue is cheap and
    gives both implementations the same answer.
    """
    if isinstance(stream, bytes):
        try:
            text = stream.decode("utf-8-sig")
        except UnicodeDecodeError:
            return None
    elif isinstance(stream, str):
        text = stream
    else:
        return None
    prologue = text.lstrip("\ufeff").split("\n---", 1)[0]
    match = _VERSION_DIRECTIVE.search(prologue)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def _warn_version(declared: tuple[int, int] | None, wanted: str) -> None:
    if not declared or wanted == "auto":
        return
    declared_str = f"{declared[0]}.{declared[1]}"
    if declared_str != wanted:
        warnings.warn(
            f"document declares %YAML {declared_str} but was parsed with the "
            f"{wanted} schema; plain scalars may differ",
            VersionMismatchWarning,
            stacklevel=4,
        )


class _YAML12Base:
    """Schema, duplicate-key and alias-budget logic shared by both loaders.

    Kept as a mixin rather than copy-pasted so the C and pure-Python loaders
    cannot drift apart.
    """

    options: LoaderOptions
    yaml_implicit_resolvers: dict[str, list[tuple[str, re.Pattern[str]]]]

    def _setup(self, stream: Any, options: LoaderOptions | None) -> None:
        self.options = options or LoaderOptions()
        self.yaml_implicit_resolvers = _build_core_resolvers(self.options.timestamps)
        # The merge key is a *structural* tag, not a scalar type.  PyYAML's
        # SafeLoader installs a path resolver that maps the `<<` key to
        # tag:yaml.org,2002:merge; our table replaces the implicit resolvers
        # but must keep that path resolver or `<<` becomes a literal string key
        # and merge semantics silently disappear.
        self.yaml_path_resolvers = dict(yaml.SafeLoader.yaml_path_resolvers)
        self._declared = _declared_version_of(stream)

    def _check_version(self) -> None:
        _warn_version(self._declared, self.options.version)

    def construct_mapping(self, node: Any, deep: bool = False) -> dict[Any, Any]:
        if not isinstance(node, yaml.MappingNode):
            raise yaml.constructor.ConstructorError(
                None, None, f"expected a mapping node, but found {node.id}", node.start_mark
            )
        if self.options.strict_duplicates:
            seen: set[Any] = set()
            for key_node, _ in node.value:
                # The merge key has no constructor (it is spliced into the
                # mapping by PyYAML's own flattening pass) and may legitimately
                # repeat in a list-of-aliases merge. Skip it rather than
                # constructing it.
                if key_node.tag == schema.MERGE_TAG:
                    continue
                key = self.construct_object(key_node, deep=True)
                try:
                    hash(key)
                except TypeError as exc:
                    raise yaml.constructor.ConstructorError(
                        "while constructing a mapping",
                        node.start_mark,
                        f"found unhashable key ({exc})",
                        key_node.start_mark,
                    ) from exc
                if key in seen:
                    raise DuplicateKeyError(key, key_node.start_mark)
                seen.add(key)
        return super().construct_mapping(node, deep=deep)  # type: ignore[misc]



class YAML12Loader(_YAML12Base, yaml.SafeLoader):
    """Pure-Python 1.2 loader."""

    def __init__(self, stream: Any, options: LoaderOptions | None = None) -> None:
        self._setup(stream, options)
        super().__init__(stream)

    def get_single_node(self) -> Any:
        node = super().get_single_node()
        self._check_version()
        return node


class YAML12SafeLoader(
    _YAML12Base,
    yaml.CSafeLoader if hasattr(yaml, "CSafeLoader") else yaml.SafeLoader,
):
    """C-accelerated 1.2 loader; falls back to pure Python without libyaml."""

    def __init__(self, stream: Any, options: LoaderOptions | None = None) -> None:
        self._setup(stream, options)
        super().__init__(stream)

    def get_single_node(self) -> Any:
        node = super().get_single_node()
        self._check_version()
        return node


_install_core_constructors(YAML12Loader)
_install_core_constructors(YAML12SafeLoader)


def load(
    stream: str | bytes,
    *,
    options: LoaderOptions | None = None,
    use_c: bool = True,
) -> Any:
    """Parse one YAML document with 1.2 Core Schema semantics."""
    loader_cls = YAML12SafeLoader if use_c else YAML12Loader
    return yaml.load(stream, Loader=lambda s: loader_cls(s, options))  # type: ignore[arg-type]


def load_all(
    stream: str | bytes,
    *,
    options: LoaderOptions | None = None,
    use_c: bool = True,
) -> list[Any]:
    """Parse every document in a multi-document stream."""
    loader_cls = YAML12SafeLoader if use_c else YAML12Loader
    return list(yaml.load_all(stream, Loader=lambda s: loader_cls(s, options)))
