"""Databricks Unity Catalog volume `ObjectStore` and the
`databricks://<catalog>/<schema>/<volume>[/<prefix>]` URL scheme. Needs the
`[databricks]` extra. Authentication is Databricks unified authentication, so
`DATABRICKS_HOST` with a token, a `~/.databrickscfg` profile, OAuth
machine-to-machine credentials and a notebook's own session all work. The
workspace is not part of the URL. It comes from that configuration.
"""

import io
import re
import threading
import time
import uuid
from collections.abc import Generator, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
from typing import Any, BinaryIO, Protocol, Self, cast
from urllib.parse import urlparse

from databricks.sdk import WorkspaceClient
from databricks.sdk.core import Config
from databricks.sdk.errors import BadRequest, NotFound
from databricks.sdk.service.files import DirectoryEntry, DownloadResponse

from evaltrack.core.errors import RepositoryUnavailableError
from evaltrack.repositories.repository import RunRepository
from evaltrack.repositories.store import READ_TIMEOUT, ObjectNotFoundError, ObjectStore

# The suffix of the files an appended object is made of. Only `append` writes
# under a key, so a directory holding one of these is an appended object.
_PART_SUFFIX = ".part"
# Downloads of one appended object's parts run side by side, so reading a long
# reflog does not pay one round trip per entry.
_PART_DOWNLOAD_THREADS = 8

# A file operation on a path that is a directory is not documented, so both
# the status a missing file gets and the one a bad path gets are taken as "not
# a file here". The integration tests check the live answer.
_NOT_A_FILE = (NotFound, BadRequest)


class FilesClient(Protocol):
    """The calls the store makes on `WorkspaceClient.files`. The type a fake
    implements."""

    def upload(
        self, file_path: str, contents: BinaryIO, *, overwrite: bool | None = None
    ) -> Any: ...
    def download(self, file_path: str) -> DownloadResponse: ...
    def delete(self, file_path: str) -> Any: ...
    def list_directory_contents(
        self, directory_path: str
    ) -> Iterator[DirectoryEntry]: ...
    def create_directory(self, directory_path: str) -> Any: ...
    def delete_directory(self, directory_path: str) -> Any: ...
    def get_directory_metadata(self, directory_path: str) -> Any: ...


@contextmanager
def _translating_transport_errors() -> Generator[None]:
    """Re-raise a failed request as `RepositoryUnavailableError`.
    `ObjectNotFoundError` must be raised inside the block."""
    try:
        yield
    # Every SDK error is an `OSError`: `DatabricksError` subclasses it, the
    # `requests` exceptions do, and the retry budget running out raises
    # `TimeoutError`.
    except OSError as exc:
        raise RepositoryUnavailableError(str(exc)) from exc


class _LazyFilesClient:
    """Builds the workspace client on first use. The SDK resolves credentials
    and fetches the workspace's metadata when the client is built, and opening
    a repository does neither on any backend. A failed build is not
    remembered, so the next call tries again."""

    def __init__(self) -> None:
        self._files: FilesClient | None = None
        self._lock = threading.Lock()

    def _resolve(self) -> FilesClient:
        with self._lock:
            if self._files is None:
                try:
                    # The one timeout the SDK has covers the connect and each
                    # read, so it takes the longer of the two. The retry
                    # budget is capped so an unreachable workspace fails in
                    # minutes, not the SDK's default.
                    client = WorkspaceClient(
                        config=Config(
                            product="evaltrack",
                            http_timeout_seconds=READ_TIMEOUT,
                            retry_timeout_seconds=READ_TIMEOUT,
                        )
                    )
                # Unresolvable credentials are a `ValueError` from the SDK.
                except (OSError, ValueError) as exc:
                    raise RepositoryUnavailableError(
                        f"cannot open the Databricks workspace: {exc}"
                    ) from exc
                self._files = client.files
            return self._files

    def __getattr__(self, name: str) -> Any:
        return getattr(self._resolve(), name)


_stamp_lock = threading.Lock()
_last_stamp = 0


def _next_part_name() -> str:
    """A name that sorts after every part this process named before, whatever
    the clock does. Across processes the order is the wall clock's, and the
    random tail keeps two writers in the same nanosecond apart."""
    global _last_stamp
    with _stamp_lock:
        stamp = max(time.time_ns(), _last_stamp + 1)
        _last_stamp = stamp
    return f"{stamp:020d}-{uuid.uuid4().hex}{_PART_SUFFIX}"


def _parent(path: str) -> str:
    return path.rsplit("/", 1)[0]


class DatabricksVolumeStore(ObjectStore):
    """`ObjectStore` backed by a Unity Catalog volume, through the Files API.
    `volume` is the `/Volumes/<catalog>/<schema>/<volume>` path and `prefix`
    a directory inside it.

    A written object is one file. The Files API cannot append to a file or
    make a write conditional, so an appended object is a directory at the key
    instead, holding one file per `append` in a name that sorts by the time of
    the call. `read` joins the files in that order, `list` shows the directory
    as one key, and `delete` removes it whole. A key is a file or such a
    directory, never both, which is how each key keeps to the mode that
    created it.
    """

    def __init__(self, files: FilesClient, volume: str, *, prefix: str = "") -> None:
        self._files = files
        self._volume = volume.rstrip("/")
        prefix = prefix.strip("/")
        self._root = f"{self._volume}/{prefix}" if prefix else self._volume

    @classmethod
    def from_url(cls, url: str) -> Self:
        volume, prefix = parse_databricks_url(url)
        return cls(cast(FilesClient, _LazyFilesClient()), volume, prefix=prefix)

    def verify_available(self) -> None:
        """A missing prefix directory is fine. The first write creates it."""
        with _translating_transport_errors():
            try:
                self._files.get_directory_metadata(self._volume)
            except NotFound:
                raise ValueError(
                    f"volume {self._volume!r} does not exist. Create it first."
                ) from None

    def _build_path(self, path: str) -> str:
        return f"{self._root}/{path}"

    def _relative(self, full: str) -> str:
        return full[len(self._root) + 1 :]

    def _download(self, full: str) -> bytes:
        response = self._files.download(full)
        if response.contents is None:
            return b""
        with closing(response.contents) as stream:
            return stream.read()

    def _upload(self, full: str, content: bytes, *, overwrite: bool) -> None:
        try:
            self._files.upload(full, io.BytesIO(content), overwrite=overwrite)
        except NotFound:
            # Whether an upload creates the directories above it is not
            # documented, so a miss is taken as a missing parent once.
            self._files.create_directory(_parent(full))
            self._files.upload(full, io.BytesIO(content), overwrite=overwrite)

    def _list_parts(self, full: str) -> list[str] | None:
        """The part files of the appended object at `full`, in append order,
        or `None` when no appended object is there."""
        try:
            entries = list(self._files.list_directory_contents(full))
        except NotFound:
            return None
        parts = sorted(
            f"{full}/{entry.name}"
            for entry in entries
            if entry.name is not None
            and not entry.is_directory
            and entry.name.endswith(_PART_SUFFIX)
        )
        return parts or None

    def read(self, path: str) -> bytes:
        full = self._build_path(path)
        with _translating_transport_errors():
            try:
                return self._download(full)
            except _NOT_A_FILE as exc:
                parts = self._list_parts(full)
                if parts is None:
                    if isinstance(exc, NotFound):
                        raise ObjectNotFoundError(path) from None
                    raise
            with ThreadPoolExecutor(
                max_workers=min(_PART_DOWNLOAD_THREADS, len(parts))
            ) as pool:
                return b"".join(pool.map(self._download, parts))

    def write(self, content: bytes, path: str) -> None:
        with _translating_transport_errors():
            self._upload(self._build_path(path), content, overwrite=True)

    def append(self, content: bytes, path: str) -> None:
        with _translating_transport_errors():
            # A fresh name every call, so two writers never touch the same
            # file and nothing has to be read first.
            self._upload(
                f"{self._build_path(path)}/{_next_part_name()}",
                content,
                overwrite=False,
            )

    def delete(self, path: str) -> None:
        full = self._build_path(path)
        with _translating_transport_errors():
            try:
                self._files.delete(full)
                return
            except _NOT_A_FILE as exc:
                parts = self._list_parts(full)
                if parts is None:
                    if isinstance(exc, NotFound):
                        raise ObjectNotFoundError(path) from None
                    raise
            for part in parts:
                try:
                    self._files.delete(part)
                except NotFound:
                    # Another delete got there first. The directory is still
                    # this call's to remove.
                    pass
            self._files.delete_directory(full)

    def list(self, prefix: str) -> Iterator[str]:
        directory = self._build_path(prefix.rstrip("/")) if prefix else self._root
        with _translating_transport_errors():
            # Collected in full, then sorted, since the API lists one
            # directory at a time in an order it does not promise.
            return iter(sorted(self._relative(full) for full in self._walk(directory)))

    def _walk(self, directory: str) -> Iterator[str]:
        """Every file under `directory`, with an appended object given as its
        directory. A missing directory holds nothing."""
        try:
            entries = list(self._files.list_directory_contents(directory))
        except NotFound:
            return
        names = [entry.name for entry in entries if entry.name is not None]
        if any(name.endswith(_PART_SUFFIX) for name in names):
            yield directory
            return
        for entry in entries:
            if entry.name is None:
                continue
            full = f"{directory}/{entry.name}"
            if entry.is_directory:
                yield from self._walk(full)
            else:
                yield full


# Unity Catalog lets a name carry more than this, but a name outside it in a
# URL is far more likely a typo, a port or a path in the wrong shape.
_OBJECT_NAME = re.compile(r"[A-Za-z0-9_-]+")
_URL_SHAPE = "databricks://<catalog>/<schema>/<volume>[/<prefix>]"


def parse_databricks_url(url: str) -> tuple[str, str]:
    """Split a `databricks://catalog/schema/volume[/prefix]` URL into the
    volume's `/Volumes/...` path and the prefix.

    Raises:
        ValueError: for a malformed URL. No message quotes the URL, which can
            carry a credential.
    """
    parsed = urlparse(url)
    if parsed.scheme != "databricks":
        raise ValueError("DatabricksVolumeStore expects a databricks:// URL")
    if "@" in parsed.netloc:
        # Before any name is quoted below, so a token never reaches the message.
        raise ValueError(
            "databricks:// URL must not carry credentials. A username and "
            "password in the URL are not supported. Authentication uses "
            "Databricks unified authentication."
        )
    if parsed.query or parsed.fragment:
        raise ValueError(
            "databricks:// URL must not carry a query or fragment. "
            "Authentication uses Databricks unified authentication (see "
            "https://github.com/jesrav/evaltrack/blob/main/docs/"
            "repositories.md#authentication-2)."
        )
    if not parsed.netloc:
        if parsed.path.startswith("/Volumes/"):
            raise ValueError(
                f"databricks:// URL names the volume as a path. Use {_URL_SHAPE}, "
                "without the /Volumes/ part."
            )
        raise ValueError(f"databricks:// URL is missing the catalog. Use {_URL_SHAPE}.")
    # urlparse keeps the leading slash on path.
    parts = parsed.path.lstrip("/").split("/", 2)
    names = [parsed.netloc, *parts[:2]]
    if len(names) < 3 or not all(names):
        raise ValueError(
            f"databricks:// URL is missing the schema or the volume. Use {_URL_SHAPE}."
        )
    for kind, name in zip(("catalog", "schema", "volume"), names, strict=True):
        if not _OBJECT_NAME.fullmatch(name):
            raise ValueError(
                f"databricks:// URL has an invalid {kind} name {name!r}: letters, "
                "digits, `_` and `-` only. The workspace is not part of the URL. "
                "It comes from the Databricks configuration."
            )
    catalog, schema, volume = names
    prefix = parts[2] if len(parts) > 2 else ""
    return f"/Volumes/{catalog}/{schema}/{volume}", prefix


def open_from_url(url: str) -> RunRepository:
    """Open a repository for a `databricks://` URL."""
    return RunRepository(DatabricksVolumeStore.from_url(url))
