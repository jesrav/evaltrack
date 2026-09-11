"""Run builders and fixture types for the repository tests."""

import threading
from collections.abc import Callable

from evaltrack.core.run_record import RunRecord
from evaltrack.repositories import RunRepository
from evaltrack.repositories.store import ObjectStore

from ..factories import make_eval_run

StoreFactory = Callable[[], ObjectStore]
RepositoryFactory = Callable[[], RunRepository]


def make_run(
    commit: str | None = None, labels: dict[str, str] | None = None
) -> RunRecord:
    """A run with no tests."""
    return make_eval_run(commit=commit, labels=labels)


def race(*actions: Callable[[], object]) -> None:
    """Run the actions in threads that start together. Raise the first
    failure."""
    barrier = threading.Barrier(len(actions))
    failures: list[BaseException] = []

    def run(action: Callable[[], object]) -> None:
        barrier.wait()
        try:
            action()
        except BaseException as exc:
            failures.append(exc)

    threads = [threading.Thread(target=run, args=(action,)) for action in actions]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    if failures:
        raise failures[0]
