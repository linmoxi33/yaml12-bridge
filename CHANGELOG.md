# Changelog

## 0.1.0 -- 2026-10-19

First release.

### Added

- `load()` / `load_all()`: YAML 1.2 Core Schema resolution on PyYAML's scanner.
  Same error messages, same anchors/merge keys/block scalars, different scalar
  types.
- `LoaderOptions`: `strict_duplicates` (default on), `timestamps` (default off,
  spec-literal), `version` (`1.1` / `1.2` / `auto`).
- `dump()` / `dump_all()`: quotes strings that a 1.2 parser would retype,
  including the case PyYAML gets wrong today (`3900e128` -> `3.9e+131`).
- `migrate_text()` / `migrate_file()`: offset-precise migration that preserves
  comments, anchors, key order and block scalars, in two modes:
  - `text` keeps the literal characters (`NO` -> `'NO'`)
  - `value` keeps the parsed value (`NO` -> `false`)
- `yaml12` CLI: `check`, `diff`, `migrate` (with `--check` for CI), `explain`,
  `roundtrip`.
- `VersionMismatchWarning` when a document's `%YAML` directive disagrees with
  the schema used to parse it -- PyYAML accepts the directive and ignores it.

### Notable findings, kept as tests

- PyYAML's `BaseResolver.resolve` uses prefix matching, so spec regexps must be
  anchored or `12:30` resolves as int 12.
- Replacing the resolver is not enough: PyYAML's int constructor accepts `0o14`
  and sums sexagesimal parts, so the constructors must be replaced too.
- `<<` is an implicit resolver in PyYAML (bucket `<`), not a path resolver;
  dropping it silently turns merges into literal keys.
- libyaml does not expose `yaml_version`, so the `%YAML` directive has to be
  read from the source text for both loaders to behave the same.
- Single-letter `y`/`n` are booleans in the YAML 1.1 spec but not in PyYAML
  6.0.3. The 1.1 table follows the implementation, and a test pins it.
