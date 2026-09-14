"""Backend parametrization for the repository tests.

The whole suite runs through these fixtures, so every backend (file, live
Azure and live S3) holds the same behavior. The cloud parametrizations run
against real storage and carry the `integration` marker, so `just test` skips
them.

There is no fake of a cloud SDK here, on purpose. A hand-written stand-in for
Azure once passed an append-race guarantee that the real backend did not
provide. The S3 append is only correct if the service enforces its
preconditions, and only the service can show that.
"""

from pathlib import Path

import pytest

from evaltrack.repositories import RunRepository
from evaltrack.repositories.file import FileStore
from evaltrack.repositories.store import ObjectStore

from .helpers import RepositoryFactory, StoreFactory


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
