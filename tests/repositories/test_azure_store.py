"""Azure-specific tests for `azure://` URL parsing and live store behavior.

The shared store contract is tested in `test_store_contract.py`,
including against live Azure. Only the tests marked `integration` here need
live storage. The URL parsing tests are pure functions.
"""

import re
from collections.abc import Callable
from typing import Any
from uuid import uuid4

import pytest
from azure.core import MatchConditions
from azure.core.exceptions import (
    AzureError,
    ClientAuthenticationError,
    HttpResponseError,
    ResourceExistsError,
    ResourceNotFoundError,
    ServiceRequestError,
)
from azure.storage.blob import BlobClient, ContainerClient

from evaltrack.core.errors import RepositoryUnavailableError
from evaltrack.repositories import azure as azure_module
from evaltrack.repositories.azure import AzureBlobStore, parse_azure_url
from evaltrack.repositories.store import ObjectNotFoundError

from .conftest import AZURE_TEST_ACCOUNT, AZURE_TEST_CONTAINER

# --- azure:// URL parsing ---


def test_parse_azure_url_basic() -> None:
    assert parse_azure_url("azure://acct/container") == ("acct", "container", "")


def test_parse_azure_url_with_prefix() -> None:
    assert parse_azure_url("azure://acct/container/runs/eval") == (
        "acct",
        "container",
        "runs/eval",
    )


def test_parse_azure_url_rejects_non_azure_scheme() -> None:
    with pytest.raises(ValueError, match="azure://"):
        parse_azure_url("https://example.com/x")


def test_parse_azure_url_requires_account() -> None:
    with pytest.raises(ValueError, match="account"):
        parse_azure_url("azure:///container")


def test_parse_azure_url_requires_container() -> None:
    with pytest.raises(ValueError, match="container"):
        parse_azure_url("azure://acct")


@pytest.mark.parametrize(
    "url",
    [
        "azure://acct:8080/container",  # port
        "azure://Acct/container",  # uppercase
        "azure://my.account/container",  # dot
        "azure://ab/container",  # shorter than Azure's 3-char minimum
    ],
)
def test_parse_azure_url_rejects_invalid_account(url: str) -> None:
    """Anything outside Azure's account-name rule, a port included, must fail
    here. A DNS or HTTP error at the derived endpoint lands far from the
    mistake."""
    with pytest.raises(ValueError, match="lowercase letters and digits"):
        parse_azure_url(url)


def test_parse_azure_url_rejects_credentials() -> None:
    with pytest.raises(ValueError, match="must not carry credentials"):
        parse_azure_url("azure://user:pass@acct/container")


def test_parse_azure_url_rejects_query() -> None:
    """A SAS token would otherwise be dropped without a word, and the user
    would fall back to ambient DefaultAzureCredential auth."""
    with pytest.raises(ValueError, match="SAS"):
        parse_azure_url("azure://acct/container?sv=2024&sig=abc")


def test_parse_azure_url_rejects_fragment() -> None:
    with pytest.raises(ValueError, match="query or fragment"):
        parse_azure_url("azure://acct/container#frag")


@pytest.mark.parametrize(
    ("url", "secret"),
    [
        ("azure://acct/container?sv=2024-01-01&sig=SUPERSECRETSIG=", "SUPERSECRETSIG"),
        ("azure://acct/container#SUPERSECRETSIG", "SUPERSECRETSIG"),
        ("azure://myuser:hunter2@acct/container", "hunter2"),
        ("https://myuser:hunter2@host/runs", "hunter2"),
        ("azure://myuser:hunter2@acct/container?sig=SUPERSECRETSIG=", "hunter2"),
    ],
)
def test_parse_azure_url_rejections_do_not_quote_the_url(url: str, secret: str) -> None:
    """A rejected URL can hold a SAS token or a password, so no rejection
    quotes it. The message names the fault instead."""
    with pytest.raises(ValueError) as excinfo:
        parse_azure_url(url)
    assert secret not in str(excinfo.value), "the rejection must not echo the secret"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("azure://", "azure://<account>/<container>"),
        ("azure://acct", "azure://<account>/<container>"),
    ],
)
def test_parse_azure_url_says_what_shape_to_use(url: str, expected: str) -> None:
    """The message no longer echoes the URL, so it must carry the shape. A
    user has nothing to compare their URL against otherwise."""
    with pytest.raises(ValueError, match=re.escape(expected)):
        parse_azure_url(url)


# --- verify_available error translation ---


class _MissingContainerClient:
    """Fake of the one container-client call `verify_available` makes, for a
    container that does not exist."""

    account_name = "evaltracktesting"
    container_name = "wip"

    def get_container_properties(self) -> object:
        raise ResourceNotFoundError("The specified container does not exist.")


def test_verify_available_names_account_and_container() -> None:
    """The SDK's ContainerNotFound must come out as a message a user can act
    on: which container is missing, in which account, and what to do."""
    store = AzureBlobStore(_MissingContainerClient())  # type: ignore[arg-type]
    with pytest.raises(
        ValueError,
        match=re.escape(
            "container 'wip' does not exist in storage account "
            "'evaltracktesting'; create it first"
        ),
    ):
        store.verify_available()


# --- transport error translation ---


class _FailingBlobClient:
    """Blob client whose every call fails with the configured SDK error."""

    def __init__(self, exc: AzureError) -> None:
        self._exc = exc

    def __getattr__(self, name: str) -> Callable[..., Any]:
        def call(*args: Any, **kwargs: Any) -> Any:
            raise self._exc

        return call


class _FailingContainerClient:
    """Container client whose every call, and every call on the blob clients it
    hands out, fails with the configured SDK error."""

    account_name = "acct"
    container_name = "runs"

    def __init__(self, exc: AzureError) -> None:
        self._exc = exc

    def get_blob_client(self, name: str) -> _FailingBlobClient:
        return _FailingBlobClient(self._exc)

    def __getattr__(self, name: str) -> Callable[..., Any]:
        def call(*args: Any, **kwargs: Any) -> Any:
            raise self._exc

        return call


def _make_store(exc: AzureError) -> AzureBlobStore:
    return AzureBlobStore(_FailingContainerClient(exc))  # type: ignore[arg-type]


# Every store operation that reaches the SDK, so the translation can be checked
# on each one.
_OPERATIONS: dict[str, Callable[[AzureBlobStore], object]] = {
    "verify_available": lambda store: store.verify_available(),
    "read": lambda store: store.read("runs/x.json"),
    "write": lambda store: store.write(b"x", "runs/x.json"),
    "append": lambda store: store.append(b"x\n", "refs/baseline.log.jsonl"),
    "delete": lambda store: store.delete("runs/x.json"),
    "list": lambda store: list(store.list("runs/")),
}


@pytest.mark.parametrize("operation", _OPERATIONS.values(), ids=_OPERATIONS.keys())
def test_transport_failure_is_translated(
    operation: Callable[[AzureBlobStore], object],
) -> None:
    """Every operation must raise the domain error, keep the SDK's message,
    and chain the original for debugging. An operation that leaves the SDK error
    untranslated pushes a raw traceback out to the surfaces."""
    exc = ServiceRequestError("connection refused")
    with pytest.raises(RepositoryUnavailableError) as raised:
        operation(_make_store(exc))
    assert "connection refused" in str(raised.value)
    assert raised.value.__cause__ is exc


def test_every_store_operation_is_covered() -> None:
    """The guard behind the parametrization above. A new operation that forgets
    the translation must fail a test rather than ship without a word."""
    exercised = set(_OPERATIONS) | {"from_url"}  # see the from_url test below
    assert {
        name for name in vars(AzureBlobStore) if not name.startswith("_")
    } == exercised, "a new operation must be added to _OPERATIONS"


@pytest.mark.parametrize(
    "exc",
    [
        ClientAuthenticationError("token expired: run `az login`"),
        ServiceRequestError("connection refused"),
        HttpResponseError("Operation returned an invalid status 'Forbidden'"),
    ],
    ids=["auth", "network", "http"],
)
def test_a_failed_request_reaches_the_caller_as_evaltracks_error(
    exc: AzureError,
) -> None:
    """Whatever the environment is broken in (credentials, network, an HTTP
    error), callers see evaltrack's exception, never the SDK's. The surfaces
    handle storage failures without knowing which backend they came from."""
    with pytest.raises(RepositoryUnavailableError) as raised:
        _make_store(exc).read("runs/x.json")
    assert not isinstance(raised.value, AzureError), (
        "callers must never see an SDK exception type"
    )
    assert str(exc) in str(raised.value)


def test_from_url_translates_a_credential_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The open of a store is a call site like any other. An environment with
    no usable credential must not push an SDK error out to the surfaces."""

    def _raise_no_credential() -> object:
        raise ClientAuthenticationError("no credential available in this environment")

    monkeypatch.setattr(azure_module, "DefaultAzureCredential", _raise_no_credential)
    with pytest.raises(RepositoryUnavailableError, match="no credential available"):
        AzureBlobStore.from_url("azure://acct/runs")


def test_missing_blob_still_reads_as_not_found() -> None:
    """A 404 is an SDK error too, so the translation must not swallow it. An
    absent blob is a valid read result, not a storage outage."""
    store = _make_store(ResourceNotFoundError("The specified blob does not exist."))
    with pytest.raises(ObjectNotFoundError):
        store.read("runs/x.json")


# --- live store behavior ---


@pytest.mark.integration
def test_from_url(azure_container: ContainerClient, azure_prefix: str) -> None:
    """The contract suite injects a container client, so this test exercises
    the real credential and transport construction path once. The trailing slash
    in the URL checks that a `.../container/prefix/` form does not produce
    double-slash keys."""
    store = AzureBlobStore.from_url(
        f"azure://{AZURE_TEST_ACCOUNT}/{AZURE_TEST_CONTAINER}/{azure_prefix}/"
    )
    store.write(b"hello", "a/b.txt")
    assert store.read("a/b.txt") == b"hello"
    assert [
        b.name for b in azure_container.list_blobs(name_starts_with=azure_prefix)
    ] == [f"{azure_prefix}/a/b.txt"], (
        "a trailing-slash prefix must not make double-slash keys"
    )


@pytest.mark.integration
def test_verify_available_missing_container_names_account_and_container() -> None:
    """A typo in the container name is the user mistake behind failures on
    every surface. Against live Azure, `verify_available` must translate the
    SDK's ContainerNotFound into a message that names the account and the
    container. The random name is never created, so there is nothing to clean
    up."""
    container = f"no-such-container-{uuid4().hex}"
    store = AzureBlobStore.from_url(f"azure://{AZURE_TEST_ACCOUNT}/{container}")
    with pytest.raises(
        ValueError,
        match=re.escape(
            f"container '{container}' does not exist in storage account "
            f"'{AZURE_TEST_ACCOUNT}'; create it first"
        ),
    ):
        store.verify_available()


# ---------- SDK premises behind append's conditional create ----------
#
# `AzureBlobStore.append` guards its create with If-None-Match: * precisely
# because of the behavior pinned here (azure-storage-blob 12.29.0). If either
# test starts failing on an SDK upgrade, re-evaluate whether the guard is
# still needed.


@pytest.mark.integration
def test_sdk_unconditional_create_truncates_an_existing_append_blob(
    azure_container: ContainerClient, azure_prefix: str
) -> None:
    key = f"{azure_prefix}/reflog"
    blob = azure_container.get_blob_client(key)
    blob.create_append_blob()
    blob.append_block(b"entry1\n")
    blob.create_append_blob()
    assert azure_container.download_blob(key).readall() == b""


@pytest.mark.integration
def test_sdk_conditional_create_raises_and_preserves_content(
    azure_container: ContainerClient, azure_prefix: str
) -> None:
    key = f"{azure_prefix}/reflog"
    blob = azure_container.get_blob_client(key)
    blob.create_append_blob(match_condition=MatchConditions.IfMissing)
    blob.append_block(b"entry1\n")
    with pytest.raises(ResourceExistsError):
        blob.create_append_blob(match_condition=MatchConditions.IfMissing)
    assert azure_container.download_blob(key).readall() == b"entry1\n"


# ---------- The reflog-creation race, deterministically ----------


@pytest.mark.integration
def test_append_create_race_loses_no_entry(
    azure_container: ContainerClient, azure_prefix: str
) -> None:
    """Replays the first-entry race. Writer A creates the reflog and appends
    between writer B's 404 and B's create. Both entries must survive."""
    writer_a = AzureBlobStore(azure_container, prefix=azure_prefix)

    class _RacingBlobClient:
        """Wraps the real blob client so that writer A wins the create race."""

        def __init__(self, inner: BlobClient) -> None:
            self._inner = inner

        def append_block(self, content: bytes) -> None:
            self._inner.append_block(content)

        def create_append_blob(
            self, *, match_condition: MatchConditions | None = None
        ) -> None:
            writer_a.append(b"entry-a\n", "reflog")
            self._inner.create_append_blob(match_condition=match_condition)

    class _RacingContainerView:
        """The real container as writer B sees it."""

        def __getattr__(self, name: str) -> object:
            return getattr(azure_container, name)

        def get_blob_client(self, name: str) -> _RacingBlobClient:
            return _RacingBlobClient(azure_container.get_blob_client(name))

    writer_b = AzureBlobStore(_RacingContainerView(), prefix=azure_prefix)  # type: ignore[arg-type]
    writer_b.append(b"entry-b\n", "reflog")

    assert writer_b.read("reflog") == b"entry-a\nentry-b\n"
