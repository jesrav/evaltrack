# The evaltrack marker

`@pytest.mark.evaltrack` marks a test as an eval, and `evaltrack.run()` hands evaltrack the eval to
run. A marked test records one eval, and that eval is the gate.

## Using the marker

The marker accepts five optional keyword arguments: `score_bars`, `repeats`, `flake_reruns`,
`reliability_target` and `eval_version`. [Score bars](#score-bars) are below, and
[Flakiness & reliability](./flakiness.md) covers the other four. The [configuration reference] lists
every default.

Call `evaltrack.run()` from the test function itself, or `run_async()` when the runner's entry point
is a coroutine.

## What counts as passing

Most runners collect an eval's results as data without asserting them. Under the marker, evaltrack
turns them into a gate that runs after the last round:

- **Every result must pass.** An assertion is its own verdict. A score must reach its bar, the
  runner's own or the one you declare, which replaces it.
- Every case must pass. How many of its attempts that takes is the marker's call: `repeats` demands
  all of them, `flake_reruns` accepts one.
- A case whose task or evaluator raised never reaches the gate. See [Exceptions](#exceptions).

### Score bars

`score_bars` is a `dict[str, float]` mapping a score name to the bar each case must clear, and a
case clears it when `value >= bar`. It replaces a threshold the runner set. The key must match the
name the runner reports the score under. In pydantic-evals that is an `LLMJudge`'s
`evaluation_name`, or an evaluator's class name unless it overrides `get_default_evaluation_name()`.
In DeepEval it is the name the metric reports, which for `GEval` carries a `[GEval]` suffix.
[`examples/test_02_scores.py`](../examples/test_02_scores.py) is a runnable one.

**A bar applies only to the cases that produced the score.** That bar does not gate the other cases.
Their own results must still pass.

## Exceptions

Anything that raises during the eval reaches you as an `EvalExecutionError`, even when the runner
caught it and stored it as data. Runners normally do store it, so that the rest of the eval can
finish, and silently swallowing an exception is almost never the intent in a test. There is no
opt-out: to inspect errors as data, call your runner directly and drop the marker on that test.

**`flake_reruns` does not retry an errored case.** It handles verdict flakiness, not exceptions.
Retry a transient infrastructure failure inside the eval, so that only a persistent error surfaces
as `EvalExecutionError`.

## Expected failures (xfail)

To record a failing eval without failing CI (an acknowledged regression, or a model that temporarily
underperforms), mark the test with pytest's own [`xfail`] marker. The eval still runs, the gate
still evaluates every case, and the results are recorded. Only the pytest outcome changes.

**Under `xfail(strict=True)` an unexpected pass is recorded as `failed`**, since pytest treats a
strict xpass as a genuine failure.

## Marking a whole file

`pytestmark` applies the marker to every test in a file, and a test can still add its own. Each
kwarg takes its value from the closest marker that sets it, and `score_bars` is **replaced whole**
rather than merged key by key. That is what lets a test drop a bar its file declared, which it has
to do when its dataset produces other scores.

## Without the marker

Your runner behaves exactly as it does on its own, the test gates whatever it asserts itself, and
nothing is recorded.

---

**Related:** [Flakiness & reliability](./flakiness.md) · [Configuration](./configuration.md) ·
[CI/CD](./ci-cd.md)

[configuration reference]: ./configuration.md#marker-keyword-arguments
[`xfail`]:
  https://docs.pytest.org/en/stable/how-to/skipping.html#xfail-mark-test-functions-as-expected-to-fail
