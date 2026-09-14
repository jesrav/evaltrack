"""`RunRepository`, the object stores it keeps runs and refs in, what happens to
a run after it is recorded, and the dispatch that opens a repository."""

from evaltrack.config import names_a_url
from evaltrack.repositories.lifecycle import (
    PromoteResult,
    RefDeletion,
    delete_ref_and_orphaned_runs,
    delete_run_if_unreferenced,
    promote,
)
from evaltrack.repositories.repository import RunRepository, RunSummary

# What a caller binds. The repository `open_repository` hands back, the
# operations that act on it, and the types those return.
__all__ = [
    "PromoteResult",
    "RefDeletion",
    "RunRepository",
    "RunSummary",
    "delete_ref_and_orphaned_runs",
    "delete_run_if_unreferenced",
    "open_repository",
    "promote",
]

# All backend URL schemes for the messages that list them.
_KNOWN_SCHEMES = ("azure", "s3")
_KNOWN_SCHEMES_TEXT = "known schemes: " + ", ".join(_KNOWN_SCHEMES)


def open_repository(location: str) -> RunRepository:
    """Open a `RunRepository` for `location`, a directory path, or a URL with a
    scheme. This does not check that the storage exists or can be reached,
    `verify_available` does.

    Raises:
        ValueError: for a URL whose scheme no backend claims, or one written
            without its `://`.
        ImportError: when a built-in scheme's optional dependency is absent.
    """
    if "://" not in location:
        # A URL with a dropped slash. Opened as a path it would quietly create
        # that directory here instead of reaching the storage it names. Only the
        # part up to the ':' is quoted, since the rest can carry a credential.
        if names_a_url(location):
            scheme_prefix, colon, _ = location.partition(":")
            raise ValueError(
                f"repository URL {scheme_prefix + colon!r} is missing '://' "
                f"({_KNOWN_SCHEMES_TEXT}). A directory path must have no ':' in "
                "its first segment."
            )
        import evaltrack.repositories.file as file_repository

        return file_repository.open_from_path(location)
    scheme = location.split("://", 1)[0]
    # Each scheme's module is imported on use, because every remote needs an
    # optional extra.
    match scheme:
        case "azure":
            try:
                import evaltrack.repositories.azure as azure_repository
            except ImportError as exc:
                raise ImportError(
                    "the 'azure' repository requires the `evaltrack[azure]` extra "
                    f"({exc})"
                ) from exc
            return azure_repository.open_from_url(location)
        case "s3":
            try:
                import evaltrack.repositories.s3 as s3_repository
            except ImportError as exc:
                raise ImportError(
                    f"the 's3' repository requires the `evaltrack[s3]` extra ({exc})"
                ) from exc
            return s3_repository.open_from_url(location)
        case _:
            raise ValueError(
                f"unknown repository scheme: {scheme!r} ({_KNOWN_SCHEMES_TEXT})"
            )
