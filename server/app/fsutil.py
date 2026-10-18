"""Small filesystem helpers shared by the launch logic and the file manager."""

from __future__ import annotations

import logging
import os
import re
import shutil

from fastapi import HTTPException

logger = logging.getLogger(__name__)


def resolve_within(base: str, path: str) -> str:
    """Return the real path of `path` after checking that it lies inside `base`.

    Both paths are resolved with `os.path.realpath`, so neither `..` segments
    nor symlinks can be used to escape `base`. Trailing components that do not
    exist yet are allowed, which makes the helper usable for paths about to be
    created.

    Args:
        base: Trusted directory the result must stay inside.
        path: Candidate path, usually built from client-supplied names.

    Returns:
        The resolved absolute path.

    Raises:
        ValueError: If `path` resolves outside `base`.
    """
    root = os.path.realpath(base)
    resolved = os.path.realpath(path)
    if resolved == root:
        return root
    if not resolved.startswith(root + os.sep):
        raise ValueError(f"Path '{path}' is outside '{base}'.")
    return resolved


def open_within(base: str, path: str, flags: int = os.O_RDONLY, mode: int = 0o644) -> int:
    """Open `path` and return the descriptor once what it opened lies inside `base`.

    A path checked by `resolve_within` can change before a later call follows
    it: whoever writes the tree (a session container mounts its user's home
    read-write) can swap a symlink in for its last component, or for a
    directory on the way. The open refuses a symlink in the last place, and
    the path the kernel resolved for the descriptor is checked against `base`
    before a byte is read or written through it.

    Args:
        base: Trusted directory the opened object must lie inside.
        path: The path, usually one `resolve_within` returned.
        flags: `os.open` flags; `O_NOFOLLOW` and `O_CLOEXEC` are added.
        mode: Mode of a file `flags` creates.

    Returns:
        The open descriptor, the caller's to close.

    Raises:
        ValueError: If the opened object lies outside `base`.
        OSError: If the open fails, a symlink in the last place included.
    """
    fd = os.open(path, flags | os.O_NOFOLLOW | os.O_CLOEXEC, mode)
    try:
        resolve_within(base, os.readlink(f"/proc/self/fd/{fd}"))
    except (OSError, ValueError) as exc:
        os.close(fd)
        raise ValueError(f"Path '{path}' is outside '{base}'.") from exc
    return fd


def open_parent_within(base: str, path: str) -> tuple[int, str]:
    """Open the directory holding `path` as `open_within` does.

    Returns:
        The directory's descriptor, the caller's to close, and `path`'s last
        component, which the caller acts on relative to it.

    Raises:
        ValueError: If the directory lies outside `base`, as `base`'s own
            parent does.
    """
    parent, name = os.path.split(os.path.normpath(path))
    return open_within(base, parent, os.O_RDONLY | os.O_DIRECTORY), name


def create_exclusive(dir_fd: int, filename: str, mode: int = 0o644) -> tuple[int, str]:
    """Create `filename`, or the first free `name-N.ext` variant, in the directory `dir_fd`.

    The name is taken by the create itself, never by a check before it, and a
    symlink already holding a name is passed over rather than followed.

    Returns:
        The new file's descriptor, open for writing, and the name it took.
    """
    name, ext = os.path.splitext(filename)
    candidate, counter = filename, 0
    while True:
        try:
            fd = os.open(
                candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, mode, dir_fd=dir_fd
            )
            return fd, candidate
        except FileExistsError:
            counter += 1
            candidate = f"{name}-{counter}{ext}"


def place_file(source: str, base: str, directory: str, filename: str, mode: int = 0o644) -> str:
    """Move the file `source` into `directory`, inside `base`, under `filename` or the next free variant.

    `directory` is one its user's sessions write, so it is opened and checked
    inside `base` (`open_within`), the name is taken by creating it
    (`create_exclusive`), and the file is renamed onto that name within the
    opened directory, or copied into it across filesystems: no symlink planted
    in the directory, or swapped in for it, takes the file anywhere else.

    Returns:
        The name the file took.

    Raises:
        ValueError: If `directory` lies outside `base`.
    """
    os.chmod(source, mode)
    dir_fd = open_within(base, directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        out_fd, name = create_exclusive(dir_fd, filename, mode)
        try:
            os.rename(source, name, dst_dir_fd=dir_fd)
        except OSError:
            with open(source, "rb") as src, os.fdopen(os.dup(out_fd), "wb") as dst:
                shutil.copyfileobj(src, dst, 1 << 20)
                os.fchmod(dst.fileno(), mode)
            os.remove(source)
        finally:
            os.close(out_fd)
        return name
    finally:
        os.close(dir_fd)


def safe_join(base: str, *parts: str) -> str:
    """Join `parts` onto `base` and return the resolved path inside `base`.

    Args:
        base: Trusted directory.
        *parts: Path components, typically client-supplied names.

    Returns:
        The resolved absolute path.

    Raises:
        ValueError: If the joined path resolves outside `base`.
    """
    return resolve_within(base, os.path.join(base, *parts))


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


def safe_rmtree(path: str, dir_fd: int | None = None) -> None:
    """Remove a directory tree, translating permission errors to HTTP 403.

    Args:
        path: Directory to remove, relative to `dir_fd` when one is given.
        dir_fd: Directory descriptor `path` is relative to.

    Raises:
        HTTPException: 403 when files owned by another user cannot be removed.
        OSError: Any other filesystem error.
    """
    try:
        shutil.rmtree(path, dir_fd=dir_fd)
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


def sanitize_for_filename(name: str) -> str:
    """Turn an arbitrary display name into a safe directory name.

    Args:
        name: Display name such as an application name.

    Returns:
        Lower-case ASCII letters, digits, and dashes, at most 50 characters,
        or `"unnamed"` for an empty input.
    """
    if not name:
        return "unnamed"
    slug = name.lower().strip()
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"[^a-z0-9-]", "", slug)
    return slug[:50] or "unnamed"
