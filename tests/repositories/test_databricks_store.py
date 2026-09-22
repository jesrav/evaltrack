"""Databricks-specific tests for `databricks://` URL parsing, error
translation, the one-file-per-append layout and live store behavior.

The shared store contract is tested in `test_store_contract.py`, including
against a live volume when `EVALTRACK_DATABRICKS_TEST_VOLUME` names one. Only
the tests marked `integration` here need live storage.

The unit tests use an in-memory fake of the Files API client. Unlike the S3
and Azure fakes, this one also drives the store's own layout logic, because
the append guarantee here rests on unique file names rather than on a
precondition only the service can enforce. What the fake assumes about the
service is written down in its docstring, and the integration tests at the
bottom check each assumption against the live API.
"""

import io
import re
import threading
from collections.abc import Callable, Iterator
from typing import Any, BinaryIO
from uuid import uuid4

import pytest
import requests
from databricks.sdk.errors import (
    AlreadyExists,
    BadRequest,
    InternalError,
    NotFound,
    PermissionDenied,
)
from databricks.sdk.errors.base import DatabricksError
from databricks.sdk.service.files import DirectoryEntry, DownloadResponse

from evaltrack.core.errors import RepositoryUnavailableError
from evaltrack.repositories import databricks as databricks_module
from evaltrack.repositories.databricks import (
    DatabricksVolumeStore,
    FilesClient,
    parse_databricks_url,
)
from evaltrack.repositories.store import ObjectNotFoundError

from .conftest import delete_databricks_tree
from .helpers import race

# --- databricks:// URL parsing ---


def test_parse_databricks_url_basic() -> None:
    assert parse_databricks_url("databricks://main/default/evals") == (
        "/Volumes/main/default/evals",
        "",
    )


def test_parse_databricks_url_with_prefix() -> None:
    assert parse_databricks_url("databricks://main/default/evals/team/project") == (
        "/Volumes/main/default/evals",
        "team/project",
    )


def test_parse_databricks_url_rejects_non_databricks_scheme() -> None:
    with pytest.raises(ValueError, match="databricks://"):
        parse_databricks_url("https://example.com/x")


def test_parse_databricks_url_requires_catalog() -> None:
    with pytest.raises(ValueError, match="missing the catalog"):
        parse_databricks_url("databricks:///default/evals")


@pytest.mark.parametrize(
    "url",
    [
        "databricks://main",
        "databricks://main/default",
        "databricks://main/default/",
        "databricks://main//evals",
    ],
)
def test_parse_databricks_url_requires_schema_and_volume(url: str) -> None:
    with pytest.raises(ValueError, match="missing the schema or the volume"):
        parse_databricks_url(url)


def test_parse_databricks_url_explains_a_volumes_path() -> None:
    """The path a user copies from the workspace starts with `/Volumes/`, and
    pasted after the scheme it has no catalog where the URL wants one."""
    with pytest.raises(ValueError, match="without the /Volumes/ part"):
        parse_databricks_url("databricks:///Volumes/main/default/evals")


@pytest.mark.parametrize(
    ("url", "kind"),
    [
        ("databricks://main:443/default/evals", "catalog"),  # port
        ("databricks://my.catalog/default/evals", "catalog"),  # dot
        ("databricks://main/de fault/evals", "schema"),  # space
        ("databricks://main/default/my.volume", "volume"),  # dot
    ],
)
def test_parse_databricks_url_rejects_invalid_names(url: str, kind: str) -> None:
    """A port or a workspace host in the URL lands here, not at the API."""
    with pytest.raises(ValueError, match=f"invalid {kind} name"):
        parse_databricks_url(url)


def test_parse_databricks_url_rejects_credentials() -> None:
    with pytest.raises(ValueError, match="must not carry credentials"):
        parse_databricks_url("databricks://user:token@main/default/evals")


def test_parse_databricks_url_rejects_query() -> None:
    with pytest.raises(ValueError, match="must not carry a query or fragment"):
        parse_databricks_url("databricks://main/default/evals?token=x")


def test_parse_databricks_url_rejects_fragment() -> None:
    with pytest.raises(ValueError, match="must not carry a query or fragment"):
        parse_databricks_url("databricks://main/default/evals#x")


@pytest.mark.parametrize(
    ("url", "secret"),
    [
        ("databricks://user:SUPERSECRET@main/default/evals", "SUPERSECRET"),
        ("databricks://main/default/evals?token=SUPERSECRET", "SUPERSECRET"),
        ("databricks://main/default#SUPERSECRET", "SUPERSECRET"),
    ],
)
def test_parse_databricks_url_rejections_do_not_quote_the_url(
    url: str, secret: str
) -> None:
    with pytest.raises(ValueError) as raised:
        parse_databricks_url(url)
    assert secret not in str(raised.value)


# --- an in-memory Files API ---


class _FakeFiles:
    """In-memory Files API client.

    What it assumes about the service, each checked live below: directories
    are explicit, and an upload into a directory that does not exist is
    `NotFound`; a file operation on a directory path answers `NotFound`; an
    upload without `overwrite` onto an existing file is refused; listing a
    missing directory is `NotFound`; deleting a directory with content is
    refused. Entries are listed in reverse name order, so nothing may rely on
    the order the service happens to use.
    """

    def __init__(self, volume: str) -> None:
        self.files: dict[str, bytes] = {}
        self.directories: set[str] = {volume}
        self._lock = threading.Lock()

    def upload(
        self, file_path: str, contents: BinaryIO, *, overwrite: bool | None = None
    ) -> None:
        with self._lock:
            if file_path in self.directories:
                raise BadRequest(f"{file_path} is a directory")
            if _parent(file_path) not in self.directories:
                raise NotFound(f"no directory at {_parent(file_path)}")
            if file_path in self.files and overwrite is False:
                raise AlreadyExists(f"{file_path} exists")
            self.files[file_path] = contents.read()

    def download(self, file_path: str) -> DownloadResponse:
        with self._lock:
            if file_path not in self.files:
                raise NotFound(f"no file at {file_path}")
            data = self.files[file_path]
        return DownloadResponse(content_length=len(data), contents=io.BytesIO(data))

    def delete(self, file_path: str) -> None:
        with self._lock:
            if file_path not in self.files:
                raise NotFound(f"no file at {file_path}")
            del self.files[file_path]

    def list_directory_contents(self, directory_path: str) -> Iterator[DirectoryEntry]:
        with self._lock:
            if directory_path not in self.directories:
                raise NotFound(f"no directory at {directory_path}")
            entries = [
                DirectoryEntry(name=path.rsplit("/", 1)[1], is_directory=False)
                for path in self.files
                if _parent(path) == directory_path
            ] + [
                DirectoryEntry(name=path.rsplit("/", 1)[1], is_directory=True)
                for path in self.directories
                if _parent(path) == directory_path
            ]
        return iter(sorted(entries, key=lambda e: e.name or "", reverse=True))

    def create_directory(self, directory_path: str) -> None:
        with self._lock:
            if directory_path in self.files:
                raise BadRequest(f"{directory_path} is a file")
            while directory_path not in self.directories:
                self.directories.add(directory_path)
                directory_path = _parent(directory_path)

    def delete_directory(self, directory_path: str) -> None:
        with self._lock:
            if directory_path not in self.directories:
                raise NotFound(f"no directory at {directory_path}")
            if any(
                _parent(p) == directory_path for p in set(self.files) | self.directories
            ):
                raise BadRequest(f"{directory_path} is not empty")
            self.directories.remove(directory_path)

    def get_directory_metadata(self, directory_path: str) -> None:
        with self._lock:
            if directory_path not in self.directories:
                raise NotFound(f"no directory at {directory_path}")


def _parent(path: str) -> str:
    return path.rsplit("/", 1)[0]


_VOLUME = "/Volumes/main/default/evals"


@pytest.fixture
def fake_files() -> _FakeFiles:
    return _FakeFiles(_VOLUME)


@pytest.fixture
def store(fake_files: _FakeFiles) -> DatabricksVolumeStore:
    return DatabricksVolumeStore(fake_files, _VOLUME, prefix="project")


# --- the one-file-per-append layout ---


def test_write_into_a_fresh_directory_then_read(
    store: DatabricksVolumeStore, fake_files: _FakeFiles
) -> None:
    """Nothing under the prefix exists before the first write, so the write
    must bring the directories with it."""
    store.write(b"hello", "runs/a.json")
    assert store.read("runs/a.json") == b"hello"
    assert fake_files.files == {f"{_VOLUME}/project/runs/a.json": b"hello"}


def test_read_missing_raises_not_found(store: DatabricksVolumeStore) -> None:
    with pytest.raises(ObjectNotFoundError):
        store.read("runs/missing.json")


def test_appends_read_back_joined_in_order(store: DatabricksVolumeStore) -> None:
    store.append(b"one\n", "refs/main.log.jsonl")
    store.append(b"two\n", "refs/main.log.jsonl")
    store.append(b"three\n", "refs/main.log.jsonl")
    assert store.read("refs/main.log.jsonl") == b"one\ntwo\nthree\n"


def test_appends_keep_their_order_when_the_clock_stands_still(
    store: DatabricksVolumeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The order of the parts is the order of the calls, not the clock's.
    A clock that reads the same twice, or steps back, must not reorder a
    reflog, since its last entry is the ref's target."""
    monkeypatch.setattr(databricks_module.time, "time_ns", lambda: 1_000)
    for i in range(20):
        store.append(f"{i}\n".encode(), "refs/main.log.jsonl")
    assert store.read("refs/main.log.jsonl").decode().split() == [
        str(i) for i in range(20)
    ]


def test_concurrent_appends_lose_no_entry(store: DatabricksVolumeStore) -> None:
    """Every writer creates its own file, so no writer can overwrite another
    and none has to retry."""
    writers = 8
    race(
        *[
            lambda i=i: store.append(f"entry-{i}\n".encode(), "log.jsonl")
            for i in range(writers)
        ]
    )
    lines = store.read("log.jsonl").decode().splitlines()
    assert sorted(lines) == sorted(f"entry-{i}" for i in range(writers))


def test_list_shows_an_appended_object_as_one_key(
    store: DatabricksVolumeStore,
) -> None:
    """The parts are the store's own business. A listing names the key, once,
    beside the written objects, in key order."""
    store.write(b"1", "runs/b.json")
    store.write(b"2", "runs/a.json")
    store.append(b"x\n", "refs/baseline.log.jsonl")
    store.append(b"y\n", "refs/baseline.log.jsonl")
    store.append(b"z\n", "refs/pr/1.log.jsonl")
    assert list(store.list("")) == [
        "refs/baseline.log.jsonl",
        "refs/pr/1.log.jsonl",
        "runs/a.json",
        "runs/b.json",
    ]
    assert list(store.list("refs/")) == [
        "refs/baseline.log.jsonl",
        "refs/pr/1.log.jsonl",
    ]


def test_list_of_a_missing_prefix_is_empty(store: DatabricksVolumeStore) -> None:
    store.write(b"1", "runs/a.json")
    assert list(store.list("refs/")) == []


def test_delete_removes_every_part(
    store: DatabricksVolumeStore, fake_files: _FakeFiles
) -> None:
    """A delete must leave nothing behind, or the next append would revive
    the old entries under the same key."""
    store.append(b"x\n", "refs/main.log.jsonl")
    store.append(b"y\n", "refs/main.log.jsonl")
    store.delete("refs/main.log.jsonl")
    with pytest.raises(ObjectNotFoundError):
        store.read("refs/main.log.jsonl")
    assert fake_files.files == {}
    assert f"{_VOLUME}/project/refs/main.log.jsonl" not in fake_files.directories
    store.append(b"z\n", "refs/main.log.jsonl")
    assert store.read("refs/main.log.jsonl") == b"z\n"


def test_delete_missing_raises_not_found(store: DatabricksVolumeStore) -> None:
    with pytest.raises(ObjectNotFoundError):
        store.delete("refs/missing.log.jsonl")


def test_a_plain_directory_is_not_an_object(store: DatabricksVolumeStore) -> None:
    """A key that names a directory of written objects is no object itself,
    so it neither reads nor deletes, and `runs` never masks `runs/a.json`."""
    store.write(b"1", "runs/a.json")
    with pytest.raises(ObjectNotFoundError):
        store.read("runs")
    with pytest.raises(ObjectNotFoundError):
        store.delete("runs")
    assert store.read("runs/a.json") == b"1"


def test_replacing_an_appended_object_takes_a_delete_first(
    store: DatabricksVolumeStore,
) -> None:
    store.append(b"first\n", "log.jsonl")
    store.delete("log.jsonl")
    store.write(b"replaced\n", "log.jsonl")
    assert store.read("log.jsonl") == b"replaced\n"


def test_write_over_an_appended_key_is_refused(store: DatabricksVolumeStore) -> None:
    """A key belongs to the mode that created it. The write cannot land as a
    file where the parts directory is, and it must not vanish either."""
    store.append(b"first\n", "log.jsonl")
    with pytest.raises(RepositoryUnavailableError):
        store.write(b"replaced\n", "log.jsonl")
    assert store.read("log.jsonl") == b"first\n"


def test_verify_available_names_a_missing_volume() -> None:
    store = DatabricksVolumeStore(_FakeFiles(_VOLUME), "/Volumes/main/default/wip")
    with pytest.raises(
        ValueError,
        match=re.escape(
            "volume '/Volumes/main/default/wip' does not exist. Create it first."
        ),
    ):
        store.verify_available()


def test_verify_available_accepts_a_missing_prefix(
    store: DatabricksVolumeStore,
) -> None:
    """The volume is what an admin creates. The prefix is the store's own."""
    store.verify_available()


# --- transport error translation ---


class _FailingFiles:
    """Files client whose every call fails with the configured error."""

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    def __getattr__(self, name: str) -> Callable[..., Any]:
        def call(*args: Any, **kwargs: Any) -> Any:
            raise self._exc

        return call


def _make_store(exc: BaseException) -> DatabricksVolumeStore:
    return DatabricksVolumeStore(_FailingFiles(exc), _VOLUME)  # type: ignore[arg-type]


# Every store operation that reaches the SDK, so the translation can be checked
# on each one.
_OPERATIONS: dict[str, Callable[[DatabricksVolumeStore], object]] = {
    "verify_available": lambda store: store.verify_available(),
    "read": lambda store: store.read("runs/x.json"),
    "write": lambda store: store.write(b"x", "runs/x.json"),
    "append": lambda store: store.append(b"x\n", "refs/baseline.log.jsonl"),
    "delete": lambda store: store.delete("runs/x.json"),
    "list": lambda store: list(store.list("runs/")),
}


@pytest.mark.parametrize("operation", _OPERATIONS.values(), ids=_OPERATIONS.keys())
def test_transport_failure_is_translated(
    operation: Callable[[DatabricksVolumeStore], object],
) -> None:
    """Every operation must raise the domain error, keep the SDK's message,
    and chain the original for debugging."""
    exc = requests.ConnectionError("connection refused")
    with pytest.raises(RepositoryUnavailableError) as raised:
        operation(_make_store(exc))
    assert "connection refused" in str(raised.value)
    assert raised.value.__cause__ is exc


def test_every_store_operation_is_covered() -> None:
    """The guard behind the parametrization above. A new operation that forgets
    the translation must fail a test rather than ship without a word."""
    exercised = set(_OPERATIONS) | {"from_url"}  # see the from_url test below
    assert {
        name for name in vars(DatabricksVolumeStore) if not name.startswith("_")
    } == exercised, "a new operation must be added to _OPERATIONS"


@pytest.mark.parametrize(
    "exc",
    [
        PermissionDenied("token expired: run `databricks auth login`"),
        requests.ConnectionError("connection refused"),
        TimeoutError("Timed out after 0:02:00"),
        InternalError("Unexpected error"),
    ],
    ids=["auth", "network", "retries-exhausted", "http"],
)
def test_a_failed_request_reaches_the_caller_as_evaltracks_error(
    exc: BaseException,
) -> None:
    """Whatever the environment is broken in, callers see evaltrack's
    exception, never the SDK's or the transport's."""
    with pytest.raises(RepositoryUnavailableError) as raised:
        _make_store(exc).read("runs/x.json")
    assert not isinstance(raised.value, DatabricksError | requests.RequestException), (
        "callers must never see an SDK exception type"
    )
    assert str(exc) in str(raised.value)


def test_from_url_reaches_no_workspace(monkeypatch: pytest.MonkeyPatch) -> None:
    """Opening a repository reads no configuration and reaches no network, on
    this backend as on the others. The SDK does both when its client is built,
    so the build waits for the first call that needs it."""

    def _fail_the_build(**kwargs: Any) -> object:
        raise AssertionError("the workspace client was built on open")

    # The SDK's configuration is where credentials resolve, before the client.
    monkeypatch.setattr(databricks_module, "Config", _fail_the_build)
    monkeypatch.setattr(databricks_module, "WorkspaceClient", _fail_the_build)
    DatabricksVolumeStore.from_url("databricks://main/default/evals")


def test_from_url_translates_a_credential_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The SDK reports a workspace it cannot configure as a `ValueError`. That
    must not reach the surfaces as a bare error, and it must not be remembered,
    since the fix is a login the user can do while the process runs."""
    attempts = 0

    def _raise_no_credential(**kwargs: Any) -> object:
        nonlocal attempts
        attempts += 1
        raise ValueError("default auth: cannot configure default credentials")

    monkeypatch.setattr(databricks_module, "Config", _raise_no_credential)
    store = DatabricksVolumeStore.from_url("databricks://main/default/evals")
    for _ in range(2):
        with pytest.raises(
            RepositoryUnavailableError, match="cannot configure default"
        ):
            store.verify_available()
    assert attempts == 2, "a failed build must be retried on the next call"


def test_missing_file_still_reads_as_not_found() -> None:
    """A 404 is an SDK error too, so the translation must not swallow it. An
    absent file is a valid read result, not a storage outage."""
    store = _make_store(NotFound("The file being accessed is not found."))
    with pytest.raises(ObjectNotFoundError):
        store.read("runs/x.json")


# --- live store behavior ---


@pytest.mark.integration
def test_from_url(
    databricks_files: FilesClient, databricks_volume: str, databricks_prefix: str
) -> None:
    """The contract suite injects a client, so this test exercises the real
    credential and transport construction path once. The trailing slash in
    the URL checks that a `.../volume/prefix/` form does not produce
    double-slash paths."""
    store = DatabricksVolumeStore.from_url(
        f"databricks://{databricks_volume.removeprefix('/Volumes/')}/{databricks_prefix}/"
    )
    store.write(b"hello", "a/b.txt")
    assert store.read("a/b.txt") == b"hello"
    assert [
        entry.name
        for entry in databricks_files.list_directory_contents(
            f"{databricks_volume}/{databricks_prefix}/a"
        )
    ] == ["b.txt"]


@pytest.mark.integration
def test_verify_available_missing_volume_names_it(databricks_volume: str) -> None:
    """A typo in the volume name is the user mistake behind failures on every
    surface. Against the live API, `verify_available` must translate the
    SDK's answer into a message that names the volume."""
    missing = f"{databricks_volume}-{uuid4().hex}"
    store = DatabricksVolumeStore.from_url(
        f"databricks://{missing.removeprefix('/Volumes/')}"
    )
    with pytest.raises(ValueError, match=re.escape(missing)):
        store.verify_available()


# The assumptions the fake makes about the service, one test each. A failure
# here means the fake, and the store's handling, need to follow the service.


@pytest.mark.integration
def test_sdk_listing_a_missing_directory_is_not_found(
    databricks_files: FilesClient, databricks_volume: str, databricks_prefix: str
) -> None:
    with pytest.raises(NotFound):
        list(
            databricks_files.list_directory_contents(
                f"{databricks_volume}/{databricks_prefix}/missing"
            )
        )


@pytest.mark.integration
def test_sdk_file_operations_on_a_directory_are_refused(
    databricks_files: FilesClient, databricks_volume: str, databricks_prefix: str
) -> None:
    """The store takes a `NotFound` or a `BadRequest` on a download or a
    delete as "no file here" and looks for an appended object instead. Any
    other answer would make every reflog unreadable."""
    directory = f"{databricks_volume}/{databricks_prefix}/dir"
    databricks_files.create_directory(directory)
    with pytest.raises((NotFound, BadRequest)):
        databricks_files.download(directory)
    with pytest.raises((NotFound, BadRequest)):
        databricks_files.delete(directory)


@pytest.mark.integration
def test_sdk_upload_without_overwrite_refuses_an_existing_file(
    databricks_files: FilesClient, databricks_volume: str, databricks_prefix: str
) -> None:
    """Not something the store relies on, since every part has a fresh name,
    but the guard behind that: a name reused by mistake fails loudly instead
    of replacing another writer's entry."""
    path = f"{databricks_volume}/{databricks_prefix}/a.txt"
    databricks_files.create_directory(_parent(path))
    databricks_files.upload(path, io.BytesIO(b"first"), overwrite=False)
    with pytest.raises(DatabricksError):
        databricks_files.upload(path, io.BytesIO(b"second"), overwrite=False)
    with databricks_files.download(path).contents or io.BytesIO() as stream:
        assert stream.read() == b"first"


@pytest.mark.integration
def test_sdk_upload_into_a_missing_directory(
    databricks_files: FilesClient, databricks_volume: str, databricks_prefix: str
) -> None:
    """Whether an upload creates the directories above it is undocumented.
    The store handles both answers, and this records which one the service
    gives: it either succeeds or answers `NotFound`."""
    path = f"{databricks_volume}/{databricks_prefix}/new/a.txt"
    try:
        databricks_files.upload(path, io.BytesIO(b"x"), overwrite=True)
    except NotFound:
        pytest.skip("uploads do not create parent directories; the store does")
    assert [
        entry.name for entry in databricks_files.list_directory_contents(_parent(path))
    ] == ["a.txt"]


@pytest.mark.integration
def test_sdk_delete_directory_refuses_content(
    databricks_files: FilesClient, databricks_volume: str, databricks_prefix: str
) -> None:
    """`delete` removes the parts before the directory. If the service
    removed a directory with content, a concurrent append could vanish
    without a word, and this test would say so."""
    directory = f"{databricks_volume}/{databricks_prefix}/dir"
    databricks_files.create_directory(directory)
    databricks_files.upload(f"{directory}/a.txt", io.BytesIO(b"x"), overwrite=True)
    with pytest.raises(DatabricksError):
        databricks_files.delete_directory(directory)
    delete_databricks_tree(databricks_files, directory)
    with pytest.raises(NotFound):
        list(databricks_files.list_directory_contents(directory))
