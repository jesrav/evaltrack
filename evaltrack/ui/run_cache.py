"""Keeps the last few runs the dashboard read, so that a run is not read from
storage again for each of its panes.

Opening a run, and then its panes, asks the server for the same run several
times. On a remote repository each read downloads the whole stored run, which
can be large. So the server keeps the last few runs it read, ready to use.

A run does not change after it is saved, so a kept run stays correct until the
dashboard deletes it. A run deleted outside the dashboard, for example with the
CLI, can still show until it leaves the cache or `evaltrack ui` restarts.
"""

import threading
from collections import OrderedDict
from collections.abc import Iterable

from evaltrack.core.run_record import RunRecord
from evaltrack.repositories import RunRepository

DEFAULT_SIZE = 4
"""Enough for two runs side by side in a diff, with room to go back and forth.
A parsed run takes several times its stored size in memory, so the count stays
small."""


class RunCache:
    """The last few runs the dashboard read, by repository and run id. When it is
    full, the run used least recently is dropped."""

    def __init__(self, *, size: int = DEFAULT_SIZE) -> None:
        self._size = size
        self._runs: OrderedDict[tuple[str, str], RunRecord] = OrderedDict()
        self._lock = threading.Lock()

    def load(self, slug: str, *, repo: RunRepository, run_id: str) -> RunRecord | None:
        """The run from the cache, or from `repo` when the cache does not have it.
        When `repo` does not have the run either, nothing is cached. The run can
        be pushed while the dashboard is open, and the next load then finds it."""
        key = (slug, run_id)
        with self._lock:
            run = self._runs.get(key)
            if run is not None:
                self._runs.move_to_end(key)
                return run
        # Read outside the lock, so that a slow read does not hold up other runs.
        run = repo.load_run(run_id)
        if run is None:
            return None
        with self._lock:
            self._runs[key] = run
            self._runs.move_to_end(key)
            while len(self._runs) > self._size:
                self._runs.popitem(last=False)
        return run

    def drop(self, slug: str, *, run_ids: Iterable[str]) -> None:
        """Forget the given runs."""
        with self._lock:
            for run_id in run_ids:
                self._runs.pop((slug, run_id), None)
