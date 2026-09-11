"""Fakes that tests inject in place of a real object."""

import threading
from collections.abc import Iterator

import pytest

from evaltrack.repositories import RunRepository
from evaltrack.repositories.store import ObjectNotFoundError, ObjectStore


class MemoryStore(ObjectStore):
    """In-memory `ObjectStore`, for tests that need a repository and no disk."""

    def __init__(self) -> None:
        self._files: dict[str, bytes] = {}
        self._append_lock = threading.Lock()

    def read(self, path: str) -> bytes:
        try:
            return self._files[path]
        except KeyError:
            raise ObjectNotFoundError(path) from None

    def write(self, content: bytes, path: str) -> None:
        self._files[path] = content

    def append(self, content: bytes, path: str) -> None:
        with self._append_lock:
            self._files[path] = self._files.get(path, b"") + content

    def delete(self, path: str) -> None:
        try:
            del self._files[path]
        except KeyError:
            raise ObjectNotFoundError(path) from None

    def list(self, prefix: str) -> Iterator[str]:
        return iter(sorted(p for p in self._files if p.startswith(prefix)))


class RaisingStore(ObjectStore):
    """Fake `ObjectStore` whose every operation raises the configured exception.

    Stands in for a backend that fails at run time, from bad credentials, a
    network outage, or an HTTP error.
    """

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def read(self, path: str) -> bytes:
        raise self._exc

    def write(self, content: bytes, path: str) -> None:
        raise self._exc

    def append(self, content: bytes, path: str) -> None:
        raise self._exc

    def delete(self, path: str) -> None:
        raise self._exc

    def list(self, prefix: str) -> Iterator[str]:
        raise self._exc


def mount_fake_azure(monkeypatch: pytest.MonkeyPatch, store: ObjectStore) -> str:
    """Serve a repository over `store` behind the `azure://` scheme, and return a
    URL on it. The real scheme dispatch runs, only the store is faked."""

    def open_from_url(url: str) -> RunRepository:
        return RunRepository(store)

    # pytester restores `sys.modules`, so the module must be re-imported here
    # rather than looked up by name, or a stale copy gets patched.
    import evaltrack.repositories.azure as azure

    monkeypatch.setattr(azure, "open_from_url", open_from_url)
    return "azure://x"
