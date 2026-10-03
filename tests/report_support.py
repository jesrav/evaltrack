"""Shared by the tests of the single-file report."""

import json
import re
from typing import Any

# What a built page carries: the element the report fills, inside markup the
# browser needs. The build's real output is larger, and the tests only need
# the slot.
TEMPLATE = (
    "<!doctype html><html><head><title>evaltrack report</title></head><body>"
    '<div id="root"></div>'
    '<script type="application/json" id="evaltrack-data"></script>'
    "<script>window.rendered = true;</script></body></html>"
)


def embedded_report_json(html: str) -> dict[str, object]:
    """The report's data as the page reads it: the one JSON element's text."""
    matches = re.findall(
        r'<script type="application/json" id="evaltrack-data">(.*?)</script>',
        html,
        flags=re.DOTALL,
    )
    assert len(matches) == 1, "the page carries exactly one data element"
    return json.loads(matches[0])


def named_run(data: dict[str, object], side: str = "run") -> dict[str, Any]:
    """One side of the embedded report, `run` or `against`, as the page reads
    it: the run and the ref it was reached by."""
    named = data[side]
    assert isinstance(named, dict), f"the page carries no {side!r} side"
    return named  # pyright: ignore[reportUnknownVariableType]
