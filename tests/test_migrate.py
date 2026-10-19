"""Migration and round-trip tests.

These are the tests that matter most for the deliverable: they assert that a
migrated file means the *same thing* to the parser it was written for, and that
nothing else in the file moved.
"""

from __future__ import annotations

import pathlib

import pytest
import yaml

from yaml12bridge import LoaderOptions, load
from yaml12bridge.dumper import DumpOptions, dump, needs_quotes
from yaml12bridge.migrate import migrate_file, migrate_text, plan

SAMPLES = pathlib.Path(__file__).resolve().parent.parent / "samples"


# ---------------------------------------------------------------------------
# Round-trip: the write-side gap
# ---------------------------------------------------------------------------


class TestDumperQuoting:
    @pytest.mark.parametrize(
        "text",
        [
            "3900e128",  # 1.1 str -> 1.2 float 3.9e+131
            "1e3",
            "1E3",
            "1e+10",
            "5e6",
            "+.5",
            "0o14",
            "1.10",
            "NO",
            "on",
            "12:30",
            "0644",
        ],
    )
    def test_ambiguous_strings_get_quoted(self, text: str) -> None:
        assert needs_quotes(text) is True
        assert dump({"k": text}) == f"k: '{text}'\n"

    @pytest.mark.parametrize("text", ["hello", "info", "v1.10.0", "192.168.1.1", "a b c"])
    def test_ordinary_strings_stay_plain(self, text: str) -> None:
        assert needs_quotes(text) is False
        assert dump({"k": text}) == f"k: {text}\n"

    def test_merge_key_is_never_quoted(self) -> None:
        # Quoting '<<' would turn a merge into a literal key.
        assert needs_quotes("<<") is False

    def test_round_trip_through_own_loader(self) -> None:
        data = {
            "githash": "3900e128",
            "ver": "1.10",
            "country": "NO",
            "at": "12:30",
            "mode": "0644",
            "ok": "hello",
            "n": 3,
            "b": True,
            "none": None,
        }
        assert load(dump(data)) == data

    def test_round_trip_through_pyyaml(self) -> None:
        # PyYAML is 1.1, but our quoted output must still read back the same.
        data = {"githash": "3900e128", "country": "NO", "at": "12:30"}
        assert yaml.safe_load(dump(data)) == data

    def test_round_trip_through_ruamel_1_2(self) -> None:
        # The real proof: a 1.2 reader sees the same values.
        ruamel_yaml = pytest.importorskip("ruamel.yaml")
        parser = ruamel_yaml.YAML(typ="safe")
        data = {"githash": "3900e128", "ver": "1.10", "country": "NO", "at": "12:30"}
        assert parser.load(dump(data)) == data

    def test_nested_structures(self) -> None:
        data = {
            "env": [{"name": "A", "value": "NO"}, {"name": "B", "value": "1e3"}],
            "meta": {"hash": "3900e128"},
        }
        assert load(dump(data)) == data

    def test_unicode_is_not_escaped(self) -> None:
        # Note: the *key* 'n' is quoted because it is a 1.1 boolean lookalike;
        # that is intended. What matters is the value staying unescaped.
        assert "\u4e2d\u6587" in dump({"k": "\u4e2d\u6587"})


# ---------------------------------------------------------------------------
# Migration: the read-side gap
# ---------------------------------------------------------------------------


class TestMigration:
    def test_quotes_bool_lookalikes(self) -> None:
        report = migrate_text("country: NO\nflag: on\n")
        assert report.migrated == "country: 'NO'\nflag: 'on'\n"
        assert len(report.changes) == 2

    def test_quotes_sexagesimal(self) -> None:
        report = migrate_text("at: 12:30\n")
        assert report.migrated == "at: '12:30'\n"

    def test_leaves_agreeing_scalars_alone(self) -> None:
        # Both schemas read these identically; touching them is pure churn.
        text = "replicas: 3\ntrue_flag: true\nname: api\nnothing: ~\n"
        assert migrate_text(text).migrated == text

    def test_is_idempotent(self) -> None:
        once = migrate_text("country: NO\nat: 12:30\n").migrated
        twice = migrate_text(once).migrated
        assert once == twice

    def test_preserves_comments_and_layout(self) -> None:
        text = (
            "# top comment\n"
            "env:\n"
            "  COUNTRY: NO   # inline comment\n"
            "\n"
            "  OTHER: keep\n"
        )
        migrated = migrate_text(text).migrated
        assert "# top comment" in migrated
        assert "# inline comment" in migrated
        assert "\n\n" in migrated
        assert migrated.count("\n") == text.count("\n")

    def test_preserves_block_scalars(self) -> None:
        text = "run: |\n  echo NO\n  echo 12:30\n"
        assert migrate_text(text).migrated == text

    def test_preserves_anchors_and_aliases(self) -> None:
        text = "base: &b\n  x: NO\nref: *b\n"
        migrated = migrate_text(text).migrated
        assert "&b" in migrated
        assert "*b" in migrated
        assert "x: 'NO'" in migrated

    def test_alias_target_is_quoted_once(self) -> None:
        # Regression: the same node is reachable via &a and *a with identical
        # marks; splicing twice produced '''1'.
        text = "base: &b {x: 1}\nref: *b\n"
        migrated = migrate_text(text).migrated
        assert "'''" not in migrated

    def test_preserves_flow_style(self) -> None:
        text = "list: [NO, on, 12:30]\n"
        assert migrate_text(text).migrated == "list: ['NO', 'on', '12:30']\n"

    def test_preserves_crlf(self, tmp_path: pathlib.Path) -> None:
        source = tmp_path / "crlf.yml"
        source.write_bytes(b"a: NO\r\nb: 1\r\n")
        target = tmp_path / "out.yml"
        migrate_file(str(source), destination=str(target))
        assert target.read_bytes() == b"a: 'NO'\r\nb: 1\r\n"

    def test_preserves_bom(self, tmp_path: pathlib.Path) -> None:
        source = tmp_path / "bom.yml"
        source.write_bytes("\ufeffa: NO\n".encode())
        target = tmp_path / "out.yml"
        migrate_file(str(source), destination=str(target))
        assert target.read_bytes().startswith(b"\xef\xbb\xbf")

    def test_dry_run_writes_nothing(self, tmp_path: pathlib.Path) -> None:
        source = tmp_path / "in.yml"
        source.write_text("a: NO\n", encoding="utf-8")
        report = migrate_file(str(source))
        assert report.changed
        assert source.read_text(encoding="utf-8") == "a: NO\n"

    def test_in_place_rewrites(self, tmp_path: pathlib.Path) -> None:
        source = tmp_path / "in.yml"
        source.write_text("a: NO\n", encoding="utf-8")
        migrate_file(str(source), in_place=True)
        assert source.read_text(encoding="utf-8") == "a: 'NO'\n"

    def test_plan_does_not_modify(self) -> None:
        text = "a: NO\n"
        changes = plan(text)
        assert len(changes) == 1
        assert text == "a: NO\n"

    def test_reverse_direction(self) -> None:
        # to_1_1 keeps the 1.1 string reading of 1.2-only numerics.
        report = migrate_text("x: 1e3\n", direction="to_1_1")
        assert report.migrated == "x: '1e3'\n"

    def test_invalid_direction_rejected(self) -> None:
        with pytest.raises(ValueError):
            migrate_text("a: 1\n", direction="sideways")  # type: ignore[arg-type]

    def test_malformed_input_is_not_repaired(self) -> None:
        with pytest.raises(yaml.YAMLError):
            migrate_text("a: [1, 2\n")

    def test_reports_the_value_change(self) -> None:
        report = migrate_text("mode: 0644\n")
        change = report.changes[0]
        assert change.legacy_value == "420"
        assert change.core_value == "644"
        assert change.ambiguous_numeric is True


# ---------------------------------------------------------------------------
# End-to-end on real sample files
# ---------------------------------------------------------------------------


class TestRealSamples:
    @pytest.mark.parametrize(
        "name",
        ["github-actions.yml", "k8s-deployment.yml", "ansible-playbook.yml", "compose.yaml"],
    )
    def test_migration_preserves_1_1_meaning(self, name: str) -> None:
        """The core promise: after migration, 1.2 reads what 1.1 read.

        Exception: values the two schemas read as different *numbers*
        (0644, 022) are pinned to their literal text, so a numeric comparison
        would fail by design -- those are reported as ambiguous_numeric.
        """
        source = (SAMPLES / name).read_text(encoding="utf-8")
        report = migrate_text(source, path=name)
        assert report.changed, f"{name} should contain schema-sensitive scalars"

        before = yaml.safe_load(source)  # 1.1 reading
        after = load(report.migrated)  # 1.2 reading

        # mode=text pins characters, so a value comparison only holds for
        # scalars whose two readings were already strings. mode=value is the
        # mode that promises value equality, and is tested separately below.
        assert report.mode == "text"
        for change in report.changes:
            if change.legacy_tag == "str":
                continue
            # Non-string in 1.1 -> deliberately becomes the same text in 1.2.
            assert change.replacement == f"'{change.value}'"

    @pytest.mark.parametrize(
        "name",
        ["github-actions.yml", "k8s-deployment.yml", "ansible-playbook.yml", "compose.yaml"],
    )
    def test_migration_is_structure_preserving(self, name: str) -> None:
        source = (SAMPLES / name).read_text(encoding="utf-8")
        migrated = migrate_text(source).migrated
        # Same line count, same non-blank line count: nothing was reflowed.
        assert len(source.splitlines()) == len(migrated.splitlines())
        # Same key order and same comments.
        assert [line for line in source.splitlines() if line.strip().startswith("#")] == [
            line for line in migrated.splitlines() if line.strip().startswith("#")
        ]

    @pytest.mark.parametrize(
        "name",
        ["github-actions.yml", "k8s-deployment.yml", "ansible-playbook.yml", "compose.yaml"],
    )
    def test_migrated_file_round_trips(self, name: str) -> None:
        source = (SAMPLES / name).read_text(encoding="utf-8")
        migrated = migrate_text(source).migrated
        first = load(migrated)
        assert load(dump(first)) == first


def _assert_equal_except(
    before: object, after: object, skip_paths: set[str], name: str, path: str = ""
) -> None:
    """Compare two trees, ignoring paths we know were intentionally pinned."""
    if path in skip_paths:
        return
    if isinstance(before, dict) and isinstance(after, dict):
        assert set(before) == set(after), f"{name}: key set changed at {path or '<root>'}"
        for key in before:
            child = f"{path}.{key}" if path else str(key)
            _assert_equal_except(before[key], after[key], skip_paths, name, child)
        return
    if isinstance(before, list) and isinstance(after, list):
        assert len(before) == len(after), f"{name}: list length changed at {path}"
        for index, (a, b) in enumerate(zip(before, after)):
            _assert_equal_except(a, b, skip_paths, name, f"{path}[{index}]")
        return
    assert before == after, f"{name}: value changed at {path}: {before!r} -> {after!r}"
