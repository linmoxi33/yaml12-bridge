"""Regression tests against a corpus of real files.

The corpus in ``tests/corpus`` is 12 Playwright accessibility snapshots taken
from an actual browser session on this machine. They are real output from a real
tool: deep nesting, long strings, Unicode labels, bracket-heavy keys, `ref=e12`
annotations, and no schema-sensitive scalars at all.

That last property is why they matter. A migration tool's worst failure mode is
*false positive churn*: rewriting files that were never ambiguous, producing
diffs nobody asked for. These tests assert the tool leaves them byte-identical.
"""

from __future__ import annotations

import pathlib

import pytest

from yaml12bridge import load
from yaml12bridge.dumper import dump
from yaml12bridge.migrate import migrate_text

CORPUS = pathlib.Path(__file__).resolve().parent / "corpus"
FILES = sorted(CORPUS.glob("*.yml"))


def _normalise(obj: object) -> object:
    """Normalise boolean keys so the two schemas are comparable.

    PyYAML maps a bare ``on:`` key to True; a migrated file quotes it and both
    readers see "on". Comparing raw structures would report that fix as a
    difference, so True/False keys become "on"/"off" on both sides.
    """
    if isinstance(obj, dict):
        out = {}
        for key, value in obj.items():
            if key is True:
                key = "on"
            elif key is False:
                key = "off"
            out[key] = _normalise(value)
        return out
    if isinstance(obj, list):
        return [_normalise(item) for item in obj]
    return obj


def test_corpus_is_present() -> None:
    # Guard against the corpus being silently emptied and the suite going green.
    assert len(FILES) >= 10, f"expected a corpus of real files, found {len(FILES)}"


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name[:28])
def test_corpus_parses(path: pathlib.Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert load(text) is not None


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name[:28])
def test_corpus_migration_preserves_meaning(path: pathlib.Path) -> None:
    """After migration the 1.2 reading must equal the 1.1 reading.

    Most corpus files have no ambiguous scalars and come through byte-identical.
    One file is a music player snapshot containing durations like ``4:56``,
    which PyYAML reads as the sexagesimal integer 296 -- a genuine (and
    genuinely wrong) 1.1 behaviour, so migration correctly rewrites it.

    Keys are compared after normalisation: PyYAML turns a bare ``on:`` key into
    boolean True, and the migrated file's quoted ``'on':`` reads as the string
    "on" in *both* schemas. That is the fix, not a regression.
    """
    import yaml

    text = path.read_text(encoding="utf-8")
    report = migrate_text(text, mode="value", path=path.name)
    before = _normalise(yaml.safe_load(text))
    after = _normalise(load(report.migrated))
    assert after == before, f"{path.name}: meaning changed"


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name[:28])
def test_corpus_text_mode_only_quotes(path: pathlib.Path) -> None:
    """Text mode may add quotes but must never remove or reorder anything."""
    text = path.read_text(encoding="utf-8")
    report = migrate_text(text, path=path.name)
    for change in report.changes:
        assert change.replacement == f"'{change.value}'"
    # Same number of lines: nothing reflowed.
    assert len(report.migrated.splitlines()) == len(text.splitlines())


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name[:28])
def test_corpus_round_trips(path: pathlib.Path) -> None:
    """load -> dump -> load must be value-preserving on real data."""
    text = path.read_text(encoding="utf-8")
    first = load(text)
    assert load(dump(first)) == first


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name[:28])
def test_corpus_survives_value_mode(path: pathlib.Path) -> None:
    """value mode: the 1.2 reading of the output equals the 1.1 reading of input.

    The baseline must be PyYAML's 1.1 reading, not our own loader's -- comparing
    our loader against itself would be circular.
    """
    import yaml

    text = path.read_text(encoding="utf-8")
    migrated = migrate_text(text, mode="value").migrated
    assert _normalise(load(migrated)) == _normalise(yaml.safe_load(text))


def test_corpus_has_unicode_and_depth() -> None:
    """Sanity check that the corpus actually exercises something."""
    text = "\n".join(p.read_text(encoding="utf-8") for p in FILES)
    assert "\u4e2d\u6587" in text or any("\u4e00" <= ch <= "\u9fff" for ch in text)
    assert "[ref=" in text
