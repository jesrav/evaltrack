# Eval runners

evaltrack has no eval runner of its own. It records, gates and tracks the results of whichever one
you already use.

A **translator** turns a runner's results into evaltrack's own model, and everything downstream of
the eval reads that model. Translators for pydantic-evals and DeepEval ship with evaltrack. To
connect another runner, write a small [translator](./translators.md).

The only thing that differs is how you hand evaltrack the eval. pydantic-evals' `evaluate` is a
coroutine, so use `run_async`. DeepEval's is not, so use `run`. After that, the gate, the recorded
run, the history and the dashboard work the same for both.

## Installing a runner

Install the runner you already write your evals in, and evaltrack reads what it produces. evaltrack
depends on no runner and imports one only when that runner's results show up.

An extra exists for each shipped translator, so the install is one line instead of two:

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

You hand `run` the eval, not its result, so evaltrack can call it again for another round. `run`
takes no configuration of its own. Configuration lives on
[the marker](./marker.md#using-the-marker), which decides what is recorded, gated and rerun.
Everything you pass after the eval is passed to it on every round.

### pydantic-evals

```python
await evaltrack.run_async(dataset.evaluate, task)
```

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

Three runnable examples: [`test_08_deepeval.py`](../examples/test_08_deepeval.py) records and gates
a run, [`test_09_deepeval_repeats.py`](../examples/test_09_deepeval_repeats.py) adds the marker's
`repeats=`, and [`test_10_deepeval_traced.py`](../examples/test_10_deepeval_traced.py) scores the
parts of a traced app with `@observe` and `dataset.evals_iterator()`.

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
