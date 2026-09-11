"""Storing and loading a `RunRecord`, including values JSON cannot encode."""

import asyncio
import hashlib
import json
import warnings
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    computed_field,
)
from pydantic.dataclasses import dataclass as pydantic_dataclass
from pydantic_evals import Case, Dataset, increment_eval_metric
from ulid import ULID

from evaltrack.core.errors import InvalidIdentifierError, UnsupportedSchemaError
from evaltrack.core.recorder import EvalRecorder
from evaltrack.core.results import (
    EvaluatorInfo,
    EvaluatorResult,
)
from evaltrack.core.run_record import (
    RUN_SCHEMA_VERSION,
    AttemptRecord,
    CaseRecord,
    MarkerSettings,
    RecordedTest,
    RunRecord,
    dump_run_json,
    ensure_run_id,
    parse_run_json,
)

from ..factories import (
    RECORDED_BY,
    make_attempt,
    make_eval_run,
    make_round,
    translate_report,
)


class _Opaque:
    def __init__(self, x: int) -> None:
        self.x = x

    def __repr__(self) -> str:
        return f"_Opaque(x={self.x})"


def _read_stored_attempt(case: dict[str, Any]) -> dict[str, Any]:
    """The single stored attempt of a case that ran once."""
    return case["attempts"][0]


def _make_run_with_opaque_values() -> RunRecord:
    return make_eval_run(
        tests={
            "test_x.py::test_a": RecordedTest(
                cases={
                    "c": CaseRecord(
                        inputs="x",
                        metadata=_Opaque(1),
                        attempts=[
                            AttemptRecord(
                                outcome="passed",
                                task_duration=0.0,
                                output=_Opaque(2),
                            )
                        ],
                    )
                }
            )
        },
    )


def test_dump_run_json_degrades_unserializable_values_to_repr() -> None:
    """A user object that JSON cannot encode (a task output, case metadata)
    must not crash the dump. A crash loses the whole recorded run at session
    end."""
    data = dump_run_json(_make_run_with_opaque_values())
    case = json.loads(data)["tests"]["test_x.py::test_a"]["cases"]["c"]
    assert case["metadata"] == "_Opaque(x=1)"
    assert _read_stored_attempt(case)["output"] == "_Opaque(x=2)"


def test_dump_run_json_output_round_trips() -> None:
    """The repr fallback still yields valid run JSON. Values that JSON can
    encode stay untouched, and the dump loads back."""
    restored = parse_run_json(dump_run_json(_make_run_with_opaque_values()))
    test = restored.tests["test_x.py::test_a"]
    case = test.cases["c"]
    assert case.inputs == "x"
    assert case.metadata == "_Opaque(x=1)"


_BINARY = b"\x89PNG\r\n\x1a\n\xff\xfe"
_SURROGATE = "response\ud800"


def _make_fingerprint(data: bytes) -> dict[str, Any]:
    """The stored form of a binary value: content hash and size, not content."""
    return {"$binary": {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}}


def _make_run_with_case(case: CaseRecord) -> RunRecord:
    return make_eval_run(
        tests={"test_x.py::test_a": RecordedTest(cases={"c": case})},
    )


def _make_run_with_binary_values() -> RunRecord:
    return _make_run_with_case(
        CaseRecord(
            inputs={"image": _BINARY, "prompt": "describe"},
            metadata={_BINARY: "keyed by bytes"},
            attempts=[
                AttemptRecord(
                    outcome="passed",
                    task_duration=0.0,
                    output=["ok", bytearray(b"\x00\xff"), ("nested", _BINARY)],
                )
            ],
        )
    )


def test_dump_run_json_degrades_binary_values_but_keeps_structure() -> None:
    """pydantic stores bytes as UTF-8 text and raises on binary input, which
    the dump's repr fallback never intercepts. A crash there loses the whole
    run. The binary leaf degrades to a sha256+size fingerprint, which supports
    display and diffing where a repr of the content does not. The structure
    around it, other values included, survives verbatim. A dict key degrades
    to repr instead, because a JSON key must stay a string."""
    data = dump_run_json(_make_run_with_binary_values())
    case = json.loads(data)["tests"]["test_x.py::test_a"]["cases"]["c"]
    assert case["inputs"] == {"image": _make_fingerprint(_BINARY), "prompt": "describe"}
    assert case["metadata"] == {repr(_BINARY): "keyed by bytes"}
    assert _read_stored_attempt(case)["output"] == [
        "ok",
        _make_fingerprint(b"\x00\xff"),
        ["nested", _make_fingerprint(_BINARY)],
    ]


def test_binary_values_round_trip() -> None:
    """The degraded form is plain JSON, so the saved run loads back and a
    load-save copy does not drift."""
    data = dump_run_json(_make_run_with_binary_values())
    loaded = parse_run_json(data)
    case = loaded.tests["test_x.py::test_a"].cases["c"]
    assert case.inputs == {"image": _make_fingerprint(_BINARY), "prompt": "describe"}
    assert dump_run_json(loaded) == data, "the round trip must be byte-stable"


def test_dump_run_json_degrades_lone_surrogate_strings() -> None:
    """A lone surrogate (a sloppy decode of LLM output) has no UTF-8 form, so
    the dump raises on it like on binary bytes. It degrades to its repr
    everywhere user text lands, an LLM judge's reason included."""
    run = _make_run_with_case(
        CaseRecord(
            inputs={_SURROGATE: "keyed by it", "text": _SURROGATE},
            attempts=[
                AttemptRecord(
                    outcome="passed",
                    task_duration=0.0,
                    output=_SURROGATE,
                    results={
                        "acc": EvaluatorResult(
                            value=1.0,
                            reason=_SURROGATE,
                            evaluator=EvaluatorInfo(name="acc"),
                        )
                    },
                )
            ],
        )
    )
    data = dump_run_json(run)
    case = json.loads(data)["tests"]["test_x.py::test_a"]["cases"]["c"]
    assert case["inputs"] == {repr(_SURROGATE): "keyed by it", "text": repr(_SURROGATE)}
    attempt = _read_stored_attempt(case)
    assert attempt["output"] == repr(_SURROGATE)
    assert attempt["results"]["acc"]["reason"] == repr(_SURROGATE)
    assert parse_run_json(data).tests, "the saved run must load back"


def test_decodable_bytes_still_store_as_their_text() -> None:
    """Only a value with no UTF-8 form degrades. Bytes that decode still store
    as the decoded text."""
    run = _make_run_with_case(
        CaseRecord(
            inputs="x",
            attempts=[AttemptRecord(outcome="passed", task_duration=0.0, output=b"ok")],
        )
    )
    case = json.loads(dump_run_json(run))["tests"]["test_x.py::test_a"]["cases"]["c"]
    assert _read_stored_attempt(case)["output"] == "ok"


def test_dump_run_json_stores_a_set_as_an_array() -> None:
    """A set has no JSON form of its own. It is stored as an array, and the
    array loads back and dumps the same bytes again."""
    run = _make_run_with_case(
        CaseRecord(
            inputs={"tags": frozenset({"a"})},
            attempts=[
                AttemptRecord(outcome="passed", task_duration=0.0, output={1, 2})
            ],
        )
    )
    data = dump_run_json(run)
    case = json.loads(data)["tests"]["test_x.py::test_a"]["cases"]["c"]
    assert case["inputs"] == {"tags": ["a"]}
    assert sorted(_read_stored_attempt(case)["output"]) == [1, 2]
    loaded = parse_run_json(data)
    assert dump_run_json(loaded) == data, "the round trip must be byte-stable"


class _SdkResponse(BaseModel):
    """A task output that keeps unknown provider fields, like LLM SDK models."""

    model_config = ConfigDict(extra="allow")

    text: str


async def _extra_field_task(inputs: str) -> _SdkResponse:
    return _SdkResponse.model_validate(
        {"text": "fine", "raw_payload": _SURROGATE, "blob": _BINARY}
    )


def test_dump_run_json_degrades_model_extra_fields() -> None:
    """Undumpable values in a model's extra fields must degrade like declared
    ones, or one provider-added field loses the run at session end. The user's
    own object stays untouched, and the raw result degrades the same way."""
    nodeid = "tests/test_extra.py::test_extra"
    dataset = Dataset[str, Any, Any](name="d", cases=[Case(name="c", inputs="x")])
    report = asyncio.run(dataset.evaluate(_extra_field_task))

    recorder = EvalRecorder(keep_raw_results=True)
    recorder.add_round(nodeid, translate_report(report), raw_result=report)
    recorder.set_test_outcome(nodeid, "passed")
    data = dump_run_json(recorder.to_run_record())

    expected_degraded = {
        "text": "fine",
        "raw_payload": repr(_SURROGATE),
        "blob": _make_fingerprint(_BINARY),
    }
    stored = json.loads(data)["tests"][nodeid]
    assert _read_stored_attempt(stored["cases"]["c"])["output"] == expected_degraded
    assert stored["raw_results"][0]["cases"][0]["output"] == expected_degraded
    assert report.cases[0].output.raw_payload == _SURROGATE, (
        "the degrade must not touch the user's object"
    )
    assert report.cases[0].output.blob == _BINARY, (
        "the degrade must not touch the user's object"
    )
    parse_run_json(data)


class _DerivedResponse(BaseModel):
    x: str

    @computed_field
    @property
    def derived(self) -> str:
        return self.x + _SURROGATE


def test_dump_run_json_saves_a_run_the_degrade_walk_cannot_clean() -> None:
    """An undumpable value inside a user's own model (here a computed field)
    must still not lose the run. The dump keeps the non-finite float convention,
    and the saved run loads back."""
    run = _make_run_with_case(
        CaseRecord(
            inputs="x",
            metadata={"budget": float("inf")},
            attempts=[
                AttemptRecord(
                    outcome="passed",
                    task_duration=0.0,
                    output=_DerivedResponse(x="ok"),
                )
            ],
        )
    )
    data = dump_run_json(run)
    case = json.loads(data)["tests"]["test_x.py::test_a"]["cases"]["c"]
    out = _read_stored_attempt(case)["output"]
    assert out["x"] == "ok"
    assert out["derived"] == repr("ok" + _SURROGATE)
    assert case["metadata"] == {"budget": "Infinity"}
    restored = parse_run_json(data)
    restored_case = restored.tests["test_x.py::test_a"].cases["c"]
    assert restored_case.attempts[0].output["derived"] == repr("ok" + _SURROGATE)


class _ImageAnswer(BaseModel):
    """A model whose computed field reads the field that degrades. Multimodal
    SDK types are built this way, so a case input carrying an image has this
    shape."""

    image: bytes

    @computed_field
    @property
    def digest(self) -> str:
        return hashlib.sha256(self.image).hexdigest()


@pydantic_dataclass
class _ImagePart:
    """The same shape as a dataclass, which is how pydantic-ai spells it."""

    data: bytes

    @computed_field
    @property
    def digest(self) -> str:
        return hashlib.sha256(self.data).hexdigest()


@pytest.mark.parametrize(
    ("carrier", "field_name"),
    [(_ImageAnswer(image=_BINARY), "image"), (_ImagePart(data=_BINARY), "data")],
    ids=["model", "dataclass"],
)
def test_an_object_whose_computed_field_reads_a_degraded_field_round_trips(
    carrier: Any, field_name: str
) -> None:
    """An image in a case input is binary, so it degrades. The object around it
    then holds a fingerprint where it declares bytes. Stored as itself, that
    object makes the dump read the declared type and the computed field over
    data that fits neither, and the session loses every eval. The
    degraded object is stored as a plain dict instead."""
    run = _make_run_with_case(
        CaseRecord(
            inputs=carrier,
            attempts=[
                AttemptRecord(outcome="passed", task_duration=0.0, output="described")
            ],
        )
    )
    data = dump_run_json(run)
    stored = json.loads(data)["tests"]["test_x.py::test_a"]["cases"]["c"]["inputs"]
    assert stored[field_name] == _make_fingerprint(_BINARY)
    assert parse_run_json(data).tests, "the saved run must load back"


class _Score(BaseModel):
    """A user's own model, of the kind an SDK builds without validating."""

    value: int


def test_a_serializer_warning_does_not_cost_the_run() -> None:
    """pydantic warns when a value does not match the type its field declares,
    and a user's object holds whatever the user put in it. Projects escalate
    warnings to errors, and the save runs inside that filter, so a warning
    there takes every eval in the session with it."""
    run = _make_run_with_case(
        CaseRecord(
            inputs="x",
            attempts=[
                AttemptRecord(
                    outcome="passed",
                    task_duration=0.0,
                    output=_Score.model_construct(value="high"),
                )
            ],
        )
    )
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        data = dump_run_json(run)
    case = json.loads(data)["tests"]["test_x.py::test_a"]["cases"]["c"]
    assert _read_stored_attempt(case)["output"] == {"value": "high"}
    assert parse_run_json(data).tests, "the saved run must load back"


class _Response(BaseModel):
    """An SDK's response type, which the SDK builds without validating."""

    text: str
    usage: dict[str, int]


def test_a_constructed_model_with_an_unset_field_records_and_dumps() -> None:
    """A model built with `model_construct` can leave a required field unset.
    pydantic's own dump omits the field, and recording the attempt must not do
    worse, or a crash inside evaltrack loses that round."""
    partial = _Response.model_construct(text="hi")
    nodeid = "tests/test_partial.py::test_partial"
    recorder = EvalRecorder()
    recorder.add_round(nodeid, make_round(attempts=[make_attempt(output=partial)]))
    recorder.set_test_outcome(nodeid, "passed")
    data = dump_run_json(recorder.to_run_record())
    case = json.loads(data)["tests"][nodeid]["cases"]["test_case"]
    assert _read_stored_attempt(case)["output"] == {"text": "hi"}
    assert parse_run_json(data).tests, "the saved run must load back"


class _Credentials(BaseModel):
    """A task output that excludes a field from pydantic's own dump and masks
    another, beside a field that can hold binary."""

    prompt: str
    api_key: str = Field(exclude=True)
    token: SecretStr
    blob: bytes = b"ok"


def _dump_run_with_output(output: Any) -> bytes:
    run = _make_run_with_case(
        CaseRecord(
            inputs="x",
            attempts=[
                AttemptRecord(outcome="passed", task_duration=0.0, output=output)
            ],
        )
    )
    return dump_run_json(run)


# Both dump paths, the one pydantic serializes itself and the fallback that a
# binary sibling forces.
_CREDENTIAL_OUTPUTS = [
    pytest.param(
        _Credentials(prompt="p", api_key="sk-live", token=SecretStr("tok-456")),
        id="clean",
    ),
    pytest.param(
        _Credentials(
            prompt="p", api_key="sk-live", token=SecretStr("tok-456"), blob=_BINARY
        ),
        id="degraded",
    ),
]


@pytest.mark.parametrize("output", _CREDENTIAL_OUTPUTS)
def test_an_excluded_field_stays_out_of_the_dumped_run(output: _Credentials) -> None:
    """A field that the user marked `exclude=True` must not reach the stored run
    on either path. Otherwise one undumpable sibling exposes what the model
    excludes."""
    data = _dump_run_with_output(output)
    case = json.loads(data)["tests"]["test_x.py::test_a"]["cases"]["c"]
    stored = _read_stored_attempt(case)["output"]
    assert "api_key" not in stored
    assert stored["prompt"] == "p"
    assert b"sk-live" not in data


@pytest.mark.parametrize("output", _CREDENTIAL_OUTPUTS)
def test_a_secret_field_is_masked_in_the_dumped_run(output: _Credentials) -> None:
    """pydantic masks a `SecretStr` when it dumps, and the fallback path must
    expose no more than the model does."""
    data = _dump_run_with_output(output)
    assert b"tok-456" not in data
    assert parse_run_json(data).tests, "the saved run must load back"


class _Node(BaseModel):
    name: str
    parent: "_Node | None" = None


def test_dump_run_json_cuts_a_cycle_inside_a_model_output() -> None:
    """A task output that points back at itself fails pydantic's own dump, which
    loses every eval in the session. The cycle is cut at record time and the
    saved run loads back."""
    node = _Node(name="root")
    node.parent = node
    data = _dump_run_with_output(node)
    restored = parse_run_json(data)
    output = restored.tests["test_x.py::test_a"].cases["c"].attempts[0].output
    assert output == {"name": "root", "parent": {"$cycle": True}}


class _Unrepresentable:
    """A task output whose `__repr__` raises, as a client's response object does
    once its connection is closed."""

    def __repr__(self) -> str:
        raise RuntimeError("connection closed")


def test_dump_run_json_stands_in_for_an_output_whose_repr_raises() -> None:
    """The repr fallback is the dump's last resort, so a `__repr__` that raises
    fails the dump and loses every eval in the session. The stored stand-in
    names the type, and the saved run loads back."""
    run = _make_run_with_case(
        CaseRecord(
            inputs="x",
            attempts=[
                AttemptRecord(
                    outcome="passed", task_duration=0.0, output=_Unrepresentable()
                )
            ],
        )
    )
    data = dump_run_json(run)
    restored = parse_run_json(data)
    output = restored.tests["test_x.py::test_a"].cases["c"].attempts[0].output
    assert output == "<unrepresentable _Unrepresentable>"


def test_degrade_replaces_only_the_back_edge_of_a_cycle() -> None:
    """A cycle has no JSON form, so the back-edge is replaced and everything
    around it is kept. Degrading the whole value throws away data the user can
    still read. A cycle left in place loses the entire run at the dump."""
    node: dict[str, Any] = {"name": "root", "children": []}
    node["children"].append({"name": "leaf", "parent": node})
    loop: list[Any] = ["a"]
    loop.append(loop)
    run = _make_run_with_case(
        CaseRecord(
            inputs="x",
            attempts=[
                AttemptRecord(
                    outcome="passed",
                    task_duration=0.0,
                    output={"tree": node, "loop": loop},
                )
            ],
        )
    )
    case = json.loads(dump_run_json(run))["tests"]["test_x.py::test_a"]["cases"]["c"]
    out = _read_stored_attempt(case)["output"]
    assert out["tree"] == {
        "name": "root",
        "children": [{"name": "leaf", "parent": {"$cycle": True}}],
    }
    assert out["loop"] == ["a", {"$cycle": True}]


def test_cycle_marker_round_trips() -> None:
    """The degrade is one-way, like the binary fingerprint. A load gives back a
    plain `{"$cycle": true}` dict and does not restore the reference, so a
    load-save copy does not drift and cannot reintroduce a value that fails the
    dump."""
    loop: list[Any] = ["a"]
    loop.append(loop)
    run = _make_run_with_case(
        CaseRecord(
            inputs="x",
            attempts=[AttemptRecord(outcome="passed", task_duration=0.0, output=loop)],
        )
    )
    data = dump_run_json(run)
    loaded = parse_run_json(data)
    output = loaded.tests["test_x.py::test_a"].cases["c"].attempts[0].output
    assert output == ["a", {"$cycle": True}]
    assert output[1] is not output, "the marker is a plain dict, not a rebuilt cycle"
    assert dump_run_json(loaded) == data, "the round trip must be byte-stable"


async def _binary_task(inputs: str) -> bytes:
    return _BINARY


def test_dump_run_json_round_trips_a_binary_task_output() -> None:
    """A task's raw-bytes output lands both in the structured attempts and in
    the kept raw result. Both must degrade, the report object the user still
    holds must not be touched, and the saved run must load back."""
    nodeid = "tests/test_binary.py::test_binary"
    dataset = Dataset[str, Any, Any](name="d", cases=[Case(name="c", inputs="x")])
    report = asyncio.run(dataset.evaluate(_binary_task))

    recorder = EvalRecorder(keep_raw_results=True)
    recorder.add_round(nodeid, translate_report(report), raw_result=report)
    recorder.set_test_outcome(nodeid, "passed")
    data = dump_run_json(recorder.to_run_record())

    assert report.cases[0].output == _BINARY, (
        "the degrade must not touch the report the user holds"
    )
    stored = json.loads(data)["tests"][nodeid]
    assert _read_stored_attempt(stored["cases"]["c"])["output"] == _make_fingerprint(
        _BINARY
    )
    assert stored["raw_results"][0]["cases"][0]["output"] == _make_fingerprint(_BINARY)
    restored = parse_run_json(data)
    assert restored.tests[nodeid].cases["c"].attempts[0].output == _make_fingerprint(
        _BINARY
    )


async def _task_reporting_an_infinite_metric(inputs: str) -> str:
    increment_eval_metric("tokens", float("inf"))
    return inputs


def test_dump_run_json_round_trips_a_non_finite_metric() -> None:
    """`increment_eval_metric("tokens", float("inf"))` is an ordinary call in a
    task, and nothing rejects it. A run can therefore carry a metric that JSON
    cannot encode. It is stored as the string "Infinity", and the run must still
    load, or the recorded history is gone.
    """
    nodeid = "tests/test_metrics.py::test_metrics"
    dataset = Dataset[str, str, Any](name="d", cases=[Case(name="c", inputs="x")])
    report = asyncio.run(dataset.evaluate(_task_reporting_an_infinite_metric))
    assert report.cases[0].metrics == {"tokens": float("inf")}, (
        "the scenario needs pydantic-evals to let a non-finite metric through"
    )

    recorder = EvalRecorder(keep_raw_results=True)
    recorder.add_round(nodeid, translate_report(report), raw_result=report)
    recorder.set_test_outcome(nodeid, "passed")
    data = dump_run_json(recorder.to_run_record())

    stored = json.loads(data)["tests"][nodeid]["raw_results"][0]
    assert stored["cases"][0]["metrics"] == {"tokens": "Infinity"}
    restored = parse_run_json(data)
    restored_test = restored.tests[nodeid]
    assert restored_test.raw_results == [stored]


def _make_run_with_non_finite_user_values() -> RunRecord:
    return make_eval_run(
        tests={
            "test_x.py::test_a": RecordedTest(
                cases={
                    "c": CaseRecord(
                        inputs={"ratio": float("nan")},
                        metadata={"limit": float("inf")},
                        attempts=[
                            AttemptRecord(
                                outcome="passed",
                                task_duration=0.0,
                                output={"margin": float("-inf")},
                            )
                        ],
                    )
                }
            )
        },
    )


def test_dump_run_json_stores_non_finite_user_values_as_strings() -> None:
    """JSON has no nan or inf, and pydantic's default stores them as null,
    which loads back indistinguishable from a real null. The marker strings keep
    the value readable, like the repr fallback for an object JSON cannot
    encode."""
    data = dump_run_json(_make_run_with_non_finite_user_values())
    case = json.loads(data)["tests"]["test_x.py::test_a"]["cases"]["c"]
    assert case["inputs"] == {"ratio": "NaN"}
    assert case["metadata"] == {"limit": "Infinity"}
    assert _read_stored_attempt(case)["output"] == {"margin": "-Infinity"}


def test_non_finite_user_values_never_load_back_as_none() -> None:
    """A re-dump compared against the original dump cannot catch a value that
    both dumps lose the same way. The guard is on the loaded values instead.
    Where the user had a number, the loaded run holds the marker string, never
    None. A re-dump of the loaded run is also stable, so a stored run does not
    drift when copied through a load and a save."""
    run = _make_run_with_non_finite_user_values()
    data = dump_run_json(run)
    loaded = parse_run_json(data)
    case = loaded.tests["test_x.py::test_a"].cases["c"]
    assert case.inputs == {"ratio": "NaN"}
    assert case.metadata == {"limit": "Infinity"}
    assert case.attempts[0].output == {"margin": "-Infinity"}
    assert dump_run_json(loaded) == data, "the round trip must be byte-stable"


def test_run_record_loads_a_stored_run_whose_raw_report_the_model_would_reject() -> (
    None
):
    """A stored raw report can hold values and fields the installed
    pydantic-evals rejects, because a different version wrote it. Stored runs are
    immutable history, so loading must not depend on that version.
    """
    stored_report = {
        "name": "t",
        "cases": [
            {
                "name": "c",
                "inputs": "x",
                "output": "X",
                "metrics": {"tokens": None},
                "task_duration": 0.1,
                "total_duration": 0.2,
                "a_field_a_later_pydantic_evals_dropped": 1,
            }
        ],
        "failures": [],
    }
    stored_run = {
        "run_schema_version": RUN_SCHEMA_VERSION,
        "id": str(ULID()),
        "created_at": datetime.now(UTC).isoformat(),
        "recorded_by": RECORDED_BY.model_dump(),
        "tests": {
            "test_x.py::test_a": {
                "cases": {},
                "raw_results": [stored_report],
                "outcome": "passed",
            }
        },
    }
    run = parse_run_json(json.dumps(stored_run).encode())
    test = run.tests["test_x.py::test_a"]
    assert test.raw_results == [stored_report]


def test_created_at_keeps_the_iso_string_wire_format() -> None:
    """A new save must still store `created_at` as an ISO 8601 string naming
    the same instant, so stored runs and their readers keep working."""
    run = make_eval_run(created_at=datetime(2026, 1, 1, 12, 30, 45, 123456, tzinfo=UTC))
    data = dump_run_json(run)
    stored = json.loads(data)["created_at"]
    assert isinstance(stored, str)
    assert datetime.fromisoformat(stored) == run.created_at
    assert parse_run_json(data) == run


def test_run_record_keeps_stored_fields_it_does_not_declare() -> None:
    """A stored run can carry fields the current model does not declare, for
    example ones a newer evaltrack added. A load must accept them, and a
    load-then-dump copy must keep them. A copy that strips them destroys
    recorded data on every path that copies a run."""
    legacy = {
        "run_schema_version": RUN_SCHEMA_VERSION,
        "id": str(ULID()),
        "created_at": datetime.now(UTC).isoformat(),
        "name": "ci-build-99",
        "recorded_by": {"evaltrack": "0.0.0", "pydantic_evals": "0.0.0"},
        "tests": {},
    }
    run = parse_run_json(json.dumps(legacy).encode())
    assert run.id == legacy["id"]
    assert json.loads(dump_run_json(run))["name"] == "ci-build-99"


def test_unknown_fields_survive_a_validate_then_dump_round_trip() -> None:
    """A copy that validates and re-dumps a stored run loses nothing a newer
    evaltrack recorded, at every level of the tree."""
    stored = json.loads(dump_run_json(_make_run_with()))
    stored["new_run_field"] = {"a": 1}
    stored["recorded_by"]["new_tool_field"] = "9.9"
    test = stored["tests"]["test_x.py::test_a"]
    test["new_test_field"] = "t"
    test["marker"]["new_meta_field"] = 2
    case = test["cases"]["c"]
    case["new_case_field"] = [1, 2]
    attempt = _read_stored_attempt(case)
    attempt["new_attempt_field"] = True
    attempt["results"]["acc"]["new_score_field"] = "why"
    attempt["results"]["acc"]["evaluator"]["new_evaluator_field"] = 3
    attempt["assertions"] = {
        "ok": {
            "value": True,
            "reason": None,
            "source": {"name": "ok", "arguments": None},
            "new_assertion_field": 1,
        }
    }

    run = parse_run_json(json.dumps(stored).encode())
    assert json.loads(dump_run_json(run)) == stored


def test_parse_run_json_refuses_another_stored_format() -> None:
    """The stamp is bumped for changes that keep the shape parseable while
    changing what a field means, so a run that parses cleanly can still be
    unreadable. The error says to upgrade, and names both versions."""
    stored = json.loads(dump_run_json(_make_run_with()))
    stored["run_schema_version"] = RUN_SCHEMA_VERSION + 1

    with pytest.raises(UnsupportedSchemaError) as excinfo:
        parse_run_json(json.dumps(stored).encode())
    message = str(excinfo.value)
    assert stored["id"] in message, "the error must name the unreadable run"
    assert f"run schema {RUN_SCHEMA_VERSION + 1}" in message, (
        "the error must name the version the run carries"
    )


def test_parse_run_json_refuses_another_stored_format_before_reading_the_shape() -> (
    None
):
    """A schema bump can change a field's type, so a run from another schema
    need not parse at all. Refused by its stamp, it is skew to upgrade past,
    not corruption to delete."""
    run_id = str(ULID())
    stored = {
        "run_schema_version": RUN_SCHEMA_VERSION + 1,
        "id": run_id,
        "tests": "a shape this evaltrack does not read",
    }

    with pytest.raises(UnsupportedSchemaError) as excinfo:
        parse_run_json(json.dumps(stored).encode())
    message = str(excinfo.value)
    assert run_id in message, "the error must name the unreadable run"
    assert f"run schema {RUN_SCHEMA_VERSION + 1}" in message


@pytest.mark.parametrize(
    "data",
    [
        pytest.param(b"{not json at all", id="not-json"),
        pytest.param(b"[]", id="not-an-object"),
        pytest.param(b'{"id": "01J9Z3QW2KJ5H8VN4TQY7B6MDC"}', id="no-stamp"),
        pytest.param(b'{"run_schema_version": true}', id="bool-stamp"),
        pytest.param(b'{"run_schema_version": "2"}', id="string-stamp"),
        pytest.param(
            f'{{"run_schema_version": {RUN_SCHEMA_VERSION}, "id": 1}}'.encode(),
            id="current-stamp-wrong-shape",
        ),
    ],
)
def test_parse_run_json_reports_bytes_without_a_readable_stamp_as_corrupt(
    data: bytes,
) -> None:
    """Only a run stamped with another version is skew. Anything else that
    fails to read is damage, whatever else the bytes hold, and a `ValueError`
    is what every caller already catches for that."""
    with pytest.raises(ValidationError):
        parse_run_json(data)


def test_ensure_run_id_accepts_a_ulid() -> None:
    run_id = str(ULID())
    assert ensure_run_id(run_id) == run_id


def test_ensure_run_id_refuses_a_lowercase_ulid() -> None:
    """Ids need no case handling downstream. A ULID is uppercase, so a
    lowercase spelling is refused here rather than read as a different key
    later."""
    lowered = str(ULID()).lower()
    with pytest.raises(InvalidIdentifierError) as excinfo:
        ensure_run_id(lowered)
    message = str(excinfo.value)
    assert lowered in message
    assert "ULID" in message, "the message states the rule, not just the value"


@pytest.mark.parametrize("run_id", ["", "not-a-ulid"])
def test_ensure_run_id_refuses_anything_that_is_not_a_ulid(run_id: str) -> None:
    with pytest.raises(InvalidIdentifierError):
        ensure_run_id(run_id)


def test_run_record_refuses_an_id_that_is_not_a_ulid() -> None:
    """The type is what makes `every stored run id is a ULID` true. A stored
    run with a bad id must read as corrupt data, so this is a pydantic error
    rather than the naming error the read boundary raises."""
    with pytest.raises(ValidationError):
        RunRecord(
            run_schema_version=RUN_SCHEMA_VERSION,
            id="01a",
            created_at=datetime.now(UTC),
            recorded_by=RECORDED_BY,
            tests={},
        )


def _make_score(value: float) -> EvaluatorResult:
    return EvaluatorResult(value=value, evaluator=EvaluatorInfo(name="acc"))


def _make_run_with(
    *,
    score: float = 0.9,
    task_duration: float = 0.5,
    score_bars: dict[str, float] | None = None,
) -> RunRecord:
    attempt = AttemptRecord(
        outcome="passed",
        task_duration=task_duration,
        results={"acc": _make_score(score)},
    )
    return make_eval_run(
        tests={
            "test_x.py::test_a": RecordedTest(
                outcome="passed",
                cases={"c": CaseRecord(inputs="x", attempts=[attempt])},
                marker=MarkerSettings(score_bars=score_bars or {"acc": 0.5}),
            )
        },
    )


def test_non_finite_floats_are_rejected() -> None:
    """A non-finite float has no JSON number form, so a stored one fails to
    load as a float. These are the three fields that carry one, so each must
    refuse it at construction."""
    value = float("inf")
    with pytest.raises(ValidationError, match="finite"):
        _make_run_with(score=value)
    with pytest.raises(ValidationError, match="finite"):
        _make_run_with(task_duration=value)
    with pytest.raises(ValidationError, match="finite"):
        _make_run_with(score_bars={"acc": value})
