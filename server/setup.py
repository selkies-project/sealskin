"""Read VERSION from the repository root (PEP 517 build hook)."""

from __future__ import annotations

import os

from setuptools import setup

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _read_version() -> str:
    path = os.path.join(_ROOT, "VERSION")
    with open(path, encoding="utf-8") as fh:
        return fh.read().strip() or "0.0.0"


setup(version=_read_version())
