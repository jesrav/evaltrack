"""Filesystem `ObjectStore`, opened from a directory path."""

import os
import uuid
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Self

from evaltrack.core.errors import RepositoryUnavailableError
from evaltrack.repositories.repository import RunRepository
from evaltrack.repositories.store import ObjectNotFoundError, ObjectStore

# Stored objects can embed prompts and model output, so files are owner-only.
_FILE_MODE = 0o600
# A ref name is a branch or PR name, so a listable directory leaks what is
# being worked on.
_DIR_MODE = 0o700


def _create_tree(directory: Path) -> None:
    """Create `directory` and every missing parent at `_DIR_MODE`.

    `Path.mkdir(parents=True)` ignores `mode` for the parents it creates, so
    each level is made on its own. An existing directory keeps the mode it has.
    """
    missing = [d for d in (directory, *directory.parents) if not d.exists()]
    for level in reversed(missing):
        level.mkdir(_DIR_MODE, exist_ok=True)


@contextmanager
def _translating_os_errors() -> Generator[None]:
    """Re-raise a filesystem failure as `RepositoryUnavailableError`.
    `ObjectNotFoundError` must be raised before this block sees the error."""
    try:
        yield
    except OSError as exc:
        raise RepositoryUnavailableError(str(exc)) from exc


class FileStore(ObjectStore):
    """Filesystem-backed `ObjectStore`. Every key is resolved under `root`."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    @classmethod
    def from_path(cls, location: str) -> Self:
        # A `~` from pyproject.toml or an environment variable never met a shell.
        return cls(Path(os.path.expanduser(location)))

    def verify_available(self) -> None:
        """A missing root is fine. It counts as empty until the first write creates it."""
        with _translating_os_errors():
            self._verify_root_is_a_directory()

    def verify_writable(self) -> None:
        """A missing root is fine, since it is created on first write."""
        with _translating_os_errors():
            self._verify_root_is_a_directory()
            ancestor = self.root
            while not ancestor.exists() and ancestor != ancestor.parent:
                ancestor = ancestor.parent
            if not ancestor.is_dir():
                raise RepositoryUnavailableError(
                    f"cannot create {self.root}: {ancestor} is not a directory"
                )
            if not os.access(ancestor, os.W_OK):
                raise RepositoryUnavailableError(
                    f"cannot create {self.root}: {ancestor} is not writable"
                )

    def _verify_root_is_a_directory(self) -> None:
        if self.root.exists() and not self.root.is_dir():
            raise RepositoryUnavailableError(f"{self.root} is not a directory")

    def _resolve_path(self, path: str) -> Path:
        """Keys are validated by the repository before they reach the store, so
        the store does not re-check them. A subdirectory of `root` that is a
        symlink to another disk resolves outside the root and must keep
        working, so the path is joined without resolving it."""
        return self.root / path

    def read(self, path: str) -> bytes:
        with _translating_os_errors():
            try:
                return self._resolve_path(path).read_bytes()
            except FileNotFoundError:
                raise ObjectNotFoundError(path) from None

    def write(self, content: bytes, path: str) -> None:
        # uuid4 reads the system random source, not this store's I/O.
        suffix = uuid.uuid4().hex
        with _translating_os_errors():
            target = self._resolve_path(path)
            _create_tree(target.parent)
            # Staged under a unique name and published with replace(2), so a
            # reader never sees a torn file. Nothing is fsynced.
            tmp = target.with_name(f"{target.name}.{suffix}.tmp")
            try:
                # Opened at its final mode, so it is never wider for an instant.
                fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, _FILE_MODE)
                with os.fdopen(fd, "wb") as staged:
                    staged.write(content)
                os.replace(tmp, target)
            finally:
                tmp.unlink(missing_ok=True)

    def append(self, content: bytes, path: str) -> None:
        with _translating_os_errors():
            target = self._resolve_path(path)
            _create_tree(target.parent)
            # O_APPEND puts every write(2) at the current end, so concurrent
            # appenders never overwrite each other. The loop covers a short write.
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_APPEND, _FILE_MODE)
            try:
                view = memoryview(content)
                while view:
                    written = os.write(fd, view)
                    view = view[written:]
            finally:
                os.close(fd)

    def delete(self, path: str) -> None:
        with _translating_os_errors():
            try:
                self._resolve_path(path).unlink()
            except FileNotFoundError:
                raise ObjectNotFoundError(path) from None

    def list(self, prefix: str) -> Iterator[str]:
        with _translating_os_errors():
            # A root that is a file has no subdirectory to list, and that must
            # read as unavailable storage, not as an empty repository.
            self._verify_root_is_a_directory()
            prefix_path = self._resolve_path(prefix)
            # An absent directory lists nothing, and only the prefix directory
            # is walked. A root under a home directory has unrelated trees
            # beside it.
            if not prefix_path.is_dir():
                return iter([])
            matches = [
                entry.relative_to(self.root).as_posix()
                for entry in prefix_path.rglob("*")
                if entry.is_file()
            ]
            return iter(sorted(matches))


def open_from_path(location: str) -> RunRepository:
    """Open a repository whose store root is the directory at `location`."""
    return RunRepository(FileStore.from_path(location))
