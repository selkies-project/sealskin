"""Product version, read from the repository-level ``VERSION`` file."""

from __future__ import annotations

import os

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_VERSION_FILE = os.path.join(_REPO_ROOT, "VERSION")


def repo_root() -> str:
    """Return the absolute path of the repository root.

    Returns:
        The directory that contains ``VERSION``, ``server/`` and ``client/``.
    """
    return _REPO_ROOT


def get_version() -> str:
    """Return the product version string.

    Returns:
        The trimmed contents of ``VERSION``, or ``"0.0.0"`` when the file is
        missing (for example when the server directory is deployed alone).
    """
    try:
        with open(_VERSION_FILE, encoding="utf-8") as handle:
            value = handle.read().strip()
            return value or "0.0.0"
    except OSError:
        return "0.0.0"


__version__ = get_version()
