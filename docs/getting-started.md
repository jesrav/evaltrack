# Getting started

The quickstart below uses pydantic-evals. If you are new to it, [start here]. If you write your
evals in [DeepEval], [Eval runners] goes through the same steps with a DeepEval run.

### 1. Install

```bash
# The quick start uses pydantic-evals as the eval runner and evaluates an agent built in pydantic-ai, but neither is required for evaltrack.
uv add "evaltrack[ui,pydantic-evals]" "pydantic-ai-slim[openai]"
# or
pip install "evaltrack[ui,pydantic-evals]" "pydantic-ai-slim[openai]"
```

!!! note

    Linux and macOS are supported, on Python 3.11 to 3.14.
    If your machine runs Windows, run evaltrack under WSL2.

### 2. Write a tracked eval

Add `@pytest.mark.evaltrack` to a test and hand the eval to `evaltrack.run()`. The test below is
sync. For an async test, use `await evaltrack.run_async(dataset.evaluate, task)` with your async
plugin.

```python
import pytest
from pydantic_ai import Agent
from pydantic_evals import Case, Dataset
from pydantic_evals.evaluators import LLMJudge

import evaltrack


@pytest.mark.evaltrack(score_bars={"helpfulness": 0.8}, flake_reruns=2)
def test_support_agent() -> None:
    agent = Agent(
        "openai:gpt-4o-mini",
        instructions=(
            "Support the Snapwombat photo app. Deleted photos are restorable from "
            "Settings > Backups for 30 days. Never promise or rule out a refund. Send "
            "billing questions to support@snapwombat.example."
        ),
    )

    async def support_task(prompt: str) -> str:
        return (await agent.run(prompt)).output

    dataset = Dataset(
        name="snapwombat-support",
        cases=[
            Case(name="refund-demand", inputs="The app deleted my photos. I want my money back!"),
            Case(name="restore-backup", inputs="How do I restore a backup?"),
        ],
        evaluators=[
            LLMJudge(rubric="The reply neither promises nor rules out a refund", include_input=True),  # (1)!
            LLMJudge(  # (2)!
                rubric=(
                    "Rate how helpful and clear the reply is, from 0.0 to 1.0. "
                    "Declining to promise a refund is not unhelpful."
                ),
                include_input=True,
                score={"evaluation_name": "helpfulness", "include_reason": True},
                assertion=False,
            ),
        ],
    )

    evaltrack.run(dataset.evaluate_sync, support_task)  # (3)!
```

1. An assertion: it must be true, or the case fails.
2. A score, gated at 0.8 by the marker's `score_bars`.
3. The marker gates here: a failed assertion, or a score under 0.8, fails the test. `flake_reruns=2`
   runs the eval again while a case is failing, up to twice.

The agent and the two LLM judges all call a real model, so this test needs an API key (here
`OPENAI_API_KEY`). The [examples] run without one — [`test_05_llm_judge.py`][llm judge example] is
the same pattern, an `LLMJudge` guardrail plus a `score_bars`-gated score, with the model and the
judge scripted.

!!! tip "One test is one eval"

    One test needs to be one eval. evaltrack records one eval per test, whichever runner produced it,
    and the test's name identifies that eval everywhere. If you want two evals, write two tests.

### 3. Run it

`evaltrack` is a registered pytest marker. You can select only your evals:

```bash
pytest -m "evaltrack"
```

Each pytest session produces a **run**, a snapshot of all recorded eval results, saved to
`.evaltrack/` by default (add `.evaltrack/` to your project's `.gitignore`).

CI can run the same command. See [CI/CD].

### 4. See the results

```bash
evaltrack ui
```

This serves the dashboard at `http://127.0.0.1:8765` and prints the link. Browse scores and
pass-rates per case, and diff any two runs. Open a case to see each score against its bar and its
history over the mainline. **Report** saves the run on screen as a single HTML file that opens
anywhere, to share with someone without the dashboard. `evaltrack report` writes the same file from
a CI job, for an artifact or a release note. See [CLI › report](./cli.md#evaltrack-report).

!!! note

    The dashboard's React frontend is beta, written largely by AI coding agents. The security notes
    are in the [security policy].

### 5. Track a shared remote (optional)

Declare your repositories in `pyproject.toml`. `local` is where the plugin saves runs. `remote` is a
shared repository that your CI writes to (Azure Blob Storage or Amazon S3). It stores the baseline
(the run recorded for what is currently deployed) and the PR history.

```toml
[tool.evaltrack]
local  = "./.evaltrack"
remote = "azure://account/evals"
```

`evaltrack ui` then mounts both and opens on your latest local run. Click **Compare to mainline** to
diff your run against the baseline run, the one recorded for what is deployed. See [Repositories and
storage][configuring repositories] for the full setup.

---

**Related:** [The evaltrack marker](./marker.md) · [Eval runners](./eval-runners.md) ·
[CI/CD](./ci-cd.md)

[start here]: https://ai.pydantic.dev/evals/
[DeepEval]: https://deepeval.com/
[Eval runners]: ./eval-runners.md
[examples]: ./examples.md
[llm judge example]: https://github.com/jesrav/evaltrack/blob/main/examples/test_05_llm_judge.py
[CI/CD]: ./ci-cd.md
[security policy]: https://github.com/jesrav/evaltrack/blob/main/SECURITY.md#dashboard-trust-model
[configuring repositories]: ./repositories.md#configuring-repositories
