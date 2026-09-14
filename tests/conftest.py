"""Shared fixtures. Report builders live in `factories.py`, fakes in `fakes.py`."""

import os

import pytest


@pytest.fixture(autouse=True)
def _clear_evaltrack_env(  # pyright: ignore[reportUnusedFunction]
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Start every test from a clean `EVALTRACK_*` slate.

    Suite-wide rather than per-module. A session that pytester spawns reads the
    real environment, so an exported `EVALTRACK_REMOTE` fails the precedence
    tests and acts on that real store. A test that needs one of these variables
    sets it with monkeypatch.
    """
    for key in list(os.environ):
        if key.startswith("EVALTRACK_"):
            monkeypatch.delenv(key)
