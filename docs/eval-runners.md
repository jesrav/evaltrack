# Eval runners

evaltrack has no eval runner of its own. It records, gates and tracks the results of whichever one
you already use.

A **translator** turns a runner's results into evaltrack's own model, and everything downstream of
the eval reads that model. Translators for pydantic-evals and DeepEval ship with evaltrack. To
connect another runner that fits [the shape of an eval](./eval-shape.md), write a small
[translator](./translators.md).

The only thing that differs is how you hand evaltrack the eval. pydantic-evals' `evaluate` is a
coroutine, so use `run_async`. DeepEval's is not, so use `run`. After that, the gate, the recorded
run, the history and the dashboard work the same for both.

## Installing a runner

evaltrack depends on no runner, so install the one you write your evals in. An extra exists for each
shipped translator, so that is one line:

```bash
uv add "evaltrack[deepeval]"     # the same as adding deepeval yourself
```

| extra                       | installs            |
| --------------------------- | ------------------- |
| `evaltrack[pydantic-evals]` | `pydantic-evals>=2` |
| `evaltrack[deepeval]`       | `deepeval>=4.2`     |

The floors are the versions the test suite runs against, not compatibility claims.

## Running an eval

```python
import evaltrack

@pytest.mark.evaltrack(score_bars={"helpfulness": 0.8}, flake_reruns=2)
def test_my_eval():
    evaltrack.run(my_runner.evaluate, dataset)
```

You hand `run` the eval, not its result, so evaltrack can call it again for another round.
Everything after the eval is passed to it on every round.

### pydantic-evals

pydantic-evals is the runner the [Getting started](./getting-started.md) walkthrough and most of the
examples use. A `Dataset` holds the cases and the evaluators, and `evaluate` runs a task over it.
Hand evaltrack the method and the task, and it calls them once per round:

```python
await evaltrack.run_async(dataset.evaluate, task)
```

An evaluator that returns a bool is an assertion, and one that returns a number is a score, gated by
the marker's `score_bars`. Three runnable examples:
[`test_01_function.py`](https://github.com/jesrav/evaltrack/blob/main/examples/test_01_function.py)
is the basics with a custom evaluator,
[`test_02_scores.py`](https://github.com/jesrav/evaltrack/blob/main/examples/test_02_scores.py)
gates numeric scores with `score_bars`, and
[`test_05_llm_judge.py`](https://github.com/jesrav/evaltrack/blob/main/examples/test_05_llm_judge.py)
uses `LLMJudge` as both a guardrail and a graded score.

On Python 3.12 and 3.13, `evaluate_sync()` emits a harmless pydantic-evals
`DeprecationWarning: There is no current event loop`, which `run_async()` does not hit. Under
`filterwarnings = error` it fails the test, so ignore that one and keep `error` for the rest:

```toml
[tool.pytest.ini_options]
filterwarnings = [
    "error",
    "ignore:There is no current event loop:DeprecationWarning",
]
```

A task can return any object, for example the result of an agent framework, so that the evaluators
can check more than the answer. [Stored outputs](./outputs.md) says what evaltrack stores of it.

### DeepEval

DeepEval's `evaluate()` scores a batch of test cases and returns the scores. It fails nothing, so
evaltrack gates them. DeepEval also ships `assert_test()`, which gates by raising inside the test.
evaltrack replaces that gate rather than adding to it, so use one or the other.

```python
from deepeval.evaluate import evaluate

evaltrack.run(lambda: evaluate(test_cases=cases, metrics=[AnswerRelevancyMetric()]))
```

A `MetricData` is one result. Its `threshold` is the bar, so a metric with a threshold gates the
case on its own, and a `score_bars` entry for that name replaces it.

Configure a metric through its constructor. Under the default `AsyncConfig(run_async=True)`,
`evaluate()` rebuilds every metric from the arguments its `__init__` accepts, and drops anything set
on the instance afterwards, `threshold` included.

`evaluate()` runs each case once and has no repeat of its own, so `repeats=N` on the marker is
evaltrack calling the eval N times. There is no second count to set.

`evaluate()` writes its last run to `./.deepeval`, beside whatever evaltrack records. Add
`.deepeval/` to your `.gitignore`.

Two runnable examples:
[`test_07_deepeval.py`](https://github.com/jesrav/evaltrack/blob/main/examples/test_07_deepeval.py)
records and gates a run, and
[`test_08_deepeval_repeats.py`](https://github.com/jesrav/evaltrack/blob/main/examples/test_08_deepeval_repeats.py)
adds the marker's `repeats=`.

## What each runner calls these

evaltrack uses one set of words for every runner, defined in
[the shape of an eval](./eval-shape.md). A translator maps each runner's own words onto them. The
dashboard and the pass-rate history then read the same, whichever runner produced the run.

| evaltrack | pydantic-evals                            | DeepEval                               |
| --------- | ----------------------------------------- | -------------------------------------- |
| case      | `Case`                                    | `LLMTestCase`                          |
| attempt   | one case, or one of `evaluate(repeat=N)`  | one case of one `evaluate()` call      |
| evaluator | `Evaluator`                               | a metric                               |
| result    | one entry of `assertions` or `scores`     | one `MetricData`                       |
| round     | one `evaluate()` call                     | one `evaluate()` call                  |
| run       | (none, it spans the whole pytest session) | a `test_run_id`, kept only as a detail |

---

**Related:** [Translators](./translators.md) · [The evaltrack marker](./marker.md) ·
[Flakiness & reliability](./flakiness.md)
