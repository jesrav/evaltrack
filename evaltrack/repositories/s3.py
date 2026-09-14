"""Amazon S3 `ObjectStore` and the `s3://<bucket>[/<prefix>]` URL scheme. Needs
the `[s3]` extra. Authentication uses the default credential chain of boto3, so
a profile, the standard `AWS_*` variables, a web identity token and an instance
role all work. Only Amazon S3 is supported.
"""

import random
import re
import time
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Protocol, Self, cast
from urllib.parse import urlparse

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from evaltrack.core.errors import RepositoryUnavailableError
from evaltrack.repositories.repository import RunRepository
from evaltrack.repositories.store import (
    CONNECTION_TIMEOUT,
    READ_TIMEOUT,
    ObjectNotFoundError,
    ObjectStore,
)

# How many times `append` tries before it gives up. Each lost put means that
# another writer won.
_APPEND_ATTEMPTS = 8
# The longest pause before a retry, in seconds. The pause is random and grows
# with each attempt, so writers that collide spread out.
_RETRY_JITTER = 0.05

# The error codes of a conditional put that lost to another writer.
# `PreconditionFailed` is the 412 for both headers. `ConditionalRequestConflict`
# is a 409 for two conditional puts that overlapped. `NoSuchKey` is an
# `If-Match` put on an object that was deleted after the read.
_LOST_CONDITIONAL_WRITE = frozenset(
    {"PreconditionFailed", "ConditionalRequestConflict", "NoSuchKey"}
)
# GetObject reports a missing object as `NoSuchKey`. A HEAD request has no
# body, so the client only sees the status code.
_NOT_FOUND = frozenset({"NoSuchKey", "NotFound", "404"})

# `SystemRandom` so the pause does not use or change the global random state
# of the process.
_jitter = random.SystemRandom()


class S3Client(Protocol):
    """The calls that the store makes on a boto3 S3 client. boto3 has no client
    type to import, so this is the type that a fake implements."""

    def head_bucket(self, **kwargs: Any) -> Any: ...
    def head_object(self, **kwargs: Any) -> Any: ...
    def get_object(self, **kwargs: Any) -> Any: ...
    def put_object(self, **kwargs: Any) -> Any: ...
    def delete_object(self, **kwargs: Any) -> Any: ...
    def get_paginator(self, operation_name: str) -> Any: ...


@dataclass(frozen=True)
class _Snapshot:
    """An object as one read saw it. The ETag identifies that version. A later
    conditional put uses it to make sure that the object has not changed."""

    content: bytes
    etag: str


def _error_code(exc: ClientError) -> str:
    return str(exc.response.get("Error", {}).get("Code", ""))


@contextmanager
def _translating_transport_errors() -> Generator[None]:
    """Re-raise a failed request as `RepositoryUnavailableError`.
    `ObjectNotFoundError` must be raised inside the block."""
    try:
        yield
    except (BotoCoreError, ClientError) as exc:
        raise RepositoryUnavailableError(str(exc)) from exc


class S3ObjectStore(ObjectStore):
    """`ObjectStore` backed by an S3 bucket. `prefix` is a key prefix inside the
    bucket. `append_attempts` is how many times `append` tries before it gives
    up."""

    def __init__(
        self,
        client: S3Client,
        bucket: str,
        *,
        prefix: str = "",
        append_attempts: int = _APPEND_ATTEMPTS,
    ) -> None:
        self._client = client
        self._bucket = bucket
        self._prefix = (prefix.strip("/") + "/") if prefix.strip("/") else ""
        self._append_attempts = append_attempts

    @classmethod
    def from_url(cls, url: str) -> Self:
        bucket, prefix = parse_s3_url(url)
        with _translating_transport_errors():
            # boto3 looks for credentials on the first request, not here. So a
            # missing login fails in `verify_available` or in the first operation.
            client = boto3.Session().client(
                "s3",
                config=Config(
                    connect_timeout=CONNECTION_TIMEOUT,
                    read_timeout=READ_TIMEOUT,
                    retries={"mode": "standard"},
                ),
            )
        return cls(cast(S3Client, client), bucket, prefix=prefix)

    def verify_available(self) -> None:
        with _translating_transport_errors():
            try:
                self._client.head_bucket(Bucket=self._bucket)
            except ClientError as exc:
                if _error_code(exc) in _NOT_FOUND:
                    raise ValueError(
                        f"bucket {self._bucket!r} does not exist. Create it first."
                    ) from None
                raise

    def _build_key(self, path: str) -> str:
        return self._prefix + path

    def _read_snapshot(self, key: str) -> _Snapshot | None:
        """The object as it is now, or `None` when absent."""
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
        except ClientError as exc:
            if _error_code(exc) in _NOT_FOUND:
                return None
            raise
        return _Snapshot(response["Body"].read(), response["ETag"])

    def read(self, path: str) -> bytes:
        with _translating_transport_errors():
            snapshot = self._read_snapshot(self._build_key(path))
            if snapshot is None:
                raise ObjectNotFoundError(path)
            return snapshot.content

    def write(self, content: bytes, path: str) -> None:
        with _translating_transport_errors():
            self._client.put_object(
                Bucket=self._bucket, Key=self._build_key(path), Body=content
            )

    def append(self, content: bytes, path: str) -> None:
        """S3 cannot append to an object, so this reads the object, adds
        `content` and puts the whole object back. The put only succeeds if the
        object still has the ETag that the read saw, or does not exist yet when
        the read found nothing. If another writer changed it, the put fails and
        the loop reads again, up to `append_attempts` times.
        """
        key = self._build_key(path)
        with _translating_transport_errors():
            for attempt in range(self._append_attempts):
                current = self._read_snapshot(key)
                # On a new key the put must only create. If another writer
                # created the object first, the put fails instead of replacing it.
                if current is None:
                    body, condition = content, {"IfNoneMatch": "*"}
                else:
                    body, condition = (
                        current.content + content,
                        {"IfMatch": current.etag},
                    )
                try:
                    self._client.put_object(
                        Bucket=self._bucket, Key=key, Body=body, **condition
                    )
                    return
                except ClientError as exc:
                    if _error_code(exc) not in _LOST_CONDITIONAL_WRITE:
                        raise
                    time.sleep(_jitter.uniform(0, _RETRY_JITTER * (attempt + 1)))
        raise RepositoryUnavailableError(
            f"append to {path} failed {self._append_attempts} times in a row "
            "because another writer kept changing the object"
        )

    def delete(self, path: str) -> None:
        key = self._build_key(path)
        with _translating_transport_errors():
            # S3 reports success for a delete of a missing object. So the store
            # first makes sure that the object exists.
            try:
                self._client.head_object(Bucket=self._bucket, Key=key)
            except ClientError as exc:
                if _error_code(exc) in _NOT_FOUND:
                    raise ObjectNotFoundError(path) from None
                raise
            self._client.delete_object(Bucket=self._bucket, Key=key)

    def list(self, prefix: str) -> Iterator[str]:
        full = self._build_key(prefix)
        prefix_len = len(self._prefix)
        with _translating_transport_errors():
            # The paginator fetches each page when the loop reaches it, so a
            # transport failure can occur in the middle of the iteration.
            paginator = self._client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self._bucket, Prefix=full):
                for entry in page.get("Contents", []):
                    yield entry["Key"][prefix_len:]


# The naming rule of S3 for general purpose buckets. Without this check, a typo
# or a port fails at the endpoint, far from the mistake.
_BUCKET_NAME = re.compile(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]")


def parse_s3_url(url: str) -> tuple[str, str]:
    """Split an `s3://bucket[/prefix]` URL into (bucket, prefix).

    Raises:
        ValueError: for a malformed URL. No message quotes the URL, which can
            carry a credential.
    """
    parsed = urlparse(url)
    if parsed.scheme != "s3":
        raise ValueError("S3ObjectStore expects an s3:// URL")
    bucket = parsed.netloc
    if not bucket:
        raise ValueError(
            "s3:// URL is missing the bucket. Use s3://<bucket>[/<prefix>]."
        )
    if "@" in bucket:
        # Before the bucket name is quoted below, so a password never reaches
        # the message.
        raise ValueError(
            "s3:// URL must not carry credentials. A username and password in "
            "the URL are not supported. Authentication uses the AWS default "
            "credential chain."
        )
    if not _BUCKET_NAME.fullmatch(bucket):
        raise ValueError(
            f"s3:// URL has an invalid bucket name {bucket!r}: bucket names are "
            "3-63 lowercase letters, digits, dots and hyphens. A port or an "
            "endpoint in the URL is not supported."
        )
    if parsed.query or parsed.fragment:
        # Otherwise the signature of a presigned URL is dropped silently.
        raise ValueError(
            "s3:// URL must not carry a query or fragment. Presigned URLs are "
            "not supported. Authentication uses the AWS default credential "
            "chain (see https://github.com/jesrav/evaltrack/blob/main/docs/"
            "repositories.md#authentication-1)."
        )
    # urlparse keeps the leading slash on path.
    prefix = parsed.path.lstrip("/")
    return bucket, prefix


def open_from_url(url: str) -> RunRepository:
    """Open a repository for an `s3://` URL."""
    return RunRepository(S3ObjectStore.from_url(url))
