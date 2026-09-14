"""Azure Blob Storage `ObjectStore` and the `azure://<account>/<container>[/<prefix>]`
URL scheme. Needs the `[azure]` extra. Authentication uses `DefaultAzureCredential`."""

import re
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from typing import Self
from urllib.parse import urlparse

from azure.core import MatchConditions
from azure.core.exceptions import (
    HttpResponseError,
    ResourceExistsError,
    ResourceNotFoundError,
    ServiceRequestError,
    ServiceResponseError,
)
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient, ContainerClient

from evaltrack.core.errors import RepositoryUnavailableError
from evaltrack.repositories.repository import RunRepository
from evaltrack.repositories.store import (
    CONNECTION_TIMEOUT,
    READ_TIMEOUT,
    ObjectNotFoundError,
    ObjectStore,
)


@contextmanager
def _translating_transport_errors() -> Generator[None]:
    """Re-raise a failed request as `RepositoryUnavailableError`.
    `ObjectNotFoundError` must be raised inside the block."""
    try:
        yield
    except (HttpResponseError, ServiceRequestError, ServiceResponseError) as exc:
        raise RepositoryUnavailableError(str(exc)) from exc


class AzureBlobStore(ObjectStore):
    """`ObjectStore` backed by Azure Blob Storage. `prefix` is a key prefix inside
    the container."""

    def __init__(self, container: ContainerClient, prefix: str = "") -> None:
        self._container = container
        self._prefix = (prefix.strip("/") + "/") if prefix.strip("/") else ""

    @classmethod
    def from_url(cls, url: str) -> Self:
        account, container_name, prefix = parse_azure_url(url)
        with _translating_transport_errors():
            service = BlobServiceClient(
                account_url=f"https://{account}.blob.core.windows.net",
                credential=DefaultAzureCredential(),
                connection_timeout=CONNECTION_TIMEOUT,
                read_timeout=READ_TIMEOUT,
            )
            container = service.get_container_client(container_name)
        return cls(container, prefix)

    def verify_available(self) -> None:
        with _translating_transport_errors():
            try:
                self._container.get_container_properties()
            except ResourceNotFoundError:
                raise ValueError(
                    f"container {self._container.container_name!r} does not exist "
                    f"in storage account {self._container.account_name!r}; "
                    "create it first"
                ) from None

    def _build_key(self, path: str) -> str:
        return self._prefix + path

    def read(self, path: str) -> bytes:
        with _translating_transport_errors():
            try:
                return self._container.download_blob(self._build_key(path)).readall()
            except ResourceNotFoundError:
                raise ObjectNotFoundError(path) from None

    def write(self, content: bytes, path: str) -> None:
        with _translating_transport_errors():
            blob = self._container.get_blob_client(self._build_key(path))
            blob.upload_blob(content, blob_type="BlockBlob", overwrite=True)

    def append(self, content: bytes, path: str) -> None:
        with _translating_transport_errors():
            blob = self._container.get_blob_client(self._build_key(path))
            # append_block is atomic for blocks up to 4 MiB.
            try:
                blob.append_block(content)
                return
            except ResourceNotFoundError:
                pass
            # Conditional (If-None-Match: *), because an unconditional create
            # re-initializes an existing append blob to zero length, and a writer
            # that loses the race then truncates the winner's entry.
            try:
                blob.create_append_blob(match_condition=MatchConditions.IfMissing)
            except ResourceExistsError:
                pass
            blob.append_block(content)

    def delete(self, path: str) -> None:
        with _translating_transport_errors():
            try:
                self._container.delete_blob(self._build_key(path))
            except ResourceNotFoundError:
                raise ObjectNotFoundError(path) from None

    def list(self, prefix: str) -> Iterator[str]:
        full = self._build_key(prefix)
        prefix_len = len(self._prefix)
        with _translating_transport_errors():
            # The SDK pages lazily, so a transport failure can arrive mid-iteration.
            for blob in self._container.list_blobs(name_starts_with=full):
                yield blob.name[prefix_len:]


# Azure's storage-account naming rule. Without the check a port or a typo fails
# as a DNS error at the derived endpoint, far from the mistake.
_ACCOUNT_NAME = re.compile(r"[a-z0-9]{3,24}")


def parse_azure_url(url: str) -> tuple[str, str, str]:
    """Split an `azure://account/container[/prefix]` URL into (account, container,
    prefix).

    Raises:
        ValueError: for a malformed URL. No message quotes the URL, which can
            carry a credential.
    """
    parsed = urlparse(url)
    if parsed.scheme != "azure":
        raise ValueError("AzureBlobStore expects an azure:// URL")
    account = parsed.netloc
    if not account:
        raise ValueError(
            "azure:// URL is missing the account. Use azure://<account>/<container>."
        )
    if "@" in account:
        # Before the account name is quoted below, so a password never reaches
        # the message.
        raise ValueError(
            "azure:// URL must not carry credentials. A username and password "
            "in the URL are not supported. Authentication uses "
            "DefaultAzureCredential."
        )
    if not _ACCOUNT_NAME.fullmatch(account):
        raise ValueError(
            f"azure:// URL has an invalid account name {account!r}: storage "
            "account names are 3-24 lowercase letters and digits. Credentials "
            "and ports in the URL are not supported. Authentication uses "
            "DefaultAzureCredential."
        )
    if parsed.query or parsed.fragment:
        # Otherwise a SAS token here is dropped without a word.
        raise ValueError(
            "azure:// URL must not carry a query or fragment. SAS tokens are "
            "not supported. Authentication uses DefaultAzureCredential "
            "(see https://github.com/jesrav/evaltrack/blob/main/docs/"
            "repositories.md#authentication)."
        )
    # urlparse keeps the leading slash on path.
    parts = parsed.path.lstrip("/").split("/", 1)
    if not parts or not parts[0]:
        raise ValueError(
            "azure:// URL is missing the container. Use azure://<account>/<container>."
        )
    container = parts[0]
    prefix = parts[1] if len(parts) > 1 else ""
    return account, container, prefix


def open_from_url(url: str) -> RunRepository:
    """Open a repository for an `azure://` URL."""
    return RunRepository(AzureBlobStore.from_url(url))
