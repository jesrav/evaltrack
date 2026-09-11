"""Shared fixtures. Report builders live in `factories.py`, fakes in `fakes.py`.

The azure SDK is imported inside the fixtures that need it, never at module
scope. This file is the suite's root conftest, so a module-scope import would
make the whole suite uncollectable without the optional `[azure]` extra, and
`just test` would be the only way to run any test at all.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

if TYPE_CHECKING:
    from azure.storage.blob import ContainerClient

    from evaltrack.repositories.azure import AzureBlobStore

# Dedicated account and container for live testing. Per-test prefixes keep
# concurrent runs apart. A test that uses these fixtures must carry the
# `integration` marker.
AZURE_TEST_ACCOUNT = "evaltracktesting"
AZURE_TEST_CONTAINER = "evaltrack-integration-tests"


@pytest.fixture(scope="session")
def azure_container() -> ContainerClient:
    """Session-scoped so the whole run shares one credential. A per-test
    credential would repeat the token exchange for every test."""
    from azure.identity import DefaultAzureCredential
    from azure.storage.blob import BlobServiceClient

    service = BlobServiceClient(
        account_url=f"https://{AZURE_TEST_ACCOUNT}.blob.core.windows.net",
        credential=DefaultAzureCredential(),
    )
    return service.get_container_client(AZURE_TEST_CONTAINER)


@pytest.fixture
def azure_prefix(azure_container: ContainerClient) -> Iterator[str]:
    """Unique per-test key prefix inside the shared container. The fixture
    deletes it on teardown, so concurrent or aborted runs do not collide."""
    prefix = f"itest-{uuid4().hex}"
    yield prefix
    for blob in azure_container.list_blobs(name_starts_with=prefix + "/"):
        azure_container.delete_blob(blob.name)


@pytest.fixture
def azure_store_factory(
    azure_container: ContainerClient, azure_prefix: str
) -> Callable[[], AzureBlobStore]:
    """StoreFactory for the contract suite's `azure` parametrization.

    Injects the shared container client. For the `from_url` construction path,
    see `repositories/test_azure_store.py`.
    """
    from evaltrack.repositories.azure import AzureBlobStore

    return lambda: AzureBlobStore(azure_container, prefix=azure_prefix)


@pytest.fixture(autouse=True)
def _clear_evaltrack_env(  # pyright: ignore[reportUnusedFunction]
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Start every test from a clean `EVALTRACK_*` slate.

    Suite-wide rather than per-module. A session that pytester spawns reads the
    real environment, so an exported `EVALTRACK_REMOTE` fails the precedence
    tests and acts on that real store. A test that needs one of these variables
    sets it with monkeypatch.
    """
    for key in list(os.environ):
        if key.startswith("EVALTRACK_"):
            monkeypatch.delenv(key)
