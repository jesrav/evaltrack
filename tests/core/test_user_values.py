"""`degrade_undumpable` and the `UserValue`/`UserStr` annotations.

These pin the walk's own contract: what degrades, what is returned untouched,
and that the user's objects are never mutated. Whole-run behavior (one
undumpable value not costing the dump) lives in `test_run_record.py`.
"""

import asyncio
import dataclasses
import hashlib
from typing import Any

import pytest
from pydantic import BaseModel, Field

from evaltrack.core.user_values import UserStr, UserValue, degrade_undumpable

_BINARY = bytes([0xFF, 0x00, 0x91])
_SURROGATE = "ok \udcff"


def _make_fingerprint(value: bytes) -> dict[str, Any]:
    return {
        "$binary": {"sha256": hashlib.sha256(value).hexdigest(), "size": len(value)}
    }


def test_clean_values_are_returned_identically() -> None:
    """The same object, not only an equal one, so clean data is stored
    untouched."""
    value = {"scores": [1.0, 2.5], "labels": ("a", "b"), "tags": {"x"}}
    assert degrade_undumpable(value) is value


def test_a_lone_surrogate_string_degrades_to_its_repr() -> None:
    assert degrade_undumpable(_SURROGATE) == repr(_SURROGATE)


def test_encodable_text_is_kept() -> None:
    value = "æøå ✓"
    assert degrade_undumpable(value) is value


def test_decodable_bytes_are_kept() -> None:
    value = b"plain ascii"
    assert degrade_undumpable(value) is value


def test_binary_bytes_degrade_to_a_sha256_fingerprint() -> None:
    assert degrade_undumpable(_BINARY) == _make_fingerprint(_BINARY)
    assert degrade_undumpable(bytearray(_BINARY)) == _make_fingerprint(_BINARY)


def test_a_binary_dict_key_degrades_to_its_repr() -> None:
    """A JSON key must stay a string, so the fingerprint object cannot go
    there."""
    assert degrade_undumpable({_BINARY: "v"}) == {repr(_BINARY): "v"}


def test_the_back_edge_of_a_cycle_degrades_to_a_marker() -> None:
    value: list[Any] = [1]
    value.append(value)
    assert degrade_undumpable(value) == [1, {"$cycle": True}]


def test_a_value_that_merely_appears_twice_is_kept() -> None:
    """A repeated reference is legal JSON, so only a true back-edge is cut."""
    shared = {"k": "v"}
    value = [shared, shared]
    assert degrade_undumpable(value) is value


class _Node(BaseModel):
    name: str
    parent: "_Node | None" = None


def test_a_cycle_inside_a_model_degrades_to_a_dict_with_the_marker() -> None:
    """A model pointing back at itself cannot be dumped at all, not only not
    read back, so the walk must cut it. The user's model is left as it was."""
    original = _Node(name="root")
    original.parent = original
    assert degrade_undumpable(original) == {"name": "root", "parent": {"$cycle": True}}
    assert original.parent is original


@dataclasses.dataclass
class _Record:
    name: str
    parent: "_Record | None" = None


def test_a_cycle_inside_a_dataclass_degrades_to_a_dict_with_the_marker() -> None:
    original = _Record(name="root")
    original.parent = original
    assert degrade_undumpable(original) == {"name": "root", "parent": {"$cycle": True}}
    assert original.parent is original


def test_a_clean_model_is_kept_as_the_model() -> None:
    """Only a model holding something undumpable flattens. Everything else
    stores through its own type, computed fields and all."""
    original = _Node(name="fine", parent=_Node(name="up"))
    assert degrade_undumpable(original) is original


class _Credentials(BaseModel):
    """A model that excludes a field from pydantic's own dump."""

    prompt: str
    api_key: str = Field(exclude=True)
    blob: bytes = b"ok"


def test_an_excluded_field_stays_out_of_a_degraded_model() -> None:
    """The flat form must leave out what pydantic's dump leaves out. Otherwise one
    undumpable sibling exposes a field the user marked `exclude=True`."""
    original = _Credentials(prompt="p", api_key="sk-live", blob=_BINARY)
    out = degrade_undumpable(original)
    assert out == {"prompt": "p", "blob": _make_fingerprint(_BINARY)}


def test_a_tuple_survives_degrading_as_a_tuple() -> None:
    out = degrade_undumpable(("kept", _SURROGATE))
    assert out == ("kept", repr(_SURROGATE))


def test_clean_siblings_of_a_degraded_leaf_are_kept_identically() -> None:
    clean = {"kept": [1, 2]}
    value = {"clean": clean, "bad": _BINARY}
    out = degrade_undumpable(value)
    assert out == {"clean": clean, "bad": _make_fingerprint(_BINARY)}
    assert out["clean"] is clean


def test_the_users_object_is_not_mutated() -> None:
    inner: list[Any] = [_BINARY]
    value = {"nested": inner}
    degrade_undumpable(value)
    assert value["nested"] is inner
    assert inner[0] is _BINARY


def test_the_annotations_degrade_at_validation() -> None:
    """`UserValue` and `UserStr` apply the walk in practice. A field with
    either annotation degrades when the model validates."""

    class Holder(BaseModel):
        value: UserValue
        reason: UserStr

    holder = Holder(value=_BINARY, reason=_SURROGATE)
    assert holder.value == _make_fingerprint(_BINARY)
    assert holder.reason == repr(_SURROGATE)


# --- private fields ---


@dataclasses.dataclass
class _WithState:
    answer: str
    _state: dict[str, Any] = dataclasses.field(default_factory=dict)


def test_a_dataclass_is_stored_without_its_private_fields() -> None:
    value = _WithState(answer="yes", _state={"schemas": "x" * 1000})
    assert degrade_undumpable(value) == {"answer": "yes"}


def test_a_nested_dataclass_loses_its_private_fields_too() -> None:
    value = {"run": _WithState(answer="yes", _state={"big": 1})}
    assert degrade_undumpable(value) == {"run": {"answer": "yes"}}


def test_a_dataclass_without_private_fields_is_kept() -> None:
    @dataclasses.dataclass
    class _Plain:
        answer: str

    value = _Plain(answer="yes")
    assert degrade_undumpable(value) is value


def test_a_pydantic_ai_agent_run_is_stored_as_its_output() -> None:
    """The case that motivated the rule. An `AgentRunResult` keeps the agent's
    state, with every tool's JSON schema, in private fields."""
    pydantic_ai = pytest.importorskip("pydantic_ai")
    from pydantic_ai.models.test import TestModel

    def add(a: int, b: int) -> int:
        return a + b

    agent = pydantic_ai.Agent(
        TestModel(call_tools="all", custom_output_text="5"), tools=[add]
    )
    result = asyncio.run(agent.run("2 plus 3"))

    class _Holder(BaseModel):
        value: UserValue

    assert _Holder(value=result).value == {"output": "5"}
