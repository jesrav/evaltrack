"""What a session that is interrupted mid-suite keeps.

The evals a session already ran cost real money, so an interrupted session
must save what it recorded. The test here starts a real pytest process, waits
until it records one eval, and signals it.
"""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from evaltrack.core.run_record import parse_run_json

from .helpers import DEFAULT_BODY, make_eval_source

# Generous. The child pays for an interpreter start, the plugin imports and a
# collection before it records anything, on a machine that can be busy.
_RECORD_TIMEOUT = 120.0
_EXIT_TIMEOUT = 60.0

# The test body writes the marker after the eval, so a parent that sees it knows
# the session has something to lose. The sleep only has to outlast the signal,
# which the parent sends as soon as the marker appears.
_SLEEPING_EVAL_SOURCE = make_eval_source(
    name="test_records_then_sleeps",
    preamble="import os\nimport time",
    body=DEFAULT_BODY
    + """\
pathlib.Path(os.environ["EVALTRACK_MARKER"]).write_text("recorded")
time.sleep(120)
""",
)


def _write_project(project: Path) -> None:
    """Lay out a one-eval project. Its own pyproject pins the rootdir, so the
    session does not inherit configuration from wherever tmp_path lives."""
    project.mkdir(parents=True)
    (project / "pyproject.toml").write_text(
        "[tool.pytest.ini_options]\n", encoding="utf-8"
    )
    (project / "test_eval.py").write_text(_SLEEPING_EVAL_SOURCE, encoding="utf-8")


def _session_log(project: Path) -> str:
    """What the signalled session printed. An assertion that cannot say why on
    its own quotes it."""
    return (project / "session.log").read_text(encoding="utf-8")


def _run_until_signalled(project: Path, *, signum: int) -> int:
    """Run the eval session, signal it once it has recorded its eval, and
    report the exit code it terminated with.

    The parent waits for the marker file the test body writes, not for a sleep.
    A sleep would let the signal race the eval it must arrive after.
    """
    marker = project / "recorded.marker"
    with (project / "session.log").open("w", encoding="utf-8") as sink:
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "pytest",
                "test_eval.py",
                "-p",
                "no:cacheprovider",
                f"--evaltrack-run-file={project / 'run.json'}",
            ],
            cwd=project,
            stdout=sink,
            stderr=subprocess.STDOUT,
            env={**os.environ, "EVALTRACK_MARKER": str(marker)},
        )
        try:
            deadline = time.monotonic() + _RECORD_TIMEOUT
            while not marker.exists():
                assert proc.poll() is None, (
                    f"the session exited before recording an eval:"
                    f"\n{_session_log(project)}"
                )
                assert time.monotonic() < deadline, (
                    f"the session recorded no eval in {_RECORD_TIMEOUT}s:"
                    f"\n{_session_log(project)}"
                )
                time.sleep(0.05)
            proc.send_signal(signum)
            proc.wait(timeout=_EXIT_TIMEOUT)
        finally:
            proc.kill()
            proc.wait()
    return proc.returncode


def _recorded_case_ids(run_path: Path) -> list[str]:
    """The cases the saved run holds for the test that finished its eval."""
    run = parse_run_json(run_path.read_bytes())
    test = run.tests["test_eval.py::test_records_then_sleeps"]
    return sorted(test.cases)


def test_sigint_saves_the_run_recorded_so_far(tmp_path: Path) -> None:
    """Ctrl-C reaches session end through pytest's own interrupt handling, which
    is what saves the run. A cancelled GitHub Actions job arrives the same way."""
    project = tmp_path / "project"
    _write_project(project)

    returncode = _run_until_signalled(project, signum=signal.SIGINT)

    run_path = project / "run.json"
    assert run_path.exists(), f"SIGINT lost the run:\n{_session_log(project)}"
    assert _recorded_case_ids(run_path) == ["c"], (
        "the eval that finished before the signal is missing from the saved run"
    )
    assert returncode != 0, "an interrupted session must not report success"
