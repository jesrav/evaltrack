"""The app shell the dashboard serves: the built index, the favicon, and the
fallback a checkout without a frontend build gets."""

from pathlib import Path

import pytest

# Every name below comes from the optional `[ui]` extra. Without it this
# module skips instead of failing collection, so the rest of the suite
# still runs.
pytest.importorskip("fastapi", reason="needs the [ui] extra")

from fastapi.testclient import TestClient  # noqa: E402

from evaltrack.repositories import RunRepository

from .conftest import make_repo_client


def test_landing_page_renders(client_factory: TestClient) -> None:
    r = client_factory.get("/")
    assert r.status_code == 200
    assert "evaltrack" in r.text


def test_index_html_revalidates(
    populated_repository: RunRepository,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The built index.html must be served `no-cache`, so a browser never serves
    a stale shell that points at a renamed asset bundle. A stale shell shows as
    a blank page.

    The static dir is pointed at a temp build, so the assertion runs whether or
    not this checkout has one. Guarding it behind a real build instead would
    make the test silently assert nothing wherever the frontend is unbuilt,
    which is every plain checkout and the CI python job."""
    import evaltrack.ui.routes.static as static_module

    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("<!doctype html><title>built</title>")
    monkeypatch.setattr(static_module, "STATIC_DIR", static)

    with make_repo_client(populated_repository) as client:
        r = client.get("/")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-cache"


def test_index_falls_back_when_the_frontend_is_not_built(
    populated_repository: RunRepository,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without a built frontend, `/` still tells the reader what to run rather
    than erroring or serving nothing."""
    import evaltrack.ui.routes.static as static_module

    monkeypatch.setattr(static_module, "STATIC_DIR", tmp_path / "missing")
    with make_repo_client(populated_repository) as client:
        r = client.get("/")
    assert r.status_code == 200
    assert "just frontend_build" in r.text, "the fallback names the build command"


def test_favicon_served_when_built(
    populated_repository: RunRepository,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The `/favicon.svg` route serves the SVG copied into the build from
    `frontend/public/`. Point the static dir at a temp copy, so the test does
    not depend on whether this checkout has a built frontend."""
    import evaltrack.ui.routes.static as static_module

    static = tmp_path / "static"
    static.mkdir()
    (static / "favicon.svg").write_bytes(b"<svg xmlns='http://www.w3.org/2000/svg'/>")
    monkeypatch.setattr(static_module, "STATIC_DIR", static)

    with make_repo_client(populated_repository) as client:
        r = client.get("/favicon.svg")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/svg+xml"


def test_favicon_404_when_not_built(
    populated_repository: RunRepository,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no built frontend the route 404s cleanly rather than erroring."""
    import evaltrack.ui.routes.static as static_module

    monkeypatch.setattr(static_module, "STATIC_DIR", tmp_path / "missing")
    with make_repo_client(populated_repository) as client:
        r = client.get("/favicon.svg")
    assert r.status_code == 404
