"""Product version.

Reads from the repository-level `VERSION` file when running from source, or
from the `VERSION` file bundled inside the `app` package when installed as
a wheel.
"""

from __future__ import annotations

import os

# Two levels up from server/app/version.py → repo root (source layout).
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_REPO_VERSION = os.path.join(_REPO_ROOT, "VERSION")
_PKG_DIR = os.path.dirname(os.path.abspath(__file__))
_PKG_VERSION = os.path.join(_PKG_DIR, "VERSION")


def repo_root() -> str:
    """Return the absolute path of the repository root.

    Returns:
        The directory that contains `VERSION`, `server/` and `client/`.
    """
    return _REPO_ROOT


def _read_file(path: str) -> str | None:
    """Return the trimmed contents of *path*, or `None` on failure."""
    try:
        with open(path, encoding="utf-8") as handle:
            value = handle.read().strip()
            return value or None
    except OSError:
        return None


def get_version() -> str:
    """Return the product version string.

    Resolution order:
    1. `VERSION` file next to the package directory (wheel layout).
    2. `VERSION` file at the repository root (source layout).
    3. `"0.0.0"` as a fallback.
    """
    for path in (_PKG_VERSION, _REPO_VERSION):
        version = _read_file(path)
        if version is not None:
            return version
    return "0.0.0"


__version__ = get_version()
