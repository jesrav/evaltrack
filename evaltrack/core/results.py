"""What an evaluator returned for one attempt, and which runner produced it."""

import math
from typing import Any, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    FiniteFloat,
    InstanceOf,
    StrictBool,
    field_validator,
    model_validator,
)

from evaltrack.core.user_values import UserStr, UserValue


class RunnerInfo(BaseModel):
    """Which eval runner produced a result, and at what version. `version` is None
    when the runner does not report one."""

    model_config = ConfigDict(frozen=True, extra="allow")

    name: str
    version: str | None = None


class EvaluatorInfo(BaseModel):
    """Which evaluator produced a result, and how it was configured.

    Attributes:
        name: The evaluator's own name, not always the result's key.
        arguments: What the evaluator was built with, as the runner reports it.
            A runner can report only the settings the caller passed, and not
            the defaults. None when the runner reports no configuration. What
            the evaluator answered goes on the result, never here.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True, extra="allow")

    name: str
    arguments: UserValue = None


ResultValue = StrictBool | InstanceOf[float]
"""An assertion or a score, because evaltrack gates on every result and can
gate on nothing else. `InstanceOf` rather than `StrictFloat`, which accepts a
`Decimal`."""


class EvaluatorResult(BaseModel):
    """One evaluator's result for one attempt.

    A score carries `verdict: None` until a bar reaches it, so the marker's
    `score_bars` can still decide.

    Attributes:
        value: What the evaluator returned.
        verdict: Whether the result passes. Derived, never set directly.
        bar: The number `value` must reach. Derived, the marker's bar over the
            runner's, and never set directly.
        runner_bar: The bar the runner set, whether or not the marker's
            replaced it. None when the runner set none.
        marker_bar: The bar the marker declared for this result in
            `score_bars`. None when it declared none.
        reason: Human-readable explanation, or None.
        evaluator: Which evaluator produced the value, and how it was configured.
        details: Runner-specific data about this result, stored as produced and
            never interpreted.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True, extra="allow")

    value: ResultValue
    verdict: bool | None = None
    bar: FiniteFloat | None = None
    runner_bar: FiniteFloat | None = None
    marker_bar: FiniteFloat | None = None
    reason: UserStr | None = None
    evaluator: EvaluatorInfo
    details: dict[str, UserValue] = {}

    @field_validator("value")
    @classmethod
    def _refuse_non_finite(cls, value: Any) -> Any:
        """A NaN compares False against any bar and sinks every average. An infinity
        clears every bar."""
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(
                "an evaluator result must be a finite number, got "
                f"{value!r}. A runner reporting it that way is saying the "
                "metric could not be computed, which belongs on the attempt "
                "as an error."
            )
        return value

    @model_validator(mode="before")
    @classmethod
    def _store_an_int_as_its_float(cls, data: Any) -> Any:
        """Here rather than in each translator, so every runner and a result
        built by hand store one representation. Runs before the field is
        checked, which is why `ResultValue` declares no int. A bool is an int
        and stays one.

        DeepEval's toxicity and bias metrics return the int 1 for an output
        with nothing to judge, so a runner does reach this.
        """
        if not isinstance(data, dict):
            return data
        value = data.get("value")
        if isinstance(value, int) and not isinstance(value, bool):
            return {**data, "value": float(value)}
        return data

    @model_validator(mode="before")
    @classmethod
    def _derive_the_gate(cls, data: Any) -> Any:
        """Answer which bar applies and what it decides.

        `bar` is the marker's, or the runner's when the marker declared none.
        `verdict` follows from the value. A bool is its own, and a number
        reaches one only through a bar.

        One validator rather than two, because the verdict reads the bar this
        resolves. `mode="before"` validators run bottom-up, so a second one
        below this cannot see it.

        Both fields are recomputed on every validation, so neither can be
        passed in, and both stay stored rather than computed. On load, a
        computed field is kept as an unknown extra and recomputed, so the dump
        names it twice.
        """
        if not isinstance(data, dict):
            return data
        marker_bar = data.get("marker_bar")
        bar = data.get("runner_bar") if marker_bar is None else marker_bar
        value = data.get("value")
        if isinstance(value, bool):
            verdict = value
        elif isinstance(value, int | float) and bar is not None:
            verdict = value >= bar
        else:
            verdict = None
        return {**data, "bar": bar, "verdict": verdict}

    @model_validator(mode="after")
    def _refuse_a_bar_on_an_assertion(self) -> Self:
        """An assertion already answers for itself, and a bar beside it is a
        second gate that no comparison can reach."""
        if self.bar is not None and self.is_assertion:
            raise ValueError(
                f"a bar of {self.bar} needs a number, but the value is the "
                f"assertion {self.value!r}"
            )
        return self

    @property
    def is_score(self) -> bool:
        """Whether a bar can grade `value`. An assertion answers for itself, and
        `True >= 0.5` holds, so a bar must never reach one."""
        return not isinstance(self.value, bool)

    @property
    def is_assertion(self) -> bool:
        """Whether `value` is its own verdict, needing no bar."""
        return isinstance(self.value, bool)
