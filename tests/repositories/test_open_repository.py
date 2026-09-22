"""How `open_repository` decides between a directory path and a URL.

The backends themselves are covered in `test_file_store.py`,
`test_azure_store.py`, `test_s3_store.py` and `test_databricks_store.py`.
"""

import pytest

from evaltrack.repositories import open_repository


@pytest.mark.parametrize("location", ["azure:/acct/evals", "azure:acct/evals"])
def test_a_url_missing_a_slash_is_refused(location: str) -> None:
    """One dropped slash used to leave the scheme branch and open a directory
    named `azure:` instead. The push then exited 0 while the shared remote
    never saw the run."""
    with pytest.raises(ValueError, match="missing '://'"):
        open_repository(location)


def test_the_rejection_quotes_only_the_scheme() -> None:
    """Everything past the ':' can be a SAS URL whose query string is a live
    credential, so no message may carry it."""
    with pytest.raises(ValueError) as raised:
        open_repository("azure:/acct/evals?sig=SUPERSECRETSIG=")
    assert "SUPERSECRETSIG" not in str(raised.value)


@pytest.mark.parametrize(
    "location", ["evals", "./.evaltrack", "../shared", "~/evals", "/srv/evals"]
)
def test_a_path_still_opens(location: str) -> None:
    """The rule reads the first path segment only, so every ordinary path form
    stays on the path branch. None of these has to exist: opening a repository
    reaches no storage."""
    open_repository(location)


def test_an_azure_url_reaches_the_azure_backend() -> None:
    """A well-formed URL opens, and one only the azure backend can judge is
    refused by it. The path branch would take both and write to a directory."""
    open_repository("azure://account/container")

    with pytest.raises(ValueError, match="missing the container"):
        open_repository("azure://account")


def test_an_s3_url_reaches_the_s3_backend() -> None:
    open_repository("s3://bucket/evals")

    with pytest.raises(ValueError, match="invalid bucket name"):
        open_repository("s3://Bucket/evals")


def test_a_databricks_url_reaches_the_databricks_backend() -> None:
    open_repository("databricks://main/default/evals")

    with pytest.raises(ValueError, match="missing the schema or the volume"):
        open_repository("databricks://main/default")


def test_an_unknown_scheme_names_the_known_ones() -> None:
    with pytest.raises(
        ValueError,
        match=(
            "unknown repository scheme: 'ftp' "
            "\\(known schemes: azure, databricks, s3\\)"
        ),
    ):
        open_repository("ftp://host/evals")
