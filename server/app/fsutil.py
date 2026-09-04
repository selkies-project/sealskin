"""Small filesystem helpers shared by the launch logic and the file manager."""

from __future__ import annotations

import logging
import os
import re
import shutil

from fastapi import HTTPException

logger = logging.getLogger(__name__)


def safe_copytree(src: str, dst: str, symlinks: bool = True) -> None:
    """Copy a directory tree, tolerating dangling symlinks.

    Args:
        src: Source directory.
        dst: Destination directory (must not exist).
        symlinks: Copy symlinks as symlinks instead of following them.
    """
    try:
        shutil.copytree(src, dst, symlinks=symlinks, ignore_dangling_symlinks=True)
    except shutil.Error as exc:
        logger.warning("Ignored errors during directory copy from %s to %s: %s", src, dst, exc)


def safe_rmtree(path: str) -> None:
    """Remove a directory tree, translating permission errors to HTTP 403.

    Args:
        path: Directory to remove.

    Raises:
        HTTPException: 403 when files owned by another user cannot be removed.
        OSError: Any other filesystem error.
    """
    try:
        shutil.rmtree(path)
    except OSError as exc:
        if exc.errno == 13:
            raise HTTPException(
                status_code=403,
                detail=(
                    "Directory cannot be deleted because of perms: there are non PUID and "
                    "PGID user owned files in those directories and we are not able to "
                    "remove them"
                ),
            ) from exc
        raise


def unique_filename(directory: str, filename: str) -> str:
    """Return `filename` or a `name-N.ext` variant that does not exist yet.

    Args:
        directory: Directory the file will be placed in.
        filename: Desired file name.

    Returns:
        A file name that is free inside `directory`.
    """
    if not os.path.exists(os.path.join(directory, filename)):
        return filename

    name, ext = os.path.splitext(filename)
    counter = 1
    while True:
        candidate = f"{name}-{counter}{ext}"
        if not os.path.exists(os.path.join(directory, candidate)):
            return candidate
        counter += 1


def sanitize_for_filename(name: str) -> str:
    """Turn an arbitrary display name into a safe directory name.

    Args:
        name: Display name such as an application name.

    Returns:
        Lower-case ASCII letters, digits and dashes, at most 50 characters,
        or `"unnamed"` for an empty input.
    """
    if not name:
        return "unnamed"
    slug = name.lower().strip()
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"[^a-z0-9-]", "", slug)
    return slug[:50] or "unnamed"
