"""Fixtures and setup helpers shared by the dashboard tests."""

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from evaltrack.config import PrUrlTemplate
from evaltrack.core.eval_round import EvalRound
from evaltrack.core.recorder import EvalRecorder
from evaltrack.core.run_context import RunContext
from evaltrack.core.run_record import MarkerSettings, RunRecord, TestOutcome
from evaltrack.core.score_bars import apply_score_bars
from evaltrack.repositories import RunRepository
from evaltrack.ui import MountedRepository, create_app

from ..factories import make_round
from ..fakes import MemoryStore


def make_recorded_run(
    eval_round: EvalRound | None = None,
    *,
    commit: str | None = None,
    test: str = "test_x",
    outcome: TestOutcome | None = None,
    **settings: Any,
) -> RunRecord:
    """A run recording `eval_round` (default: one passing case) under one test.

    `settings` holds `MarkerSettings` fields (score_bars, reliability_target,
    eval_version).
    The bars are resolved onto the results first, the way a recorded run carries
    them.
    """
    rec = EvalRecorder(RunContext(commit=commit), keep_raw_results=True)
    eval_settings = MarkerSettings(**settings)
    rec.add_round(
        test,
        apply_score_bars(
            eval_round if eval_round is not None else make_round(),
            eval_settings.score_bars,
        ),
        settings=eval_settings,
    )
    if outcome is not None:
        rec.set_test_outcome(test, outcome)
    return rec.to_run_record()


@pytest.fixture
def populated_repository() -> RunRepository:
    repo = RunRepository(MemoryStore())
    for i in range(3):
        repo.save_run(make_recorded_run(make_round(), commit=f"c{i}"))
    # Stage a couple of refs so reflog endpoints have something to return.
    summaries = list(repo.list_runs())
    repo.move_ref("baseline", summaries[0].id, commit="c0", pr=1)
    repo.move_ref("pr/42", summaries[1].id, commit="c1", pr=42)
    return repo


def make_client(app: FastAPI, *, raise_server_exceptions: bool = True) -> TestClient:
    """The app rejects a Host that is not loopback, and the default Host of
    TestClient is `testserver`. The base URL must therefore be a loopback one.

    `raise_server_exceptions=False` lets a test read the 500 response instead
    of the exception behind it.
    """
    return TestClient(
        app,
        base_url="http://127.0.0.1",
        raise_server_exceptions=raise_server_exceptions,
    )


def make_repo_app(
    repo: RunRepository, *, pr_url_template: PrUrlTemplate | None = None
) -> FastAPI:
    """A single-mount app over `repo`, mounted as slug `main`."""
    return create_app(
        {"main": MountedRepository(url="/x", repository=repo, role="local")},
        pr_url_template=pr_url_template,
    )


def make_repo_client(
    repo: RunRepository, *, raise_server_exceptions: bool = True
) -> TestClient:
    """A client over a single-mount app, for tests that assert against `repo`."""
    return make_client(
        make_repo_app(repo), raise_server_exceptions=raise_server_exceptions
    )


@pytest.fixture
def client_factory(
    populated_repository: RunRepository,
) -> Iterator[TestClient]:
    with make_repo_client(populated_repository) as client:
        yield client


@dataclass(frozen=True)
class CorruptReflogRepo:
    """A repo whose `ref` has one good reflog entry and a torn line 2.

    `run_id` is the run the good entry points at.
    """

    repo: RunRepository
    run_id: str


def make_corrupt_reflog_repository(*, ref: str = "pr/1") -> CorruptReflogRepo:
    store = MemoryStore()
    repo = RunRepository(store)
    run = make_recorded_run(make_round(), commit="c0")
    repo.save_run(run)
    repo.move_ref(ref, run.id, pr=1)
    store.append(b'{"run_id": "01KXB8', f"refs/{ref}.log.jsonl")
    return CorruptReflogRepo(repo, run.id)
