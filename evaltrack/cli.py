"""The evaltrack command line."""

import argparse
import importlib.metadata
import os
import sys
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from evaltrack.config import (
    DEFAULT_LOCAL_PATH,
    LOCAL_ENV,
    REMOTE_ENV,
    ConfiguredRemote,
    EvaltrackConfig,
    RepositoryRole,
    load_config,
    resolve_local,
    resolve_remote,
)
from evaltrack.core.errors import (
    CorruptRecordError,
    EvaltrackError,
    InvalidIdentifierError,
    RepositoryUnavailableError,
)
from evaltrack.core.refs import BASELINE_REF, Ref, RefKind, ReflogEntry
from evaltrack.core.run_record import (
    RunRecord,
    dump_run_json,
    ensure_run_id,
    parse_run_json,
)
from evaltrack.repositories import (
    RunRepository,
    RunSummary,
    open_repository,
    promote,
)
from evaltrack.ui.report import collect_report_data, render_report

# The dashboard is unauthenticated, so it binds loopback only.
_UI_HOST = "127.0.0.1"

# sysexits.h EX_SOFTWARE, clear of the command exit codes.
_EXIT_INTERNAL_ERROR = 70

DEFAULT_REPORT_PATH = "evaltrack-report.html"

_ISSUES_URL = "https://github.com/jesrav/evaltrack/issues"


# --- entry point ---


def main(argv: list[str] | None = None) -> int:
    """Run one invocation and return the exit code (`docs/cli.md` lists them)."""
    args = _build_parser().parse_args(argv)
    try:
        # Every subparser stores its handler, and the parser refuses a session
        # with none.
        code = args.func(args)
        # Flush here rather than at exit. A closed pipe raises at the flush,
        # and at exit nothing is left to catch it.
        sys.stdout.flush()
        return code
    except BrokenPipeError:
        return _end_on_broken_pipe()
    except (EvaltrackError, ValueError, ImportError, OSError) as exc:
        # User errors and environment problems get a clean message, no traceback.
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Exception:
        traceback.print_exc()
        print(
            "error: this is a bug in evaltrack. Please report it at "
            f"{_ISSUES_URL} with the traceback above.",
            file=sys.stderr,
        )
        return _EXIT_INTERNAL_ERROR


def _end_on_broken_pipe() -> int:
    """Point stdout at the null device, so the interpreter's shutdown flush cannot fail
    again."""
    try:
        stdout_fd = sys.stdout.fileno()
    except (OSError, ValueError):
        # Captured stdout has no descriptor.
        return 0
    devnull_fd = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(devnull_fd, stdout_fd)
    finally:
        os.close(devnull_fd)
    return 0


# --- the parser ---


def _int_in_range(lo: int, hi: int | None, *, what: str) -> Callable[[str], int]:
    """Argparse type for an int of at least `lo` and, unless None, at most `hi`. A
    rejection names the value as `what`."""

    def parse(text: str) -> int:
        try:
            value = int(text)
        except ValueError:
            raise argparse.ArgumentTypeError(f"invalid int value: {text!r}") from None
        if value < lo:
            raise argparse.ArgumentTypeError(f"{what} is at least {lo}, got {value}")
        if hi is not None and value > hi:
            raise argparse.ArgumentTypeError(f"{what} is at most {hi}, got {value}")
        return value

    return parse


def _add_repository_flags(
    command: argparse.ArgumentParser, *, required: bool, url_help: str
) -> None:
    """Add the mutually exclusive `--repository`, `--local` and `--remote` flags."""
    group = command.add_mutually_exclusive_group(required=required)
    group.add_argument(
        "--repository",
        default=None,
        dest="repository_url",
        help=url_help,
    )
    group.add_argument(
        "--local",
        action="store_true",
        default=False,
        help=f"The configured local repository: ${LOCAL_ENV}, then "
        f"[tool.evaltrack].local, else {DEFAULT_LOCAL_PATH} under the project.",
    )
    group.add_argument(
        "--remote",
        action="store_true",
        default=False,
        help=f"The configured remote repository: ${REMOTE_ENV}, then "
        "[tool.evaltrack].remote.",
    )


def _build_parser() -> argparse.ArgumentParser:
    # Subparsers do not inherit `allow_abbrev`, so every `add_parser` repeats it.
    parser = argparse.ArgumentParser(
        prog="evaltrack",
        description="Record, push, and promote evaltrack runs.",
        allow_abbrev=False,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"evaltrack {importlib.metadata.version('evaltrack')}",
    )
    # Required, so a missing subcommand is a usage error rather than a silent
    # success. `evaltrack "$CMD"` with an empty variable must not exit 0.
    sub = parser.add_subparsers(dest="command", required=True)

    push = sub.add_parser(
        "push",
        help="Copy a run JSON file into a repository.",
        allow_abbrev=False,
    )
    push.set_defaults(func=_cmd_push)
    push.add_argument(
        "--run-file",
        required=True,
        type=Path,
        help="Path to a run JSON file (typically produced by "
        "`pytest --evaltrack-run-file`).",
    )
    _add_repository_flags(
        push,
        required=False,
        url_help="Repository path or URL. Defaults to the configured remote.",
    )
    push.add_argument(
        "--ref",
        default=None,
        help="If set, point this ref at the run after saving.",
    )
    push.add_argument(
        "--commit",
        default=None,
        help="Record this commit on the reflog entry written by --ref.",
    )
    push.add_argument(
        "--pr",
        type=_int_in_range(1, None, what="a PR number"),
        default=None,
        help="Record this PR number on the reflog entry written by --ref.",
    )
    push.add_argument(
        "--title",
        default=None,
        help="Record this PR title on the reflog entry written by --ref (needs --pr).",
    )

    promote = sub.add_parser(
        "promote",
        help="Move `baseline` to the run that <ref> targets and append a reflog entry.",
        allow_abbrev=False,
    )
    promote.set_defaults(func=_cmd_promote)
    promote.add_argument("ref", help="Source ref to promote (for example pr/123).")
    _add_repository_flags(
        promote,
        required=False,
        url_help="Repository path or URL. Defaults to the configured remote.",
    )
    promote.add_argument(
        "--commit",
        default=None,
        help="Squash-merge commit SHA to record on the baseline reflog. Not "
        "recorded if baseline already points at the run.",
    )
    promote.add_argument(
        "--cleanup",
        action="store_true",
        default=False,
        help="After the promote, delete the source ref and its superseded runs. "
        "The promoted run survives, because baseline still reaches it.",
    )

    runs = sub.add_parser(
        "runs",
        help="List recorded runs, newest first.",
        allow_abbrev=False,
    )
    runs.set_defaults(func=_cmd_runs)
    _add_repository_flags(runs, required=True, url_help="Repository path or URL.")
    runs.add_argument(
        "--limit",
        type=_int_in_range(1, None, what="the limit"),
        default=20,
        help="How many runs to list (default 20).",
    )

    refs = sub.add_parser(
        "refs",
        help="List refs and the runs they point at.",
        allow_abbrev=False,
    )
    refs.set_defaults(func=_cmd_refs)
    _add_repository_flags(refs, required=True, url_help="Repository path or URL.")

    export = sub.add_parser(
        "export",
        help="Print a recorded run as JSON.",
        allow_abbrev=False,
    )
    export.set_defaults(func=_cmd_export)
    export.add_argument("run_id", help="The run id (ULID) to export.")
    _add_repository_flags(
        export, required=True, url_help="Repository path or URL the run lives in."
    )

    report = sub.add_parser(
        "report",
        help="Write a single-file HTML report of a recorded run.",
        allow_abbrev=False,
        description=(
            "Write one HTML file that shows a recorded run the way the "
            "dashboard does, with the run embedded, so it opens anywhere "
            "with no server and no network. With --against it shows the "
            "changes from that run to the reported one instead."
        ),
    )
    report.set_defaults(func=_cmd_report)
    subject = report.add_mutually_exclusive_group(required=True)
    subject.add_argument(
        "--run-id",
        dest="run_id",
        help="The run id (ULID) to report.",
    )
    subject.add_argument(
        "--ref",
        default=None,
        help="Report the run this ref points at, for example pr/123 or baseline.",
    )
    report.add_argument(
        "--against",
        default=None,
        metavar="RUN_ID_OR_REF",
        help="Embed this run too and render a comparison from it to the "
        "reported run. A run id names a run, anything else a ref, so "
        "`--against baseline` compares against the mainline. When the "
        "repository does not hold it, the report shows the run alone and "
        "a warning says so.",
    )
    _add_repository_flags(
        report,
        required=False,
        url_help="Repository path or URL. Defaults to the configured remote.",
    )
    report.add_argument(
        "--output",
        default=DEFAULT_REPORT_PATH,
        help=f"The file to write, or - for stdout (default {DEFAULT_REPORT_PATH}).",
    )

    ui = sub.add_parser(
        "ui",
        help="Serve the dashboard over the configured repositories.",
        allow_abbrev=False,
        description=(
            "Serve the dashboard and print its URL. The repositories "
            "come from the configuration, not from flags. Local is "
            "$EVALTRACK_LOCAL, then [tool.evaltrack].local, else "
            "./.evaltrack under the project. The remote is "
            "$EVALTRACK_REMOTE, then [tool.evaltrack].remote, and is mounted "
            "only when one of them names it."
        ),
    )
    ui.set_defaults(func=_cmd_ui)
    ui.add_argument(
        "--port",
        type=_int_in_range(1, 65535, what="a port (1-65535)"),
        default=8765,
        help="Bind port, 1-65535 (default 8765).",
    )
    ui.add_argument(
        "--run-id",
        default=None,
        dest="run_id",
        help="Point the printed URL at this run.",
    )

    return parser


# --- opening a repository ---


def _open_remote_repository(config: EvaltrackConfig) -> ConfiguredRemote:
    """The configured remote, required."""
    remote = resolve_remote(config)
    if remote is None:
        raise ValueError(
            f"no remote configured. Set ${REMOTE_ENV} or add "
            "[tool.evaltrack].remote to pyproject.toml. You can also name a "
            "repository with --repository <path-or-url>, --local or --remote."
        )
    return remote


@dataclass(frozen=True)
class _OpenedRepository:
    """An open repository and the URL it was opened from."""

    repository: RunRepository
    url: str


def _open_resolved_repository(args: argparse.Namespace) -> _OpenedRepository:
    """Open the repository the flags name, or the remote, echoed to stderr."""
    source: str | None = None
    if args.repository_url is not None:
        url = args.repository_url
        if not url.strip():
            # Not read as an absent flag. Falling back to the configured
            # remote would retarget the push without saying so.
            raise ValueError(
                "--repository is empty. Name a path or URL, or use --local or "
                "--remote for a configured repository. An unset shell variable "
                "expands to an empty string, so check the value you passed."
            )
    else:
        # Read once here, so a named repository costs no read at all.
        config = load_config()
        if args.local:
            url = resolve_local(config)
        elif args.remote:
            url = _open_remote_repository(config).url
        else:
            default = _open_remote_repository(config)
            url = default.url
            source = default.source
    repository = open_repository(url)
    # Echoed after the open, so a URL the open rejects is never announced.
    if source is not None:
        print(f"using repository {url} (from {source})", file=sys.stderr)
    return _OpenedRepository(repository, url)


# --- the commands ---


def _describe_run_errors(exc: ValidationError) -> str:
    """Render the rejected fields without their values, which are whatever file the
    caller named: a mistyped --run-file points at one holding secrets."""
    return "".join(
        f"\n  {'.'.join(str(part) for part in error['loc']) or 'file'}: {error['msg']}"
        for error in exc.errors(include_input=False)
    )


def _cmd_push(args: argparse.Namespace) -> int:
    ref_metadata_given = (
        args.commit is not None or args.pr is not None or args.title is not None
    )
    if args.ref is None and ref_metadata_given:
        print(
            "error: --commit/--pr/--title only apply to the reflog entry written "
            "by --ref. Pass --ref or drop them.",
            file=sys.stderr,
        )
        return 2
    if args.title is not None and args.pr is None:
        print(
            "error: --title records a PR title, so it needs --pr.",
            file=sys.stderr,
        )
        return 2
    run_path: Path = args.run_file
    if not run_path.exists():
        print(f"error: run file not found: {run_path}", file=sys.stderr)
        return 2
    if not run_path.is_file():
        print(f"error: not a file: {run_path}", file=sys.stderr)
        return 2
    # Parse before touching the repository, so an unreadable file fails before
    # anything is saved.
    try:
        run = parse_run_json(run_path.read_bytes())
    except ValidationError as exc:
        print(
            f"error: {run_path} does not parse as a run file:"
            f"{_describe_run_errors(exc)}",
            file=sys.stderr,
        )
        return 2

    target = _open_resolved_repository(args)
    if args.ref is not None:
        # Before the save, so an invalid name cannot leave an orphan run behind.
        target.repository.validate_ref_name(args.ref)
    target.repository.save_run(run)

    if args.ref is not None:
        target.repository.move_ref(
            args.ref,
            run.id,
            commit=args.commit,
            pr=args.pr,
            title=args.title,
        )
        print(f"pushed run {run.id} to {target.url}. ref {args.ref} -> {run.id}")
    else:
        print(f"pushed run {run.id} to {target.url}")
    return 0


def _cmd_promote(args: argparse.Namespace) -> int:
    if args.ref == BASELINE_REF:
        print(
            f"error: cannot promote {BASELINE_REF!r}: promoting the baseline "
            "onto itself does nothing, and --cleanup would delete the "
            "baseline ref. Pass a source ref such as pr/123.",
            file=sys.stderr,
        )
        return 2
    target = _open_resolved_repository(args)
    result = promote(
        target.repository, args.ref, commit=args.commit, cleanup=args.cleanup
    )
    baseline = result.baseline
    # Print the reflog's commit rather than the flag, since a re-promote keeps
    # the tip's stored commit.
    print(
        f"promoted {args.ref} -> baseline ({baseline.run_id})"
        + (f" at commit {_printable(baseline.commit)}" if baseline.commit else "")
    )
    if result.cleanup is not None:
        print(f"cleaned up {args.ref} and its superseded runs")
    return 0


def _cmd_runs(args: argparse.Namespace) -> int:
    target = _open_resolved_repository(args)
    listed = 0
    for listed, summary in enumerate(target.repository.list_runs(), start=1):
        print(_format_run(summary))
        if listed == args.limit:
            break
    if not listed:
        print(f"note: {target.url} holds no runs yet", file=sys.stderr)
    return 0


def _cmd_refs(args: argparse.Namespace) -> int:
    target = _open_resolved_repository(args)
    lines = _format_ref_lines(target.repository)
    for line in lines:
        print(line)
    if not lines:
        print(f"note: {target.url} holds no refs yet", file=sys.stderr)
    return 0


def _cmd_export(args: argparse.Namespace) -> int:
    target = _open_resolved_repository(args)
    run = target.repository.load_run(args.run_id)
    if run is None:
        # A missing run is a defined outcome, so exit 1, not the usage error 2.
        print(f"export: run {args.run_id!r} not found in {target.url}", file=sys.stderr)
        return 1
    print(dump_run_json(run).decode())
    return 0


@dataclass(frozen=True)
class _NamedRun:
    """A loaded run and the ref it was named by, when it was named by one."""

    run: RunRecord
    via: str | None


class _RunNotFound(Exception):
    """The repository does not hold the run a name picks. The message says
    which name, and where it was looked for."""


def _load_named_run(target: _OpenedRepository, name: str, *, as_ref: bool) -> _NamedRun:
    """The run `name` picks: the tip of that ref, or the run with that id.

    Raises:
        _RunNotFound: when the repository does not hold it.
    """
    via: str | None = None
    run_id = name
    if as_ref:
        tip = target.repository.get_ref(name)
        if tip is None:
            raise _RunNotFound(f"ref {name!r} not found in {target.url}")
        via, run_id = name, tip.run_id
    run = target.repository.load_run(run_id)
    if run is None:
        held_by = f" (the tip of ref {name!r})" if as_ref else ""
        raise _RunNotFound(f"run {run_id!r} not found in {target.url}{held_by}")
    return _NamedRun(run, via)


def _names_a_run_id(value: str) -> bool:
    try:
        ensure_run_id(value)
    except InvalidIdentifierError:
        return False
    return True


def _load_comparison(
    target: _OpenedRepository, subject: _NamedRun, *, against: str
) -> _NamedRun | None:
    """The run to compare `subject` against, or None, with a warning printed,
    when there is no comparison to make. A project's first pull request has no
    `baseline` yet, and a report of the run alone beats none in its artifacts."""
    # A canonical run id can only be a run id. A ref cannot tell the two
    # apart, so a ref named like one is unreachable here.
    try:
        loaded = _load_named_run(target, against, as_ref=not _names_a_run_id(against))
    except _RunNotFound as exc:
        print(
            f"warning: {exc}, so the report shows run {subject.run.id} alone",
            file=sys.stderr,
        )
        return None
    if loaded.run.id == subject.run.id:
        print(
            f"warning: {against!r} is run {subject.run.id} itself, so the report "
            "shows it alone rather than against itself",
            file=sys.stderr,
        )
        return None
    return loaded


def _cmd_report(args: argparse.Namespace) -> int:
    target = _open_resolved_repository(args)
    try:
        if args.run_id is not None:
            subject = _load_named_run(target, args.run_id, as_ref=False)
        else:
            subject = _load_named_run(target, args.ref, as_ref=True)
    except _RunNotFound as exc:
        # Like a missing export, a defined outcome, so exit 1.
        print(f"report: {exc}", file=sys.stderr)
        return 1
    against: _NamedRun | None = None
    if args.against is not None:
        against = _load_comparison(target, subject, against=args.against)
    data = collect_report_data(
        target.repository,
        subject.run,
        via=subject.via,
        against=against.run if against else None,
        against_via=against.via if against else None,
    )
    html = render_report(data)
    if args.output == "-":
        sys.stdout.write(html)
        return 0
    Path(args.output).write_text(html, encoding="utf-8")
    what = f"run {subject.run.id}"
    if against is not None:
        what += f" against {against.run.id}"
    print(f"wrote report of {what} to {args.output}")
    return 0


def _resolve_ui_mounts(config: EvaltrackConfig) -> list[tuple[str, RepositoryRole]]:
    """The (url, role) pairs `evaltrack ui` mounts, the remote only when configured."""
    mounts: list[tuple[str, RepositoryRole]] = [(resolve_local(config), "local")]
    remote = resolve_remote(config)
    if remote is not None:
        mounts.append((remote.url, "remote"))
    return mounts


def _cmd_ui(args: argparse.Namespace) -> int:
    # Imported late, so the other commands do not pay the FastAPI/uvicorn cost.
    try:
        import uvicorn

        from evaltrack.ui.app import MountedRepository, create_app
    except ImportError as exc:  # pragma: no cover - exercised only when extras missing
        print(
            f"error: the dashboard requires the `evaltrack[ui]` extra ({exc}).",
            file=sys.stderr,
        )
        return 2

    config = load_config()
    ui_url = _build_ui_url(args.port, args.run_id)

    # The role is the slug, since there is at most one mount per role.
    # A URL that carries a credential is rejected when it is opened, so an
    # opened URL is safe to print and an unopened one is not.
    repositories: dict[str, MountedRepository] = {}
    for url, role in _resolve_ui_mounts(config):
        try:
            repository = open_repository(url)
            if role == "local":
                # A network remote can take a minute to fail, and the dashboard
                # should be serving local runs before then. An unreachable remote
                # fails per request.
                repository.verify_available()
        except (ValueError, ImportError, RepositoryUnavailableError) as exc:
            if role == "local":
                raise
            print(
                f"warning: skipping the configured remote: {_first_line(exc)}. "
                "Serving the local repository only.",
                file=sys.stderr,
            )
            continue
        # Naming the repository shows a plugin and a dashboard that landed on
        # different pyproject.toml files.
        print(f"mounted {role} repository {url}", file=sys.stderr)
        repositories[role] = MountedRepository(
            url=url, repository=repository, role=role
        )

    app = create_app(repositories, pr_url_template=config.pr_url_template)

    # Printed before the server takes over the process, so the link is on
    # screen whether or not uvicorn binds.
    print(f"dashboard: {ui_url}", file=sys.stderr)
    uvicorn.run(app, host=_UI_HOST, port=args.port)
    return 0


# --- formatting output ---


def _format_run(run: RunSummary) -> str:
    """One line: run id, time, the checkout it ran on, its outcome, its branch."""
    commit = _printable(run.commit[:8]) if run.commit else "no-commit"
    if run.worktree_dirty:
        commit += "-dirty"
    if run.tests_total == 0:
        outcome = "no tests"
    elif run.tests_failed:
        outcome = f"{run.tests_failed}/{run.tests_total} failed"
    else:
        # The summary counts failures only. Skipped and expected-failure
        # tests are in the total, so the line cannot claim they all passed.
        outcome = f"{run.tests_total} tests, none failed"
    # 14 is the widest commit column, eight characters plus the dirty marker.
    fields = [run.id, _format_time(run.created_at), commit.ljust(14), outcome]
    if branch := run.labels.get("branch"):
        fields.append(f"({_printable(branch)})")
    return "  ".join(fields)


# Baseline first, since the mainline is what a reader looks for.
_REF_ORDER = {RefKind.BASELINE: 0, RefKind.PR: 1, RefKind.OTHER: 2}


def _format_ref_lines(repository: RunRepository) -> list[str]:
    """One ref per line, ordered by kind then name. An unreadable ref is listed
    without a target."""
    rows: list[tuple[Ref, str]] = []
    for name in repository.list_refs():
        try:
            tip = repository.get_ref(name)
        except (CorruptRecordError, InvalidIdentifierError):
            rows.append((Ref(name=name), "unreadable history"))
            continue
        rows.append((Ref(name=name, tip=tip), _format_ref_target(tip)))
    rows.sort(key=lambda row: (_REF_ORDER[row[0].kind], row[0].name))
    width = max((len(ref.name) for ref, _ in rows), default=0)
    return [f"{_printable(ref.name).ljust(width)}  {target}" for ref, target in rows]


def _format_ref_target(tip: ReflogEntry | None) -> str:
    """The run a ref points at, when it moved there, and the PR when the move recorded
    one."""
    if tip is None:
        return "no target"
    fields = [tip.run_id, _format_time(tip.moved_at)]
    if tip.pr is not None:
        fields.append(
            f"#{tip.pr}" if tip.title is None else f"#{tip.pr} {_printable(tip.title)}"
        )
    return "  ".join(fields)


def _printable(text: str) -> str:
    # A commit, a branch and a PR title all come from outside, so keep escape
    # sequences out of the terminal.
    return "".join(char if char.isprintable() else "?" for char in text)


def _format_time(when: datetime) -> str:
    """Timestamps in UTC, so runs recorded on different machines line up."""
    return when.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _first_line(exc: BaseException) -> str:
    """A storage SDK can raise a multi-line message. One line is enough for a warning."""
    text = str(exc).strip()
    return text.splitlines()[0].rstrip(".") if text else type(exc).__name__


def _build_ui_url(port: int, run_id: str | None) -> str:
    base = f"http://{_UI_HOST}:{port}/"
    return base if run_id is None else f"{base}?run={run_id}"
