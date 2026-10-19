"""yaml12-bridge -- read and write YAML 1.2 Core Schema on top of PyYAML.

PyYAML is a YAML 1.1 parser and its maintainers have said that defaulting to
1.2 "would break a lot of stuff", so the fix is opt-in at the call site rather
than upstream. This package is that opt-in: same scanner, same error messages,
1.2 scalar semantics, plus a migration tool for files that were written under
1.1 rules and now need to mean the same thing to a 1.2 parser.

Quick start
-----------
>>> import yaml12bridge
>>> yaml12bridge.load("on: off")["on"]
'off'

>>> yaml12bridge.load("mode: 0644")["mode"]
644
"""

from .loader import (
    DuplicateKeyError,
    LoaderOptions,
    VersionMismatchWarning,
    YAML12Loader,
    YAML12SafeLoader,
    load,
    load_all,
)
from .schema import classify_core, classify_legacy

__version__ = "0.1.0"

__all__ = [
    "load",
    "load_all",
    "LoaderOptions",
    "YAML12Loader",
    "YAML12SafeLoader",
    "DuplicateKeyError",
    "VersionMismatchWarning",
    "classify_core",
    "classify_legacy",
    "__version__",
]
