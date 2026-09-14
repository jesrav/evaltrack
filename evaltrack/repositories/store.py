"""The `ObjectStore` ABC and its exceptions. A store raises
`RepositoryUnavailableError` when it cannot serve a request. Anything else means
it was used the wrong way and reaches the caller unchanged."""

from abc import ABC, abstractmethod
from collections.abc import Iterator

# Fixed here so an SDK default cannot change them and every remote behaves the
# same. `READ_TIMEOUT` is how long to wait for the next bytes, not for the whole
# download, so a large object on a slow connection still completes.
CONNECTION_TIMEOUT = 20
READ_TIMEOUT = 120


class ObjectNotFoundError(Exception):
    """Raised by `ObjectStore.read` and `delete` when the path does not exist.

    Deliberately outside the `EvaltrackError` hierarchy: a missing object means
    nothing until something says what it was looking for.
    """


class ObjectStore(ABC):
    """Bytes keyed by path. A key belongs to the mode that created it. A store can
    refuse `write` on an appended key and `append` on a written one."""

    def verify_available(self) -> None:
        """Cheaply check that the storage can serve requests. The default is a no-op."""
        return

    def verify_writable(self) -> None:  # noqa: B027 (deliberate concrete no-op)
        """Cheaply check, without network I/O, that a write can succeed."""

    @abstractmethod
    def read(self, path: str) -> bytes:
        """Read a whole object. Raises `ObjectNotFoundError` when the path does
        not exist."""
        ...

    @abstractmethod
    def write(self, content: bytes, path: str) -> None:
        """Write a whole object, replacing one already at the path. The publish is
        atomic, so a reader sees the old bytes or the new ones, never a mix."""
        ...

    @abstractmethod
    def append(self, content: bytes, path: str) -> None:
        """Append to an object, creating it if absent. One call must be atomic
        for `content` within the store's atomic-append limit."""
        ...

    @abstractmethod
    def delete(self, path: str) -> None:
        """Delete one object. Raises `ObjectNotFoundError` when the path does not
        exist."""
        ...

    @abstractmethod
    def list(self, prefix: str) -> Iterator[str]:
        """Yield every path under `prefix`, a directory prefix ending in `/`. The
        empty prefix yields every path."""
        ...
