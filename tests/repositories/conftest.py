"""Backend parametrization for the repository tests, and the live cloud
fixtures it looks up.

The whole suite runs through these fixtures, so every backend (file, live
Azure, live S3 and a live Databricks volume) holds the same behavior. The
cloud parametrizations run against real storage and carry the `integration`
marker, so `just test` skips them.

There is no fake of a cloud SDK here, on purpose. A hand-written stand-in for
Azure once passed an append-race guarantee that the real backend did not
provide. The S3 append is only correct if the service enforces its
preconditions, and only the service can show that.
"""

import os
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import cast
from uuid import uuid4

import boto3
import pytest
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient, ContainerClient
from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import NotFound

from evaltrack.repositories import RunRepository
from evaltrack.repositories.azure import AzureBlobStore
from evaltrack.repositories.databricks import DatabricksVolumeStore, FilesClient
from evaltrack.repositories.file import FileStore
from evaltrack.repositories.s3 import S3Client, S3ObjectStore
from evaltrack.repositories.store import ObjectStore

from .helpers import RepositoryFactory, StoreFactory

# Dedicated account and container for live testing. Per-test prefixes keep
# concurrent runs apart. A test that uses these fixtures must carry the
# `integration` marker.
AZURE_TEST_ACCOUNT = "evaltracktesting"
AZURE_TEST_CONTAINER = "evaltrack-integration-tests"


@pytest.fixture(scope="session")
def azure_container() -> ContainerClient:
    """Session-scoped so the whole run shares one credential. A per-test
    credential would repeat the token exchange for every test."""
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
    return cast(S3Client, boto3.Session().client("s3", region_name=S3_TEST_REGION))


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
    return lambda: S3ObjectStore(s3_client, S3_TEST_BUCKET, prefix=s3_prefix)


# There is no maintainer workspace for Databricks, so the volume for live
# testing is named by whoever runs the tests, as `/Volumes/<catalog>/<schema>/
# <volume>`. The workspace and credentials come from Databricks unified
# authentication. Unset, the `databricks` parametrization skips.
DATABRICKS_TEST_VOLUME_ENV = "EVALTRACK_DATABRICKS_TEST_VOLUME"


@pytest.fixture(scope="session")
def databricks_volume() -> str:
    volume = os.environ.get(DATABRICKS_TEST_VOLUME_ENV)
    if not volume:
        pytest.skip(f"{DATABRICKS_TEST_VOLUME_ENV} names no volume to test against")
    return volume.rstrip("/")


@pytest.fixture(scope="session")
def databricks_files(databricks_volume: str) -> FilesClient:
    """Session-scoped so the whole run resolves credentials once."""
    return WorkspaceClient(product="evaltrack-tests").files


def delete_databricks_tree(files: FilesClient, directory: str) -> None:
    """Delete `directory` and everything under it. The API deletes only an
    empty directory, so the tree is walked. A missing directory is a no-op."""
    try:
        entries = list(files.list_directory_contents(directory))
    except NotFound:
        return
    for entry in entries:
        path = f"{directory}/{entry.name}"
        if entry.is_directory:
            delete_databricks_tree(files, path)
        else:
            files.delete(path)
    files.delete_directory(directory)


@pytest.fixture
def databricks_prefix(
    databricks_files: FilesClient, databricks_volume: str
) -> Iterator[str]:
    """Unique per-test directory inside the shared volume, deleted on teardown."""
    prefix = f"itest-{uuid4().hex}"
    yield prefix
    delete_databricks_tree(databricks_files, f"{databricks_volume}/{prefix}")


@pytest.fixture
def databricks_store_factory(
    databricks_files: FilesClient, databricks_volume: str, databricks_prefix: str
) -> Callable[[], DatabricksVolumeStore]:
    """StoreFactory for the contract suite's `databricks` parametrization.
    Injects the shared client. For the `from_url` construction path, see
    `repositories/test_databricks_store.py`."""
    return lambda: DatabricksVolumeStore(
        databricks_files, databricks_volume, prefix=databricks_prefix
    )


@pytest.fixture(
    params=[
        "file",
        pytest.param("azure", marks=pytest.mark.integration),
        pytest.param("s3", marks=pytest.mark.integration),
        pytest.param("databricks", marks=pytest.mark.integration),
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
