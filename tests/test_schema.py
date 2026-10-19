"""Conformance tests: every assertion here was derived from an observed
difference between PyYAML 6.0.3 (YAML 1.1) and a YAML 1.2 reader, not from
memory.  The comments name the behaviour being pinned so a future reader can
tell whether a failure is a regression or an intended change.
"""

from __future__ import annotations

import datetime
import warnings

import pytest
import yaml

import yaml12bridge
from yaml12bridge import LoaderOptions, load
from yaml12bridge.schema import classify_core, classify_legacy


class TestCoreScalars:
    """The scalar table, one behaviour per test."""

    @pytest.mark.parametrize(
        "text, expected",
        [
            # 1.2 dropped these from the bool set entirely.
            ("on", "on"),
            ("off", "off"),
            ("yes", "yes"),
            ("no", "no"),
            ("y", "y"),
            ("n", "n"),
            ("Y", "Y"),
            ("N", "N"),
            # ...but kept the three casings of true/false.
            ("true", True),
            ("True", True),
            ("TRUE", True),
            ("false", False),
            ("False", False),
            ("FALSE", False),
        ],
    )
    def test_bool_set(self, text: str, expected: object) -> None:
        assert load(f"k: {text}")["k"] == expected

    @pytest.mark.parametrize(
        "text, expected",
        [
            # Sexagesimal is gone in 1.2; these are strings now.
            ("12:30", "12:30"),
            ("1:30:00", "1:30:00"),
            # 1.1 leading-zero octal becomes decimal 10.
            ("0644", 644),
            ("022", 22),
            ("007", 7),
            # 1.2 spellings that 1.1 did not accept.
            ("0o14", 12),
            ("0x1F", 31),
            ("+5", 5),
            ("-0", 0),
            # Underscore separators are not in the 1.2 core schema.
            ("1_000", "1_000"),
            ("0b101", "0b101"),
        ],
    )
    def test_int_set(self, text: str, expected: object) -> None:
        assert load(f"k: {text}")["k"] == expected

    @pytest.mark.parametrize(
        "text, expected",
        [
            # JSON-style exponents without a dot are floats in 1.2 only.
            ("1e3", 1000.0),
            ("1E3", 1000.0),
            ("3900e128", 3.9e131),
            ("5e6", 5000000.0),
            ("1e+10", 1e10),
            ("+.5", 0.5),
            ("1.", 1.0),
            (".5", 0.5),
            ("1.10", 1.1),
        ],
    )
    def test_float_set(self, text: str, expected: float) -> None:
        assert load(f"k: {text}")["k"] == pytest.approx(expected)

    @pytest.mark.parametrize("text", ["~", "null", "Null", "NULL"])
    def test_null_set(self, text: str) -> None:
        assert load(f"k: {text}")["k"] is None

    def test_empty_scalar_is_null(self) -> None:
        assert load("k:")["k"] is None

    def test_plain_strings_stay_strings(self) -> None:
        for text in ("192.168.1.1", "1.2.3", "info", "v1.10.0", "2024-13-45"):
            assert load(f"k: {text}")["k"] == text


class TestTimestamps:
    """The 1.2 Core Schema has no timestamp type; we make that a choice."""

    def test_dates_are_strings_by_default(self) -> None:
        assert load("d: 2001-12-14")["d"] == "2001-12-14"

    def test_dates_can_be_opted_into(self) -> None:
        value = load("d: 2001-12-14", options=LoaderOptions(timestamps=True))["d"]
        assert value == datetime.date(2001, 12, 14)

    def test_datetime_with_offset(self) -> None:
        value = load(
            "d: 2001-12-14 21:59:43.10 -5", options=LoaderOptions(timestamps=True)
        )["d"]
        assert isinstance(value, datetime.datetime)
        assert value.utcoffset() == datetime.timedelta(hours=-5)


class TestClassificationTable:
    """classify_core / classify_legacy are the public form of the table."""

    def test_core_says_string_where_legacy_says_bool(self) -> None:
        # Note: single-letter y/n are NOT here. The 1.1 spec lists them as
        # booleans but PyYAML 6.0.3 returns the strings, and PyYAML is the
        # oracle (see TestLegacyTableMatchesRealPyYAML).
        for text in ("yes", "no", "on", "off"):
            assert classify_legacy(text) == "tag:yaml.org,2002:bool"
            assert classify_core(text) is None

    def test_int_before_float_ordering(self) -> None:
        # 0644 matches both patterns; the spec resolves int first.  Getting
        # this wrong turns file modes into floats.
        assert classify_core("0644") == "tag:yaml.org,2002:int"
        assert classify_core("007") == "tag:yaml.org,2002:int"

    def test_sexagesimal_is_string_in_core(self) -> None:
        assert classify_legacy("12:30") == "tag:yaml.org,2002:int"
        assert classify_core("12:30") is None


class TestVersionDirective:
    """`%YAML` is parsed by PyYAML and then ignored -- we surface that."""

    def test_directive_does_not_change_the_schema(self) -> None:
        # This is the upstream gap: the directive is accepted but has no effect.
        assert load("%YAML 1.2\n---\nc: NO\n")["c"] == "NO"
        assert yaml.safe_load("%YAML 1.2\n---\nc: NO\n")["c"] is False

    def test_mismatch_warns(self) -> None:
        with pytest.warns(yaml12bridge.VersionMismatchWarning):
            load("%YAML 1.1\n---\na: 1\n")

    def test_matching_version_does_not_warn(self) -> None:
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            load("%YAML 1.2\n---\na: 1\n")

    def test_auto_never_warns(self) -> None:
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            load("%YAML 1.1\n---\na: 1\n", options=LoaderOptions(version="auto"))

    def test_c_and_pure_python_agree(self) -> None:
        # libyaml does not expose yaml_version at all, so relying on it would
        # make the two loaders disagree. Regression guard for that fix.
        text = "%YAML 1.1\n---\nm: 0644\ns: 12:30\n"
        assert load(text, use_c=True) == load(text, use_c=False)


class TestStructuralBehaviour:
    def test_duplicate_keys_are_rejected(self) -> None:
        with pytest.raises(yaml12bridge.DuplicateKeyError) as excinfo:
            load("a: 1\na: 2\n")
        assert "duplicate key 'a'" in str(excinfo.value)

    def test_duplicate_keys_can_be_allowed(self) -> None:
        value = load("a: 1\na: 2\n", options=LoaderOptions(strict_duplicates=False))
        assert value == {"a": 2}

    def test_duplicate_detection_ignores_nesting(self) -> None:
        # Only the inner mapping repeats; the outer one is fine.
        assert load("outer:\n  a: 1\n  a: 2\n", options=LoaderOptions(strict_duplicates=False))
        with pytest.raises(yaml12bridge.DuplicateKeyError):
            load("outer:\n  a: 1\n  a: 2\n")

    def test_merge_key_still_merges(self) -> None:
        text = "base: &b {x: 1}\nchild:\n  <<: *b\n  y: 2\n"
        assert load(text)["child"] == {"x": 1, "y": 2}

    def test_anchors_and_aliases_survive(self) -> None:
        text = "a: &x [1, 2]\nb: *x\n"
        value = load(text)
        assert value["a"] == value["b"] == [1, 2]

    def test_merge_key_must_not_be_quoted_by_migration(self) -> None:
        from yaml12bridge.migrate import migrate_text

        text = "base: &b {x: 1}\nchild:\n  <<: *b\n"
        assert migrate_text(text).migrated == text


class TestErrorHandling:
    """A migration tool must fail loudly, never 'repair' silently."""

    @pytest.mark.parametrize(
        "text",
        [
            "a: [1, 2\n",  # unclosed flow sequence
            "a:\n\tb: 1\n",  # tab indentation
            "a: 1\n---\nb: 2\n",  # multi-doc through single-doc API
            ": v\n",  # empty key
        ],
    )
    def test_malformed_input_raises(self, text: str) -> None:
        with pytest.raises(yaml.YAMLError):
            load(text)

    def test_error_message_points_at_the_problem(self) -> None:
        with pytest.raises(yaml.YAMLError) as excinfo:
            load("a: [1, 2\n")
        assert "line 1" in str(excinfo.value)


class TestEncodings:
    def test_utf8_bom(self) -> None:
        assert load("\ufeffa: NO\n")["a"] == "NO"

    def test_crlf(self) -> None:
        assert load("a: NO\r\nb: 1\r\n") == {"a": "NO", "b": 1}

    def test_bytes_input(self) -> None:
        assert load(b"a: NO\n")["a"] == "NO"

    def test_unicode_values(self) -> None:
        assert load("n: \u4e2d\u6587\n")["n"] == "\u4e2d\u6587"


class TestLegacyTableMatchesRealPyYAML:
    """Our 1.1 table must agree with the PyYAML installed here.

    This is the anti-drift test. `classify_legacy` is hand-written from the 1.1
    spec plus observed behaviour, and it is easy to get subtly wrong -- it was:
    the spec lists single-letter y/n as booleans, but PyYAML 6.0.3 dropped them.
    If a future PyYAML changes again, this test fails first and points at the
    exact scalar.
    """

    SCALARS = [
        "y", "Y", "n", "N", "yes", "no", "on", "off",
        "true", "false", "Yes", "NO", "On", "Off", "TRUE", "FALSE",
        "~", "null", "Null", "NULL", "",
        "12:30", "1:30:00", "0644", "022", "0b101", "0x1F", "1_000", "0",
        "1e3", "1.5", "1.", ".5", "+.5", "-0", "-12", "+7",
        "2001-12-14", "2001-12-14 21:59:43", "3900e128",
        "abc", "192.168.1.1", "v1.10.0",
    ]

    @pytest.mark.parametrize("scalar", SCALARS)
    def test_matches_pyyaml(self, scalar: str) -> None:
        source = f"k: {scalar}\n" if scalar else "k:\n"
        actual = yaml.safe_load(source)["k"]
        expected = (classify_legacy(scalar) or "tag:yaml.org,2002:str").rsplit(":", 1)[-1]
        kind = {
            bool: "bool",
            int: "int",
            float: "float",
            type(None): "null",
            str: "str",
        }.get(type(actual))
        if kind is None:
            kind = "timestamp" if isinstance(actual, (datetime.date, datetime.datetime)) else type(actual).__name__
        assert kind == expected, f"{scalar!r}: PyYAML gives {actual!r} ({kind}), table says {expected}"
