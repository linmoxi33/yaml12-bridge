"""CLI contract tests: exit codes are the API that CI depends on."""

from __future__ import annotations

import pathlib

import pytest

from yaml12bridge.cli import main

SAMPLES = pathlib.Path(__file__).resolve().parent.parent / "samples"


def test_check_passes_on_valid_samples(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["check", str(SAMPLES)]) == 0
    assert "ok" in capsys.readouterr().out


def test_check_fails_on_duplicate_keys(tmp_path: pathlib.Path) -> None:
    bad = tmp_path / "dup.yml"
    bad.write_text("a: 1\na: 2\n", encoding="utf-8")
    assert main(["check", str(bad)]) == 1


def test_check_allows_duplicates_on_request(tmp_path: pathlib.Path) -> None:
    bad = tmp_path / "dup.yml"
    bad.write_text("a: 1\na: 2\n", encoding="utf-8")
    assert main(["check", str(bad), "--allow-duplicates"]) == 0


def test_check_reports_parse_errors(tmp_path: pathlib.Path) -> None:
    bad = tmp_path / "broken.yml"
    bad.write_text("a: [1, 2\n", encoding="utf-8")
    assert main(["check", str(bad)]) == 1


def test_diff_flags_meaning_change() -> None:
    assert main(["diff", str(SAMPLES / "github-actions.yml")]) == 1


def test_diff_is_quiet_when_nothing_differs() -> None:
    assert main(["diff", str(SAMPLES / "playwright-snapshot.yml"), "-v"]) == 0


def test_migrate_dry_run_writes_nothing(tmp_path: pathlib.Path) -> None:
    target = tmp_path / "in.yml"
    target.write_text("a: NO\n", encoding="utf-8")
    assert main(["migrate", str(target)]) == 0
    assert target.read_text(encoding="utf-8") == "a: NO\n"


def test_migrate_in_place(tmp_path: pathlib.Path) -> None:
    target = tmp_path / "in.yml"
    target.write_text("a: NO\n", encoding="utf-8")
    assert main(["migrate", str(target), "--in-place"]) == 0
    assert target.read_text(encoding="utf-8") == "a: 'NO'\n"


def test_migrate_check_is_a_ci_gate() -> None:
    # A file that needs migration must fail the gate.
    assert main(["migrate", str(SAMPLES / "github-actions.yml"), "--check", "-q"]) == 1


def test_migrate_check_passes_when_clean(tmp_path: pathlib.Path) -> None:
    clean = tmp_path / "clean.yml"
    clean.write_text("a: 1\nb: hello\n", encoding="utf-8")
    assert main(["migrate", str(clean), "--check"]) == 0


def test_explain_reports_both_schemas(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["explain", "NO"]) == 0
    out = capsys.readouterr().out
    assert "bool" in out and "str" in out


def test_roundtrip_passes_on_samples() -> None:
    assert main(["roundtrip", str(SAMPLES)]) == 0


def test_missing_file_is_an_error() -> None:
    assert main(["check", "does-not-exist-xyz.yml"]) == 2


def test_glob_expansion(tmp_path: pathlib.Path) -> None:
    (tmp_path / "a.yml").write_text("a: 1\n", encoding="utf-8")
    (tmp_path / "b.yaml").write_text("b: 2\n", encoding="utf-8")
    assert main(["check", str(tmp_path / "*.y*ml")]) == 0
