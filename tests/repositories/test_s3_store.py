"""S3-specific tests for `s3://` URL parsing, error translation, the append
retry loop and live store behavior.

The shared store contract is tested in `test_store_contract.py`, including
against live S3. Only the tests marked `integration` here need live storage.
The unit tests use an in-memory fake of the boto3 client that enforces the
same put preconditions as S3. The fake exists to drive the error and retry
paths. It never replaces the service in the contract suite.
"""

import hashlib
import io
import re
from collections.abc import Callable, Iterator
from typing import Any
from uuid import uuid4

import pytest

# Every name below comes from the optional `[s3]` extra. Without it this module
# skips instead of failing collection, so the rest of the suite still runs.
pytest.importorskip("boto3", reason="needs the [s3] extra")

from botocore.exceptions import (  # noqa: E402
    BotoCoreError,
    ClientError,
    EndpointConnectionError,
    NoCredentialsError,
)

from evaltrack.core.errors import RepositoryUnavailableError  # noqa: E402
from evaltrack.repositories.s3 import (  # noqa: E402
    S3Client,
    S3ObjectStore,
    parse_s3_url,
)
from evaltrack.repositories.store import ObjectNotFoundError  # noqa: E402

from ..conftest import S3_TEST_BUCKET  # noqa: E402

# --- s3:// URL parsing ---


def test_parse_s3_url_basic() -> None:
    assert parse_s3_url("s3://bucket") == ("bucket", "")


def test_parse_s3_url_with_prefix() -> None:
    assert parse_s3_url("s3://bucket/runs/eval") == ("bucket", "runs/eval")


def test_parse_s3_url_rejects_non_s3_scheme() -> None:
    with pytest.raises(ValueError, match="s3://"):
        parse_s3_url("https://example.com/x")


def test_parse_s3_url_requires_bucket() -> None:
    with pytest.raises(ValueError, match="bucket"):
        parse_s3_url("s3:///prefix")


@pytest.mark.parametrize(
    "url",
    [
        "s3://bucket:9000/evals",  # port
        "s3://Bucket/evals",  # uppercase
        "s3://my_bucket/evals",  # underscore
        "s3://ab/evals",  # shorter than S3's 3-char minimum
        "s3://-bucket/evals",  # leading hyphen
    ],
)
def test_parse_s3_url_rejects_invalid_bucket(url: str) -> None:
    """Anything outside the bucket-name rule of S3, a port included, must fail
    here. An endpoint error lands far from the mistake."""
    with pytest.raises(ValueError, match="lowercase letters, digits, dots and hyphens"):
        parse_s3_url(url)


def test_parse_s3_url_rejects_credentials() -> None:
    with pytest.raises(ValueError, match="must not carry credentials"):
        parse_s3_url("s3://user:pass@bucket/evals")


def test_parse_s3_url_rejects_query() -> None:
    """Otherwise the signature is dropped silently and the user falls back to
    the ambient credentials."""
    with pytest.raises(ValueError, match="Presigned"):
        parse_s3_url("s3://bucket/evals?X-Amz-Signature=abc")


def test_parse_s3_url_rejects_fragment() -> None:
    with pytest.raises(ValueError, match="query or fragment"):
        parse_s3_url("s3://bucket/evals#frag")


@pytest.mark.parametrize(
    ("url", "secret"),
    [
        ("s3://bucket/evals?X-Amz-Signature=SUPERSECRETSIG", "SUPERSECRETSIG"),
        ("s3://bucket/evals#SUPERSECRETSIG", "SUPERSECRETSIG"),
        ("s3://myuser:hunter2@bucket/evals", "hunter2"),
        ("https://myuser:hunter2@host/evals", "hunter2"),
        ("s3://myuser:hunter2@bucket/evals?X-Amz-Signature=SUPERSECRETSIG", "hunter2"),
    ],
)
def test_parse_s3_url_rejections_do_not_quote_the_url(url: str, secret: str) -> None:
    """A rejected URL can hold a signature or a password, so no rejection
    quotes it. The message names the fault instead."""
    with pytest.raises(ValueError) as excinfo:
        parse_s3_url(url)
    assert secret not in str(excinfo.value), "the rejection must not echo the secret"


@pytest.mark.parametrize("url", ["s3://", "s3:///evals"])
def test_parse_s3_url_says_what_shape_to_use(url: str) -> None:
    """The message never echoes the URL, so it must carry the shape."""
    with pytest.raises(ValueError, match=re.escape("s3://<bucket>[/<prefix>]")):
        parse_s3_url(url)


# --- an in-memory client with S3's conditional-put semantics ---


def _client_error(code: str, operation: str, status: int) -> ClientError:
    return ClientError(
        {
            "Error": {"Code": code, "Message": f"fake {code}"},
            "ResponseMetadata": {"HTTPStatusCode": status},
        },
        operation,
    )


class _FakeS3:
    """Enough of a boto3 S3 client for the store. Objects are keyed by name,
    ETags change with the content, and PutObject enforces the `If-Match` and
    `If-None-Match` preconditions that the append retry loop depends on."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def _etag(self, key: str) -> str:
        return '"' + hashlib.md5(self.objects[key]).hexdigest() + '"'

    def head_bucket(self, **kwargs: Any) -> Any:
        return {}

    def head_object(self, **kwargs: Any) -> Any:
        key = kwargs["Key"]
        if key not in self.objects:
            raise _client_error("404", "HeadObject", 404)
        return {"ETag": self._etag(key)}

    def get_object(self, **kwargs: Any) -> Any:
        key = kwargs["Key"]
        if key not in self.objects:
            raise _client_error("NoSuchKey", "GetObject", 404)
        return {"Body": io.BytesIO(self.objects[key]), "ETag": self._etag(key)}

    def put_object(self, **kwargs: Any) -> Any:
        key = kwargs["Key"]
        if kwargs.get("IfNoneMatch") == "*" and key in self.objects:
            raise _client_error("PreconditionFailed", "PutObject", 412)
        if "IfMatch" in kwargs:
            if key not in self.objects:
                raise _client_error("NoSuchKey", "PutObject", 404)
            if kwargs["IfMatch"] != self._etag(key):
                raise _client_error("PreconditionFailed", "PutObject", 412)
        self.objects[key] = bytes(kwargs["Body"])
        return {"ETag": self._etag(key)}

    def delete_object(self, **kwargs: Any) -> Any:
        self.objects.pop(kwargs["Key"], None)
        return {}

    def get_paginator(self, operation_name: str) -> Any:
        return _FakePaginator(self)


class _FakePaginator:
    def __init__(self, client: _FakeS3) -> None:
        self._client = client

    def paginate(self, **kwargs: Any) -> Iterator[dict[str, Any]]:
        prefix = kwargs["Prefix"]
        keys = sorted(k for k in self._client.objects if k.startswith(prefix))
        yield {"Contents": [{"Key": k} for k in keys]}


class _FailingS3:
    """Client whose every call fails with the configured error."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def __getattr__(self, name: str) -> Callable[..., Any]:
        def call(*args: Any, **kwargs: Any) -> Any:
            raise self._exc

        return call


def _make_store(exc: Exception) -> S3ObjectStore:
    # Delegation through __getattr__ satisfies the protocol at run time, not
    # structurally.
    return S3ObjectStore(_FailingS3(exc), "evals")  # type: ignore[arg-type]


# --- verify_available error translation ---


def test_verify_available_names_a_missing_bucket() -> None:
    """The 404 from HeadBucket must come out as a message that a user can act
    on. It names the missing bucket and says what to do."""
    store = _make_store(_client_error("404", "HeadBucket", 404))
    with pytest.raises(
        ValueError, match=re.escape("bucket 'evals' does not exist. Create it first.")
    ):
        store.verify_available()


def test_verify_available_reports_a_forbidden_bucket_as_unavailable() -> None:
    """A 403 means that the bucket can exist but the caller cannot see it.
    That is a credentials problem, not an error in the URL."""
    store = _make_store(_client_error("403", "HeadBucket", 403))
    with pytest.raises(RepositoryUnavailableError, match="403"):
        store.verify_available()


# --- transport error translation ---

# Every store operation that reaches the SDK, so the translation can be checked
# on each one.
_OPERATIONS: dict[str, Callable[[S3ObjectStore], object]] = {
    "verify_available": lambda store: store.verify_available(),
    "read": lambda store: store.read("runs/x.json"),
    "write": lambda store: store.write(b"x", "runs/x.json"),
    "append": lambda store: store.append(b"x\n", "refs/baseline.log.jsonl"),
    "delete": lambda store: store.delete("runs/x.json"),
    "list": lambda store: list(store.list("runs/")),
}


@pytest.mark.parametrize("operation", _OPERATIONS.values(), ids=_OPERATIONS.keys())
def test_transport_failure_is_translated(
    operation: Callable[[S3ObjectStore], object],
) -> None:
    """Every operation must raise the domain error, keep the message of the
    SDK, and chain the original error for debugging."""
    exc = EndpointConnectionError(endpoint_url="https://s3.example")
    with pytest.raises(RepositoryUnavailableError) as raised:
        operation(_make_store(exc))
    assert "s3.example" in str(raised.value)
    assert raised.value.__cause__ is exc


def test_every_store_operation_is_covered() -> None:
    """The guard behind the parametrization above. A new operation without the
    translation must fail a test instead of shipping unnoticed."""
    exercised = set(_OPERATIONS) | {"from_url"}  # see the live from_url test
    assert {
        name for name in vars(S3ObjectStore) if not name.startswith("_")
    } == exercised, "a new operation must be added to _OPERATIONS"


@pytest.mark.parametrize(
    "exc",
    [
        NoCredentialsError(),
        EndpointConnectionError(endpoint_url="https://s3.example"),
        _client_error("AccessDenied", "GetObject", 403),
    ],
    ids=["no-credentials", "network", "http"],
)
def test_a_failed_request_reaches_the_caller_as_evaltracks_error(
    exc: Exception,
) -> None:
    """Whatever is broken in the environment, callers see the evaltrack
    exception and never the SDK one. botocore has two unrelated error base
    classes, and both must be caught."""
    with pytest.raises(RepositoryUnavailableError) as raised:
        _make_store(exc).read("runs/x.json")
    assert not isinstance(raised.value, BotoCoreError | ClientError), (
        "callers must never see an SDK exception type"
    )
    assert str(exc) in str(raised.value)


def test_missing_object_still_reads_as_not_found() -> None:
    """A 404 is an SDK error too, so the translation must not swallow it."""
    store = S3ObjectStore(_FakeS3(), "evals")
    with pytest.raises(ObjectNotFoundError):
        store.read("runs/x.json")


def test_deleting_a_missing_object_raises_not_found() -> None:
    """S3 reports success for a delete of a missing object, so the store must
    detect the missing object itself to keep the contract."""
    store = S3ObjectStore(_FakeS3(), "evals")
    with pytest.raises(ObjectNotFoundError):
        store.delete("runs/x.json")


# --- the append retry loop ---


class _InterposingS3:
    """A client as one writer sees it. `before_first_put` runs between the
    read of that writer and its first put. It stands in for what another
    process did in that window. Later puts go straight through."""

    def __init__(self, inner: Any, before_first_put: Callable[[], None]) -> None:
        self._inner = inner
        self._before_first_put = before_first_put
        self.puts = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def put_object(self, **kwargs: Any) -> Any:
        self.puts += 1
        if self.puts == 1:
            self._before_first_put()
        return self._inner.put_object(**kwargs)


@pytest.mark.parametrize("pre_existing", [False, True], ids=["fresh-key", "existing"])
def test_append_that_loses_the_conditional_write_retries(pre_existing: bool) -> None:
    """Both entries must survive, whichever header the losing put carried."""
    fake = _FakeS3()
    writer_a = S3ObjectStore(fake, "evals")
    if pre_existing:
        writer_a.append(b"entry-0\n", "reflog")
    racing = _InterposingS3(fake, lambda: writer_a.append(b"entry-a\n", "reflog"))
    writer_b = S3ObjectStore(racing, "evals")  # type: ignore[arg-type]

    writer_b.append(b"entry-b\n", "reflog")

    expected = (b"entry-0\n" if pre_existing else b"") + b"entry-a\nentry-b\n"
    assert writer_b.read("reflog") == expected
    assert racing.puts == 2, "the lost put must be followed by exactly one retry"


def test_append_over_an_object_deleted_meanwhile_creates_it() -> None:
    """A delete between the read and the put makes the `If-Match` put miss
    the object. That is a lost race like any other. The writer reads again,
    finds nothing and creates the object."""
    fake = _FakeS3()
    other = S3ObjectStore(fake, "evals")
    other.append(b"entry-0\n", "reflog")
    racing = _InterposingS3(fake, lambda: other.delete("reflog"))
    writer = S3ObjectStore(racing, "evals")  # type: ignore[arg-type]

    writer.append(b"entry-b\n", "reflog")

    assert writer.read("reflog") == b"entry-b\n"
    assert racing.puts == 2


@pytest.mark.parametrize(
    "code", ["PreconditionFailed", "ConditionalRequestConflict", "NoSuchKey"]
)
def test_append_retries_every_lost_race_code(code: str) -> None:
    """S3 reports a lost conditional put under three codes. The 409 conflict
    cannot be caused on demand against the service, so each code is pinned
    here as a retry and not a failure."""
    fake = _FakeS3()

    def lose_once() -> None:
        raise _client_error(code, "PutObject", 409 if "Conflict" in code else 412)

    racing = _InterposingS3(fake, lose_once)
    store = S3ObjectStore(racing, "evals")  # type: ignore[arg-type]

    store.append(b"entry\n", "reflog")

    assert store.read("reflog") == b"entry\n"
    assert racing.puts == 2


def test_append_does_not_retry_an_unrelated_put_failure() -> None:
    """Only a lost race is worth a retry. A permissions failure must come back
    at once as an unavailable repository, not after all the attempts and with
    a message that blames another writer."""
    fake = _FakeS3()

    def deny() -> None:
        raise _client_error("AccessDenied", "PutObject", 403)

    racing = _InterposingS3(fake, deny)
    store = S3ObjectStore(racing, "evals")  # type: ignore[arg-type]

    with pytest.raises(RepositoryUnavailableError, match="AccessDenied"):
        store.append(b"entry\n", "reflog")
    assert racing.puts == 1, "a non-race failure must not be retried"
    assert not fake.objects


def test_append_gives_up_after_the_attempt_budget() -> None:
    """A writer that never wins must come back as an unavailable repository.
    It must not loop forever or raise a 412."""

    class _AlwaysLosing(_FakeS3):
        def put_object(self, **kwargs: Any) -> Any:
            raise _client_error("PreconditionFailed", "PutObject", 412)

    fake = _AlwaysLosing()
    store = S3ObjectStore(fake, "evals", append_attempts=3)
    with pytest.raises(RepositoryUnavailableError, match="another writer kept"):
        store.append(b"entry\n", "reflog")
    assert not fake.objects, "a lost append must land nothing"


# --- live store behavior ---


@pytest.mark.integration
def test_from_url(s3_client: S3Client, s3_prefix: str) -> None:
    """The contract suite injects a client, so this test exercises the real
    credential and transport construction path once. The trailing slash in
    the URL makes sure that a `.../prefix/` form does not make double-slash
    keys."""
    store = S3ObjectStore.from_url(f"s3://{S3_TEST_BUCKET}/{s3_prefix}/")
    store.write(b"hello", "a/b.txt")
    assert store.read("a/b.txt") == b"hello"
    listing = s3_client.get_paginator("list_objects_v2").paginate(
        Bucket=S3_TEST_BUCKET, Prefix=s3_prefix
    )
    assert [entry["Key"] for page in listing for entry in page["Contents"]] == [
        f"{s3_prefix}/a/b.txt"
    ], "a trailing-slash prefix must not make double-slash keys"


@pytest.mark.integration
def test_verify_available_missing_bucket_names_it() -> None:
    """A typo in the bucket name is the user mistake behind failures on every
    surface. Against live S3, `verify_available` must translate the 404 into
    a message that names the bucket. The random name is never created, so
    there is nothing to clean up."""
    bucket = f"no-such-bucket-{uuid4().hex}"
    store = S3ObjectStore.from_url(f"s3://{bucket}")
    with pytest.raises(
        ValueError,
        match=re.escape(f"bucket '{bucket}' does not exist. Create it first."),
    ):
        store.verify_available()


# ---------- What the append relies on from S3 ----------
#
# `append` is only correct if S3 refuses a put whose precondition no longer
# holds and leaves the object as it was. If one of these tests fails, the
# append is not safe with concurrent writers.


@pytest.mark.integration
def test_service_refuses_a_create_only_put_on_an_existing_key(
    s3_client: S3Client, s3_prefix: str
) -> None:
    key = f"{s3_prefix}/reflog"
    s3_client.put_object(Bucket=S3_TEST_BUCKET, Key=key, Body=b"entry1\n")
    with pytest.raises(ClientError) as raised:
        s3_client.put_object(
            Bucket=S3_TEST_BUCKET, Key=key, Body=b"entry2\n", IfNoneMatch="*"
        )
    assert raised.value.response["Error"]["Code"] == "PreconditionFailed"
    body = s3_client.get_object(Bucket=S3_TEST_BUCKET, Key=key)["Body"].read()
    assert body == b"entry1\n"


@pytest.mark.integration
def test_service_refuses_a_put_with_a_stale_etag(
    s3_client: S3Client, s3_prefix: str
) -> None:
    key = f"{s3_prefix}/reflog"
    stale = s3_client.put_object(Bucket=S3_TEST_BUCKET, Key=key, Body=b"entry1\n")[
        "ETag"
    ]
    s3_client.put_object(Bucket=S3_TEST_BUCKET, Key=key, Body=b"entry1\nentry2\n")
    with pytest.raises(ClientError) as raised:
        s3_client.put_object(
            Bucket=S3_TEST_BUCKET, Key=key, Body=b"entry1\nentry3\n", IfMatch=stale
        )
    assert raised.value.response["Error"]["Code"] == "PreconditionFailed"
    body = s3_client.get_object(Bucket=S3_TEST_BUCKET, Key=key)["Body"].read()
    assert body == b"entry1\nentry2\n"


@pytest.mark.integration
def test_service_answers_a_put_on_a_deleted_object_with_not_found(
    s3_client: S3Client, s3_prefix: str
) -> None:
    """An `If-Match` put on a deleted object gets a 404, not a 412. The append
    treats both as a lost race. This test pins which one arrives."""
    key = f"{s3_prefix}/reflog"
    etag = s3_client.put_object(Bucket=S3_TEST_BUCKET, Key=key, Body=b"entry1\n")[
        "ETag"
    ]
    s3_client.delete_object(Bucket=S3_TEST_BUCKET, Key=key)
    with pytest.raises(ClientError) as raised:
        s3_client.put_object(
            Bucket=S3_TEST_BUCKET, Key=key, Body=b"entry1\nentry2\n", IfMatch=etag
        )
    assert raised.value.response["Error"]["Code"] == "NoSuchKey"
    with pytest.raises(ClientError) as missing:
        s3_client.head_object(Bucket=S3_TEST_BUCKET, Key=key)
    assert missing.value.response["Error"]["Code"] == "404", (
        "the refused put must not have recreated the object"
    )


# ---------- The append races, deterministically ----------


@pytest.mark.integration
@pytest.mark.parametrize("pre_existing", [False, True], ids=["fresh-key", "existing"])
def test_append_race_loses_no_entry(
    s3_client: S3Client, s3_prefix: str, pre_existing: bool
) -> None:
    """Replays a lost append against the live service. Writer A appends
    between the read of writer B and its conditional put. The key is new in
    one case and existing in the other, so both headers are exercised. Both
    entries must survive."""
    writer_a = S3ObjectStore(s3_client, S3_TEST_BUCKET, prefix=s3_prefix)
    if pre_existing:
        writer_a.append(b"entry-0\n", "reflog")
    racing = _InterposingS3(s3_client, lambda: writer_a.append(b"entry-a\n", "reflog"))
    writer_b = S3ObjectStore(racing, S3_TEST_BUCKET, prefix=s3_prefix)  # type: ignore[arg-type]

    writer_b.append(b"entry-b\n", "reflog")

    expected = (b"entry-0\n" if pre_existing else b"") + b"entry-a\nentry-b\n"
    assert writer_b.read("reflog") == expected
    assert racing.puts == 2
