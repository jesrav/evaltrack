"""Top-level `main` behavior: `--version`, bare invocation, how backend
failures map to exit codes, and output that nobody reads any more."""

import io
import os
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

import evaltrack
from evaltrack.cli import main
from evaltrack.core.errors import RepositoryUnavailableError

from ..fakes import RaisingStore, mount_fake_azure
from .helpers import run_cli, seed_run, write_run_file


def test_version_flag_prints_the_package_version(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`--version` must print the installed version, and it must agree with
    `__version__`."""
    assert evaltrack.__version__
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])
    assert excinfo.value.code == 0
    assert capsys.readouterr().out.strip() == f"evaltrack {evaltrack.__version__}"


def test_no_command_is_a_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    """`evaltrack "$CMD"` with an empty variable must not look like success. The
    usage goes to stderr, so a script reading stdout gets nothing to parse."""
    with pytest.raises(SystemExit) as excinfo:
        main([])
    assert excinfo.value.code == 2
    captured = capsys.readouterr()
    assert "usage:" in captured.err.lower()
    assert captured.out == ""


# storage backend runtime errors


def _register_failing_backend(monkeypatch: pytest.MonkeyPatch, exc: Exception) -> str:
    """A URL whose store always raises `exc`."""
    return mount_fake_azure(monkeypatch, RaisingStore(exc))


def test_push_storage_unavailable_exits_2(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Unreachable storage (no credentials, network down, an HTTP error) is an
    environment problem, not a bug. It gets a clean message and exit 2, with no
    traceback. The message must survive, because the user has nothing else to
    act on."""
    run_file = write_run_file(tmp_path)
    url = _register_failing_backend(
        monkeypatch,
        RepositoryUnavailableError(
            "DefaultAzureCredential failed to retrieve a token: run `az login`."
        ),
    )
    result = run_cli(["push", "--run-file", str(run_file.path), "--repository", url])
    assert result.code == 2, "storage that cannot be reached exits 2"
    assert "error:" in result.err
    assert "run `az login`" in result.err


def test_promote_storage_unavailable_exits_2(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`promote` gets the same clean treatment as `push` when the backend
    fails at runtime."""
    url = _register_failing_backend(
        monkeypatch, RepositoryUnavailableError("connection refused")
    )
    result = run_cli(["promote", "pr/1", "--repository", url])
    assert result.code == 2, "storage that cannot be reached exits 2"
    assert "error:" in result.err
    assert "connection refused" in result.err


def test_unexpected_backend_error_tracebacks_and_exits_70(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Only unreachable storage gets the clean treatment. An unexpected
    exception type is a bug, so the traceback must stay visible for the report.
    Nothing else exits 70, so a script can tell a crash from an outcome it can
    swallow."""
    run_file = write_run_file(tmp_path)
    url = _register_failing_backend(monkeypatch, RuntimeError("boom"))
    result = run_cli(["push", "--run-file", str(run_file.path), "--repository", url])
    assert result.code == 70, "an unexpected exception is a bug and exits 70"
    assert "Traceback" in result.err
    assert "boom" in result.err
    assert "bug in evaltrack" in result.err


# output nobody is reading any more


def test_a_listing_whose_reader_left_exits_0_and_says_nothing(tmp_path: Path) -> None:
    """`evaltrack runs | head -1` is an ordinary thing to write, and a broken
    pipe ends it. A report of that error would fail the pipeline under
    `set -o pipefail`."""
    url = f"{tmp_path}/repository"
    seed_run(url)
    read_fd, write_fd = os.pipe()
    os.close(read_fd)
    err = io.StringIO()

    with (
        os.fdopen(write_fd, "w") as stdout,
        redirect_stdout(stdout),
        redirect_stderr(err),
    ):
        code = main(["runs", "--repository", url])

    assert code == 0, "a reader that stopped early ends the output, it does not fail it"
    assert err.getvalue() == ""


def test_a_listing_whose_reader_left_leaves_no_shutdown_noise(tmp_path: Path) -> None:
    """The interpreter flushes stdout again on the way out, and that second
    failure prints `Exception ignored in: <_io.TextIOWrapper name='<stdout>'>`
    past any handler. Only a real process reaches that point, so this runs one.
    """
    url = f"{tmp_path}/repository"
    seed_run(url)
    read_fd, write_fd = os.pipe()
    os.close(read_fd)

    try:
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys; from evaltrack.cli import main; sys.exit(main(sys.argv[1:]))",
                "runs",
                "--repository",
                url,
            ],
            stdout=write_fd,
            stderr=subprocess.PIPE,
            text=True,
        )
    finally:
        os.close(write_fd)

    assert result.returncode == 0, "the listing exits 0 with nobody reading it"
    assert result.stderr == ""
