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
    """The data of the report, parsed from the one JSON element of the page."""
    matches = re.findall(
        r'<script type="application/json" id="evaltrack-data">(.*?)</script>',
        html,
        flags=re.DOTALL,
    )
    assert len(matches) == 1, "the page carries exactly one data element"
    return json.loads(matches[0])


def embedded_run(data: dict[str, object]) -> dict[str, Any]:
    """The reported run, as the page reads it."""
    run = data["run"]
    assert isinstance(run, dict), "the page carries no run"
    return run  # pyright: ignore[reportUnknownVariableType]


def recorded_output(data: dict[str, object]) -> object:
    """The output of the one case the report's fixture run records."""
    run = embedded_run(data)
    return run["tests"]["test_x"]["cases"]["test_case"]["attempts"][0]["output"]
