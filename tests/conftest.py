"""Shared fixtures. Report builders live in `factories.py`, fakes in `fakes.py`."""

import os
from pathlib import Path

import pytest

import evaltrack.report.page as report_module

from .report_support import TEMPLATE


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


@pytest.fixture
def report_template(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the report at a temp template, so its tests run whether or not
    this checkout has a frontend build. In a directory of its own, clear of
    what a test writes into `tmp_path`."""
    path = tmp_path / "report-template" / "report.html"
    path.parent.mkdir()
    path.write_text(TEMPLATE, encoding="utf-8")
    monkeypatch.setattr(report_module, "TEMPLATE_PATH", path)
    return path
