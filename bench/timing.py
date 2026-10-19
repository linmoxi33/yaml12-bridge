"""Timing: is the compatibility layer usable, or is it a toy?

Compare like with like. ``yaml.safe_load`` is a thin wrapper over the
*pure-Python* SafeLoader, while this package defaults to libyaml's
``CSafeLoader``. Comparing those two directly makes the schema layer look five
times faster than PyYAML, which is nonsense -- the speed is libyaml's, not ours.
Both baselines are therefore reported, and the honest comparison is
``yaml12`` vs ``CSafeLoader``.

Reported, not asserted: timing on a shared machine is noisy, and a flaky perf
test is worse than no perf test.
"""

from __future__ import annotations

import pathlib
import statistics
import time

import yaml

from yaml12bridge import LoaderOptions, load

CORPUS = pathlib.Path(__file__).resolve().parent.parent / "tests" / "corpus"


def best_of(fn, repeats: int = 7) -> tuple[float, float]:
    times = []
    for _ in range(repeats):
        start = time.perf_counter()
        fn()
        times.append((time.perf_counter() - start) * 1000)
    return min(times), statistics.median(times)


def report(title: str, cases: list[tuple[str, object]], repeats: int = 7) -> None:
    print(title)
    for name, fn in cases:
        best, med = best_of(fn, repeats=repeats)
        print(f"  {name:26} best={best:8.2f}ms  median={med:8.2f}ms")


def main() -> None:
    files = sorted(CORPUS.glob("*.yml"))
    texts = [p.read_text(encoding="utf-8") for p in files]
    total = sum(len(t) for t in texts)

    report(
        f"corpus: {len(texts)} real files, {total} bytes",
        [
            ("pyyaml 1.1 (pure Python)", lambda: [yaml.load(t, Loader=yaml.SafeLoader) for t in texts]),
            ("pyyaml 1.1 (libyaml)", lambda: [yaml.load(t, Loader=yaml.CSafeLoader) for t in texts]),
            ("yaml12 (libyaml)", lambda: [load(t) for t in texts]),
            (
                "yaml12 (pure Python)",
                lambda: [load(t, use_c=False, options=LoaderOptions()) for t in texts],
            ),
        ],
    )

    big = "\n".join(f"k{i}: value{i}\nn{i}: {i}\n" for i in range(20_000))
    report(
        f"\nsynthetic: {len(big)} bytes, 40000 keys",
        [
            ("pyyaml 1.1 (pure Python)", lambda: yaml.load(big, Loader=yaml.SafeLoader)),
            ("pyyaml 1.1 (libyaml)", lambda: yaml.load(big, Loader=yaml.CSafeLoader)),
            ("yaml12 (libyaml)", lambda: load(big)),
        ],
        repeats=5,
    )


if __name__ == "__main__":
    main()
