"""Backend parametrization for the repository tests.

The whole suite runs through these fixtures, so every backend (file and live
Azure) holds the same behavior. The `azure` parametrization runs
against real Azure storage and carries the `integration` marker, so
`just test` skips it.

There is no fake of the Azure SDK, on purpose. A hand-written stand-in for it
once passed an append-race guarantee that the real backend did not provide.
"""

from pathlib import Path

import pytest

from evaltrack.repositories import RunRepository
from evaltrack.repositories.file import FileStore
from evaltrack.repositories.store import ObjectStore

from .helpers import RepositoryFactory, StoreFactory


@pytest.fixture(params=["file", pytest.param("azure", marks=pytest.mark.integration)])
def store_factory(request: pytest.FixtureRequest, tmp_path: Path) -> StoreFactory:
    if request.param == "azure":
        # Lazy lookup, so a non-integration run never touches credentials.
        return request.getfixturevalue("azure_store_factory")

    def make_store() -> ObjectStore:
        return FileStore(tmp_path / "store")

    return make_store


@pytest.fixture
def repository_factory(store_factory: StoreFactory) -> RepositoryFactory:
    return lambda: RunRepository(store_factory())
