# Stored outputs

A run stores the output of every attempt, together with the inputs, the expected output and the
metadata of each case. This page describes what evaltrack stores of a value, and how to make a large
value smaller.

## Private fields

evaltrack stores a dataclass without its private fields, the fields whose name starts with `_`. A
library often keeps its internal state in private fields, and this state can be large.

For example, a pydantic-ai `AgentRunResult` keeps the message history and the JSON schema of every
tool in private fields. evaltrack stores it as its final output only:

```json
{ "output": "The sum is 5." }
```

The evaluators still see the full object, because they run before evaltrack stores the output.

## Converters

A converter changes how evaltrack stores the values of one type. Register it in a `conftest.py`:

```python
import evaltrack.converters

evaltrack.converters.register(
    "my-framework",
    native_types=("my_framework.results.RunResult",),
    convert=lambda result: {"answer": result.answer},
)
```

`native_types` are dotted paths, compared as text, so the registration imports nothing. A subclass
of a registered type is converted too. The converter applies to a whole stored value, such as an
attempt's output. It does not look inside a value for a registered type.

If a converter raises, evaltrack stores the value as it is and logs a warning. A converter never
fails an eval.

### A converter for pydantic-ai tool calls

This converter stores an agent run as its final output and the tool calls it made, in order:

```python
from typing import Any

import evaltrack.converters
from pydantic_ai.messages import ModelResponse, ToolCallPart


def record_agent_run(result: Any) -> dict[str, Any]:
    tool_calls = [
        {"tool": part.tool_name, "args": part.args_as_dict()}
        for message in result.all_messages()
        if isinstance(message, ModelResponse)
        for part in message.parts
        if isinstance(part, ToolCallPart)
    ]
    return {"output": result.output, "tool_calls": tool_calls}


evaltrack.converters.register(
    "pydantic-ai",
    native_types=("pydantic_ai.run.AgentRunResult",),
    convert=record_agent_run,
)
```

This is often not necessary. An evaluator that checks a tool call already records its verdict and
its reason for each attempt.

## Large outputs

If each output is hundreds of kilobytes, a run of a few hundred attempts is slow to save, push and
open. The dashboard gets a large value only when a pane needs it, so it can open such a run. Saving
and pushing stay slow.

At the end of the pytest session, evaltrack reports each attempt whose stored output is larger than
256 KB. The report names the test and the case. To make the outputs smaller, do one of these:

- Return only what the evaluators need from the task.
- Register a [converter](#converters) for the type of the output.

If your eval runner records traces, use them for the full data. A translator can record the trace
IDs in the `details` of an attempt. The pydantic-evals translator does this when tracing is
configured, for example with Logfire.

---

**Related:** [Translators](./translators.md) · [Eval runners](./eval-runners.md)
