"""Shared by the tests of the single-file report."""

from typing import Any


def named_run(data: dict[str, object], side: str = "run") -> dict[str, Any]:
    """One side of the embedded report, `run` or `against`, as the page reads
    it: the run and the ref it was reached by."""
    named = data[side]
    assert isinstance(named, dict), f"the page carries no {side!r} side"
    return named  # pyright: ignore[reportUnknownVariableType]
