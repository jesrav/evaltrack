# Translators

A **translator** turns one eval runner's results into evaltrack's model. Write one to connect a
runner evaltrack ships none for. pydantic-evals and DeepEval work out of the
[box](./eval-runners.md).

```python
from evaltrack.translators import EvalRound, Translator, register


class MyTranslator:
    def translate(self, result) -> EvalRound:
        return EvalRound(...)


translator: Translator = MyTranslator()

register("my-runner", native_types=("my_runner.results.EvalReport",), load=MyTranslator)
```

Register from a `conftest.py`. `Translator` is a protocol matched by shape, so nothing inherits from
it: annotate against it and a type checker reports a missing or mistyped method. `native_types` are
dotted paths compared as text, so registering imports no runner.
[`examples/test_07_other_eval_runner.py`](../examples/test_07_other_eval_runner.py) is a runnable
one, for a made-up runner.

## The model

A translator builds an `EvalRound` of `RoundAttempt`s, each holding its evaluators' answers as
`EvaluatorResult`s. A result is an assertion or a score. evaltrack gates on every result, so a
translator raises `EvalDefinitionError` for an evaluator that answers with anything else, rather
than recording something the gate cannot read. Every field is documented where it is defined, in
[`core/eval_round.py`][models] and [`core/results.py`][result model], and the two shipped
translators are working examples: [`pydantic_evals.py`][pydantic-evals translator] and
[`deepeval.py`][deepeval translator].

### Case ids

A case id keys a case in the stored run, in the dashboard, and in the pass-rate history that spans
runs. Set `case_id` from the one name or id the runner reported, never from the position of the row:
an inserted row would shift every unnamed case after it.

A translator must refuse a round in which two distinct cases reach the same id, by raising
`EvalDefinitionError`. If it records them, the two merge into one row with one shared pass-rate
history, and no later read can separate them. Repeated attempts at one case are the exception and do
share an id. Collisions are easy to produce, because a runner often names an unnamed case after its
position or after the running test.

---

**Related:** [Eval runners](./eval-runners.md) · [The evaltrack marker](./marker.md)

[models]: ../evaltrack/core/eval_round.py
[result model]: ../evaltrack/core/results.py
[pydantic-evals translator]: ../evaltrack/translators/pydantic_evals.py
[deepeval translator]: ../evaltrack/translators/deepeval.py
