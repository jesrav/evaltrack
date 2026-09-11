"""The errors whose message a reader has to act on.

Most evaltrack errors carry whatever their raiser passes. Two of them build a
message themselves, so the wording is theirs to get right.
"""

from evaltrack.core.errors import RefNotFoundError, RunReferencedError


def test_a_missing_ref_names_itself() -> None:
    error = RefNotFoundError("pr/9")
    assert error.name == "pr/9"
    assert "pr/9" in str(error)


def test_a_referenced_run_names_what_to_drop_first() -> None:
    """The caller cannot delete the run until it drops these, so the message
    has to list them rather than say the run is in use."""
    error = RunReferencedError("01ABC", ["baseline", "pr/7"])
    assert error.run_id == "01ABC"
    assert error.refs == ["baseline", "pr/7"]
    assert "baseline, pr/7" in str(error)
