# evaltrack examples

Small, runnable evals that double as documentation. Each file is a self-contained pytest module that
uses the `@pytest.mark.evaltrack` marker. The marker records the run (to `./.evaltrack` by default).

Examples 1 to 6 are `async def` tests marked with `@pytest.mark.asyncio`, so they need
[pytest-asyncio](https://pytest-asyncio.readthedocs.io/). The [command below](#run-them) installs it
for one run. Examples 7 to 10 are sync and need no async plugin.

Your own evals can use any async plugin.
[anyio](https://anyio.readthedocs.io/en/stable/testing.html) uses `@pytest.mark.anyio` instead.

> **On pydantic-ai.** The agent examples (4 to 6) use [pydantic-ai](https://ai.pydantic.dev/), but
> evaltrack does not require it. A pydantic-ai `FunctionModel` stands in for a real model. Examples
> 1 to 6 record [pydantic-evals](https://ai.pydantic.dev/evals/) runs, and pydantic-evals evaluates
> _any_ callable, a plain function (examples 1 to 3) or an agent or LLM call from any framework.
> Examples 7 to 10 use no pydantic-evals at all.

## No LLM calls, no API keys

Every example runs offline, so CI runs them all. Examples 1 to 3 and 7 evaluate plain Python.
Deterministic code does not normally need an eval, but these examples show the mechanics with
nothing to configure. Examples 4 to 6 evaluate pydantic-ai agents, and 8 to 10 DeepEval test cases,
whose model replies and judges are scripted, so they run without a key too. The
[next section](#evaluating-agents) says how to run them against a real model.

| File                                                               | Shows                                                                                                                                                                               |
| ------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [`test_01_function.py`](test_01_function.py)                       | The basics: a function under test, a custom assertion evaluator, and the marker's gate and recording.                                                                               |
| [`test_02_scores.py`](test_02_scores.py)                           | Numeric scores + per-case `score_bars`, dataset- vs per-case evaluators, and reading `Case.metadata`.                                                                               |
| [`test_03_reliability.py`](test_03_reliability.py)                 | Absorbing a flaky failure with `flake_reruns`, plus `reliability_target` and `eval_version` for cross-run tracking. The flakiness is faked, so a rerun always happens.              |
| [`test_04_agent_tool_use.py`](test_04_agent_tool_use.py)           | Evaluating tool use: the agent calls a tool only when the query needs it (`ToolCalled` / `NoToolCalled`, per-case).                                                                 |
| [`test_05_llm_judge.py`](test_05_llm_judge.py)                     | `LLMJudge` grading the output as a binary assertion (a guardrail) and a graded score (gated by `score_bars`), plus a cheap deterministic check. One case misses the bar on purpose. |
| [`test_06_fake_tool_in_inputs.py`](test_06_fake_tool_in_inputs.py) | Making a faked tool's data visible in the run by seeding it from `Case.inputs` instead of a fixture, so the dashboard shows what the agent could retrieve.                          |
| [`test_07_other_eval_runner.py`](test_07_other_eval_runner.py)     | Recording a made-up eval runner: a translator plus `evaltrack.run()`. Not an async test, so it needs no async plugin.                                                               |
| [`test_08_deepeval.py`](test_08_deepeval.py)                       | A [DeepEval](https://deepeval.com/) `evaluate()` recorded and gated: a `GEval` judge's threshold and verdict as the case's bar, a metric with no threshold gated by `score_bars`.   |
| [`test_09_deepeval_repeats.py`](test_09_deepeval_repeats.py)       | The marker's `repeats=N` over a runner with no repeat of its own: evaltrack calls `evaluate()` N times and demands every attempt passes. One case fails a round, so the test fails. |
| [`test_10_deepeval_traced.py`](test_10_deepeval_traced.py)         | Scoring the parts of a traced app: `@observe` metrics, driving `dataset.evals_iterator()`, and naming every scored span so two cases never share an id.                             |

## Evaluating agents

Examples 4 to 6 evaluate pydantic-ai agents, their tool use and their output quality. The agent, its
tools, the dataset and the evaluators are what you write for a real model. Only the model is a
stand-in, a pydantic-ai [`FunctionModel`](https://ai.pydantic.dev/testing/) with replies scripted in
the example file. In example 5, the judge is one too. This stand-in makes the examples deterministic
and runnable in CI. It is also what you lose. The model never surprises you.

To run one against a real model, pass a model string such as `"openai:gpt-4o-mini"` to `Agent(...)`.
In example 5, pass it to `LLMJudge(model=...)` too. Then remove the `ALLOW_MODEL_REQUESTS = False`
line. Set the provider's key:

```bash
uv sync                  # dev deps include pydantic-ai-slim[openai]
export OPENAI_API_KEY=sk-...
```

### Making a faked tool's setup visible (test_06)

[`test_06_fake_tool_in_inputs.py`](test_06_fake_tool_in_inputs.py) seeds a faked document store from
`Case.inputs` rather than from a fixture, so the run records what the agent could retrieve. Its
docstring says why. This is what that looks like in the dashboard:

![Dashboard: a case opened to show the faked document store in its input, and the answer the agent grounded in it](https://raw.githubusercontent.com/jesrav/evaltrack/main/examples/images/fake-tool-input.png)

## Run them

`--with` installs the async plugin for that one command. To run a single example, replace
`examples/` with its file path.

```bash
uv run --with pytest-asyncio pytest examples/
uv run evaltrack ui   # open the dashboard on the recorded runs
```

## In the dashboard

After you run some examples, `evaltrack ui` shows each run with its per-case verdicts, scores, and
the test docstring as context.

![Dashboard: an example run: the case table with pass/fail pills, per-case metadata, scores against their bars, and the docstring under the test header](https://raw.githubusercontent.com/jesrav/evaltrack/main/examples/images/run-detail.png)

Every example is deterministic. Examples 5, 7 and 9 each fail on purpose (5 misses a score bar, 7
misses a runner's bar, 9 answers one of its rounds wrong), and all three have an `xfail` mark, so
the suite stays green. To see a failure in the others, raise a `score_bars` bar or change a case's
expected value.

![Dashboard: a case failing on a tightened score bar, with the score in red against the bar it missed](https://raw.githubusercontent.com/jesrav/evaltrack/main/examples/images/failing-case.png)
