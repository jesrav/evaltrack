"""`evaltrack ui`: which repositories it mounts, what it prints, and the
startup checks. The dashboard itself is covered in `tests/ui/`."""

import sys
from pathlib import Path

import pytest

# Every name below comes from the optional `[ui]` extra. Without it this
# module skips instead of failing collection, so the rest of the suite
# still runs.
pytest.importorskip("fastapi", reason="needs the [ui] extra")


from fastapi import FastAPI  # noqa: E402

from evaltrack.cli import main
from evaltrack.config import PrUrlTemplate
from evaltrack.core.errors import RepositoryUnavailableError
from evaltrack.ui import MountedRepository

from ..fakes import MemoryStore, mount_fake_azure
from .helpers import configure_local, configure_repositories, run_cli


def _set_up_stub_server(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace uvicorn with a client that starts the app and stops, so
    `evaltrack ui` returns without serving or blocking."""
    import uvicorn
    from fastapi.testclient import TestClient

    def _run(app: FastAPI, **kwargs: object) -> None:
        with TestClient(app):
            pass

    monkeypatch.setattr(uvicorn, "run", _run)


def _capture_mounts(
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, MountedRepository]:
    """Wrap `create_app` to record the repositories `evaltrack ui` mounts."""
    import evaltrack.ui.app as ui_app

    captured: dict[str, MountedRepository] = {}
    real_create_app = ui_app.create_app

    def _capture(
        repositories: dict[str, MountedRepository],
        *,
        pr_url_template: PrUrlTemplate | None = None,
    ) -> FastAPI:
        captured.update(repositories)
        return real_create_app(repositories, pr_url_template=pr_url_template)

    monkeypatch.setattr(ui_app, "create_app", _capture)
    return captured


def _capture_app(monkeypatch: pytest.MonkeyPatch) -> list[FastAPI]:
    """Wrap `create_app` to record the app `evaltrack ui` builds, so a test can
    send it requests."""
    import evaltrack.ui.app as ui_app

    built: list[FastAPI] = []
    real_create_app = ui_app.create_app

    def _capture(
        repositories: dict[str, MountedRepository],
        *,
        pr_url_template: PrUrlTemplate | None = None,
    ) -> FastAPI:
        app = real_create_app(repositories, pr_url_template=pr_url_template)
        built.append(app)
        return app

    monkeypatch.setattr(ui_app, "create_app", _capture)
    return built


def test_ui_prints_the_dashboard_url(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The printed URL is the only way in, so it has to reach the terminal."""
    configure_repositories(tmp_path)
    monkeypatch.chdir(tmp_path)
    _set_up_stub_server(monkeypatch)

    result = run_cli(["ui", "--port", "9997"])

    assert result.code == 0
    assert "http://127.0.0.1:9997/" in result.err


def test_ui_binds_loopback_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The dashboard is unauthenticated, so the bind host is not configurable
    and always stays on loopback."""
    import uvicorn

    configure_repositories(tmp_path)
    monkeypatch.chdir(tmp_path)
    _set_up_stub_server(monkeypatch)
    bound: list[object] = []

    def _record_run(*args: object, **kwargs: object) -> None:
        bound.append(kwargs["host"])

    monkeypatch.setattr(uvicorn, "run", _record_run)

    code = main(["ui", "--port", "9996"])

    assert code == 0
    assert bound == ["127.0.0.1"]


@pytest.mark.parametrize("value", ["99999", "-1", "0"])
def test_ui_port_outside_the_valid_range_rejected(
    value: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """A port outside 1-65535 once reached socket.bind and raised an uncaught
    OverflowError. Port 0 printed a dead :0 URL while uvicorn bound an
    ephemeral port. Argparse rejects both up front."""
    with pytest.raises(SystemExit) as excinfo:
        main(["ui", "--port", value])
    assert excinfo.value.code == 2, "a rejected --port is a usage error that exits 2"
    err = capsys.readouterr().err
    assert "--port" in err
    assert "1-65535" in err
    assert "Traceback" not in err, (
        "a traceback would mean the OverflowError still escapes"
    )


def test_ui_port_at_the_upper_bound_accepted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """65535 is the last valid port, so the range check must let it through."""
    configure_repositories(tmp_path)
    monkeypatch.chdir(tmp_path)
    _set_up_stub_server(monkeypatch)

    result = run_cli(["ui", "--port", "65535"])

    assert result.code == 0
    assert "http://127.0.0.1:65535/" in result.err


def test_ui_unavailable_repository_exits_2(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A local repository whose backing storage is unavailable aborts startup
    with the storage error, instead of a dashboard where every request fails."""

    class _UnavailableStore(MemoryStore):
        def verify_available(self) -> None:
            raise ValueError(
                "container 'wip' does not exist in storage account "
                "'evaltracktesting'; create it first"
            )

    configure_local(tmp_path, mount_fake_azure(monkeypatch, _UnavailableStore()))
    monkeypatch.chdir(tmp_path)
    _set_up_stub_server(monkeypatch)

    result = run_cli(["ui", "--port", "9991"])

    assert result.code == 2, "unavailable storage is an environment error that exits 2"
    assert "container 'wip' does not exist" in result.err
    assert "create it first" in result.err
    assert "dashboard:" not in result.err, (
        "a URL must not be offered for a server that never started"
    )


def test_ui_starts_without_verifying_the_remote(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A remote that cannot be reached (no network, an expired login) can take a
    minute of credential and connect timeouts to refuse. Startup must not spend
    it, so the remote is mounted unverified and the dashboard serves at once."""
    verifications: list[str] = []

    class _RecordingStore(MemoryStore):
        def verify_available(self) -> None:
            verifications.append("remote")
            raise RepositoryUnavailableError(
                "DefaultAzureCredential failed to retrieve a token"
            )

    remote = mount_fake_azure(monkeypatch, _RecordingStore())
    monkeypatch.delenv("EVALTRACK_REMOTE", raising=False)
    (tmp_path / "pyproject.toml").write_text(
        f'[tool.evaltrack]\nlocal = "{tmp_path}/local"\nremote = "{remote}"\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    _set_up_stub_server(monkeypatch)
    mounts = _capture_mounts(monkeypatch)

    result = run_cli(["ui", "--port", "9994"])

    assert result.code == 0
    assert verifications == [], "startup must not wait on the remote's verification"
    assert list(mounts) == ["local", "remote"]


def test_ui_reports_an_unusable_remote_per_request(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A remote nothing can be read from, here a path naming a regular file, is
    mounted like any other. Its own requests answer 502 with the reason, which
    the dashboard shows, while the local mount keeps serving."""
    from fastapi.testclient import TestClient

    blocker = tmp_path / "blocker"
    blocker.write_bytes(b"a file where a directory is needed")
    monkeypatch.delenv("EVALTRACK_REMOTE", raising=False)
    (tmp_path / "pyproject.toml").write_text(
        f'[tool.evaltrack]\nlocal = "{tmp_path}/local"\nremote = "{blocker}"\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    _set_up_stub_server(monkeypatch)
    built = _capture_app(monkeypatch)

    result = run_cli(["ui", "--port", "9992"])

    assert result.code == 0
    # The app admits loopback Hosts only, so the default `testserver` is out.
    with TestClient(built[0], base_url="http://127.0.0.1") as client:
        listed = client.get("/api/repositories")
        remote_runs = client.get("/api/repositories/remote/runs")
        local_runs = client.get("/api/repositories/local/runs")
    assert [r["role"] for r in listed.json()] == ["local", "remote"]
    assert remote_runs.status_code == 502, "an unreachable backend is not a bug"
    assert "is not a directory" in remote_runs.json()["detail"]
    assert local_runs.status_code == 200, "the local runs survive an unusable remote"


def test_ui_serves_local_alone_when_the_remote_extra_is_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A remote whose backend cannot even be opened, here for a missing extra,
    is skipped with a warning: there is nothing to mount, and the failure costs
    no network I/O. The test hides the azure module, so its import fails."""
    monkeypatch.setitem(sys.modules, "evaltrack.repositories.azure", None)
    monkeypatch.delenv("EVALTRACK_REMOTE", raising=False)
    (tmp_path / "pyproject.toml").write_text(
        f'[tool.evaltrack]\nlocal = "{tmp_path}/local"\nremote = "azure://evals"\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    _set_up_stub_server(monkeypatch)
    mounts = _capture_mounts(monkeypatch)

    result = run_cli(["ui", "--port", "9993"])

    assert result.code == 0
    assert list(mounts) == ["local"]
    assert "evaltrack[azure]" in result.err


def test_ui_remote_warning_does_not_echo_the_url(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A SAS URL copied from the Azure portal is a likely first value for the
    remote, and the dashboard rejects it. The warning must not put the live
    token in the terminal, the shell history or a log."""
    remote = "azure://myacct/evals?sig=SUPERSECRETSIG&se=2030"
    monkeypatch.setenv("EVALTRACK_REMOTE", remote)
    configure_local(tmp_path, f"{tmp_path}/local")
    monkeypatch.chdir(tmp_path)
    _set_up_stub_server(monkeypatch)
    mounts = _capture_mounts(monkeypatch)

    result = run_cli(["ui", "--port", "9990"])

    assert result.code == 0
    assert list(mounts) == ["local"], "the rejected remote is not mounted"
    assert "SUPERSECRETSIG" not in result.err
    assert remote not in result.err
    assert "must not carry a query or fragment" in result.err, (
        "the redacted warning must still say why the remote was skipped"
    )
    assert "Serving the local repository only" in result.err


def test_ui_run_id_prints_a_deep_link(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A failing run tells the reader to run `evaltrack ui --run-id <id>`, so the
    URL that lands in the terminal has to open on that run."""
    configure_repositories(tmp_path)
    monkeypatch.chdir(tmp_path)
    _set_up_stub_server(monkeypatch)

    result = run_cli(["ui", "--run-id", "01ABC", "--port", "9996"])

    assert result.code == 0
    assert "http://127.0.0.1:9996/?run=01ABC" in result.err


def test_ui_mounts_local_and_remote_from_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The checked-in setup a team shares mounts both repositories, each in its
    own role, so the dashboard can show local runs against the shared history."""
    monkeypatch.delenv("EVALTRACK_REMOTE", raising=False)
    (tmp_path / "pyproject.toml").write_text(
        f'[tool.evaltrack]\nlocal = "{tmp_path}/local"\nremote = "{tmp_path}/remote"\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    _set_up_stub_server(monkeypatch)
    mounts = _capture_mounts(monkeypatch)

    code = main(["ui", "--port", "9995"])

    assert code == 0
    by_role = {m.role: m.url for m in mounts.values()}
    assert by_role["local"].endswith("/local")
    assert by_role["remote"].endswith("/remote")
    assert [slug for slug in mounts] == ["local", "remote"], "the slug is the role"


def test_ui_defaults_to_local_evaltrack_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No config and no flags: only the default local repository, role local."""
    monkeypatch.chdir(tmp_path)
    _set_up_stub_server(monkeypatch)
    mounts = _capture_mounts(monkeypatch)

    code = main(["ui", "--port", "9994"])

    assert code == 0
    assert len(mounts) == 1, "an extra mount would be a repository no config names"
    mount = next(iter(mounts.values()))
    assert mount.role == "local"
    assert mount.url == f"{tmp_path}/.evaltrack"


def test_ui_mounts_local_alone_when_no_remote_is_configured(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A remote is optional, and a new project has none. The dashboard over the
    local runs must still start, not fail for a missing remote."""
    local = f"{tmp_path}/local"
    configure_local(tmp_path, local)
    monkeypatch.chdir(tmp_path)
    _set_up_stub_server(monkeypatch)
    mounts = _capture_mounts(monkeypatch)

    code = main(["ui", "--port", "9993"])

    assert code == 0
    assert [(m.role, m.url) for m in mounts.values()] == [("local", local)]


def test_ui_names_each_repository_it_mounted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`ui` is the one command with no repository flag, so it always resolves
    one for you. In a monorepo it and the pytest plugin can land on different
    `pyproject.toml` files, and only the printed paths make that visible."""
    configure_repositories(tmp_path)
    monkeypatch.chdir(tmp_path)
    _set_up_stub_server(monkeypatch)

    result = run_cli(["ui", "--port", "9989"])

    assert result.code == 0
    assert f"mounted local repository {tmp_path}/local" in result.err
    assert f"mounted remote repository {tmp_path}/remote" in result.err
