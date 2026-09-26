"""The run view that the dashboard opens.

The view is the stored run with each large `inputs`, `expected_output`,
`metadata` and attempt `output` replaced by an envelope, `{"$deferred": {...}}`.
The envelope holds a preview, the size, a hash, and the address of the whole
value. The hash lets the dashboard compare two runs without the values. It
covers the part of the value that the dashboard compares for a small value, so
that a large value and a small one count as changed for the same reasons. The
`raw_results` that a run recorded before 0.3.0 can hold are left out. Everything
else is as stored.
"""

import hashlib
import json
from typing import Any

from evaltrack.core.run_record import (
    CaseRecord,
    RunRecord,
    dump_plain,
    dump_plain_json,
)

INLINE_VALUE_BYTES: int = 16 * 1024
"""The stored size above which a value is deferred. A model's answer stays
inline, so the common case needs no second request."""

DEFERRED_KEY = "$deferred"

PREVIEW_CHARS = 200

# The case fields that the dashboard shows in a cell.
_CASE_FIELDS = ("inputs", "expected_output", "metadata")

# The keys the dashboard unwraps to find the answer in a wrapped result. Keep
# them the same as `getPrimaryView` in the frontend, so that a preview shows
# what a cell shows.
_RESULT_KEYS = frozenset({"output", "result", "answer", "content"})


def _primary_view(value: Any, depth: int = 0) -> Any:
    if depth >= 4 or not isinstance(value, dict):
        return value
    public = [k for k in value if not str(k).startswith("_")]
    if len(public) == 1 and public[0] in _RESULT_KEYS:
        return _primary_view(value[public[0]], depth + 1)
    return value


def _build_preview(value: Any, data: bytes) -> str:
    primary = _primary_view(value)
    if isinstance(primary, str):
        return primary[:PREVIEW_CHARS]
    text = data if primary is value else dump_plain_json(primary)
    return text.decode()[:PREVIEW_CHARS]


def _hash_primary_view(value: Any) -> str:
    """The hash of the primary view, with keys sorted, so that key order is no
    change."""
    plain = json.loads(dump_plain_json(_primary_view(value)))
    text = json.dumps(plain, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode()).hexdigest()


def _defer(value: Any, *, limit: int, address: dict[str, Any]) -> Any:
    """`value` itself when it stores small, else its envelope."""
    if value is None:
        return value
    data = dump_plain_json(value)
    if len(data) <= limit:
        return value
    return {
        DEFERRED_KEY: {
            "preview": _build_preview(value, data),
            "size": len(data),
            "sha256": _hash_primary_view(value),
            **address,
        }
    }


def dump_run_view_json(run: RunRecord, *, limit: int = INLINE_VALUE_BYTES) -> bytes:
    """The run as the dashboard opens it. Every value over `limit` bytes is
    replaced by its envelope."""
    plain = dump_plain(run)
    for nodeid, test in plain["tests"].items():
        test.pop("raw_results", None)
        for case_id, case in test["cases"].items():
            address = {"run": run.id, "test": nodeid, "case": case_id}
            for field in _CASE_FIELDS:
                case[field] = _defer(
                    case[field], limit=limit, address={**address, "field": field}
                )
            for index, attempt in enumerate(case["attempts"]):
                attempt["output"] = _defer(
                    attempt["output"],
                    limit=limit,
                    address={**address, "field": "output", "attempt": index},
                )
    return dump_plain_json(plain)


def dump_case_json(case: CaseRecord) -> bytes:
    """One case whole, for the values the run view deferred."""
    return dump_plain_json(dump_plain(case))
