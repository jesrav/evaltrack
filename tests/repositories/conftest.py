"""Backend parametrization for the repository tests, and the live cloud
fixtures it looks up.

The whole suite runs through these fixtures, so every backend (file, live
Azure and live S3) holds the same behavior. The cloud parametrizations run
against real storage and carry the `integration` marker, so `just test` skips
them.

There is no fake of a cloud SDK here, on purpose. A hand-written stand-in for
Azure once passed an append-race guarantee that the real backend did not
provide. The S3 append is only correct if the service enforces its
preconditions, and only the service can show that.

The cloud SDKs are imported inside the fixtures that need them, never at
module scope, so the folder stays collectable without the optional extras.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING, cast
from uuid import uuid4

import pytest

from evaltrack.repositories import RunRepository
from evaltrack.repositories.file import FileStore
from evaltrack.repositories.store import ObjectStore

from .helpers import RepositoryFactory, StoreFactory

if TYPE_CHECKING:
    from azure.storage.blob import ContainerClient

    from evaltrack.repositories.azure import AzureBlobStore
    from evaltrack.repositories.s3 import S3Client, S3ObjectStore

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


# Dedicated AWS bucket for live testing.
# The region is named so a CI job needs no AWS_REGION. Per-test prefixes keep
# concurrent runs apart. A lifecycle rule on the bucket deletes what an aborted
# run leaves behind after one day.
S3_TEST_BUCKET = "evaltrack-integration-tests"
S3_TEST_REGION = "eu-north-1"


@pytest.fixture(scope="session")
def s3_client() -> S3Client:
    """Session-scoped so the whole run looks up credentials once."""
    import boto3

    return cast("S3Client", boto3.Session().client("s3", region_name=S3_TEST_REGION))


@pytest.fixture
def s3_prefix(s3_client: S3Client) -> Iterator[str]:
    """Unique per-test key prefix inside the shared bucket, deleted on teardown."""
    prefix = f"itest-{uuid4().hex}"
    yield prefix
    paginator = s3_client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=S3_TEST_BUCKET, Prefix=prefix + "/"):
        for entry in page.get("Contents", []):
            s3_client.delete_object(Bucket=S3_TEST_BUCKET, Key=entry["Key"])


@pytest.fixture
def s3_store_factory(
    s3_client: S3Client, s3_prefix: str
) -> Callable[[], S3ObjectStore]:
    """StoreFactory for the contract suite's `s3` parametrization. Injects the
    shared client. For the `from_url` construction path, see
    `repositories/test_s3_store.py`."""
    from evaltrack.repositories.s3 import S3ObjectStore

    return lambda: S3ObjectStore(s3_client, S3_TEST_BUCKET, prefix=s3_prefix)


@pytest.fixture(
    params=[
        "file",
        pytest.param("azure", marks=pytest.mark.integration),
        pytest.param("s3", marks=pytest.mark.integration),
    ]
)
def store_factory(request: pytest.FixtureRequest, tmp_path: Path) -> StoreFactory:
    if request.param != "file":
        # Lazy lookup, so a non-integration run never touches credentials.
        return request.getfixturevalue(f"{request.param}_store_factory")

    def make_store() -> ObjectStore:
        return FileStore(tmp_path / "store")

    return make_store


@pytest.fixture
def repository_factory(store_factory: StoreFactory) -> RepositoryFactory:
    return lambda: RunRepository(store_factory())
