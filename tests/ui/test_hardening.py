"""Browser hardening and failure mapping: the Host allowlist, the security
headers every response carries, and how storage and programming failures reach
the client."""

from collections.abc import Mapping

import pytest

# Every name below comes from the optional `[ui]` extra. Without it this
# module skips instead of failing collection, so the rest of the suite
# still runs.
pytest.importorskip("fastapi", reason="needs the [ui] extra")

from fastapi.testclient import TestClient  # noqa: E402

from evaltrack.core.errors import RepositoryUnavailableError
from evaltrack.repositories import RunRepository

from ..fakes import RaisingStore
from .conftest import make_repo_client


def _assert_hardened(headers: Mapping[str, str]) -> None:
    assert headers["content-security-policy"] == "frame-ancestors 'none'", (
        "no page may frame the app"
    )
    assert headers["x-content-type-options"] == "nosniff", (
        "a JSON body must not be sniffable into a script"
    )


@pytest.mark.parametrize(
    "origin",
    ["http://localhost", "http://localhost:5173", "http://127.0.0.1:8765"],
    ids=["localhost", "dev-server-port", "the-bind-address"],
)
def test_write_accepts_an_origin_on_an_allowed_host(
    populated_repository: RunRepository, origin: str
) -> None:
    """A same-host page can only carry one of the two hosts the allowlist admits,
    so those are the origins a write accepts. Any port passes, for a dev server
    that proxies the API."""
    with make_repo_client(populated_repository) as client:
        r = client.delete(
            "/api/repositories/main/refs/pr/42", headers={"origin": origin}
        )
    assert r.status_code == 200


@pytest.mark.parametrize(
    "origin",
    [
        "https://evil.example",
        "http://[::1]:8765",
        "http://0.0.0.0:8765",
        "null",
        "http://[",
    ],
    ids=["a-site", "v6-loopback", "all-v4", "opaque", "malformed"],
)
def test_write_rejects_an_origin_off_the_allowed_hosts(
    populated_repository: RunRepository, origin: str
) -> None:
    """Every other origin is a cross-site write. `[::1]` is loopback, but the Host
    allowlist never admits it, so no page the dashboard served carries it. A
    malformed Origin is refused like the rest, not raised out of the URL parser
    as a 500."""
    with make_repo_client(populated_repository) as client:
        r = client.delete(
            "/api/repositories/main/refs/pr/42", headers={"origin": origin}
        )
    assert r.status_code == 403
    assert populated_repository.get_ref("pr/42") is not None, (
        "the refused delete left the ref untouched"
    )


def test_forged_host_rejected(client_factory: TestClient) -> None:
    """DNS rebinding points the attacker's own domain at 127.0.0.1 to read runs
    same-origin. The request still carries the attacker's Host, so the
    allowlist must refuse it before any data is served."""
    r = client_factory.get("/api/repositories", headers={"host": "evil.example"})
    assert r.status_code == 400
    assert r.headers["x-content-type-options"] == "nosniff", (
        "the rejection itself is still a hardened response"
    )


@pytest.mark.parametrize(
    "host", ["localhost", "127.0.0.1", "localhost:8765", "127.0.0.1:8765"]
)
def test_loopback_hosts_accepted(client_factory: TestClient, host: str) -> None:
    r = client_factory.get("/api/repositories", headers={"host": host})
    assert r.status_code == 200


@pytest.mark.parametrize("path", ["/", "/api/repositories"])
def test_security_headers_on_every_response(
    client_factory: TestClient, path: str
) -> None:
    """Anti-framing must cover the app shell as well as the API. An attacker's
    page frames the shell to drive same-origin delete clicks."""
    r = client_factory.get(path)
    assert r.status_code == 200
    _assert_hardened(r.headers)


@pytest.mark.parametrize(
    "message",
    [
        "token expired: run `az login`",
        "connection refused",
        "The specified container does not exist.",
    ],
    ids=["auth", "network", "container-gone"],
)
def test_backend_error_maps_to_502(message: str) -> None:
    """Mid-session backend failures (expired credentials, the container deleted
    while serving, network loss) surface as a 502 that names the failure, so the
    frontend can show a storage error instead of a bare 500."""
    repo = RunRepository(RaisingStore(RepositoryUnavailableError(message)))
    with make_repo_client(repo) as client:
        r = client.get("/api/repositories/main/refs")
    assert r.status_code == 502
    detail = r.json()["detail"]
    assert "storage backend error" in detail
    assert message in detail, "the 502 must not swallow the backend's own message"


def test_non_backend_error_still_500s() -> None:
    """Only unreachable storage gets the 502 mapping. A programming error must
    stay a 500, so a bug does not read as a storage outage. Its body is the
    generic status text: an internal failure describes the server, and a
    dashboard reachable from a browser must not narrate it."""
    repo = RunRepository(RaisingStore(RuntimeError("boom")))
    with make_repo_client(repo, raise_server_exceptions=False) as client:
        r = client.get("/api/repositories/main/refs")
    assert r.status_code == 500
    assert "boom" not in r.text, "the failure's own message must not reach the client"
