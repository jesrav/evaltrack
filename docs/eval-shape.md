# The shape of an eval

This is what evaltrack records about a pytest run that has tests marked with
`@pytest.mark.evaltrack`:

```text
run                  one pytest session
└─ test              one marked test, one eval
   └─ case           one input, usually with an expected output
      └─ attempt     one try at that case
         └─ result   one answer from an evaluator
```

An **eval** is a batch of **cases** evaluated together (in one test).

A **round** is one pass over every case, and it normally gives each case one attempt. A runner that
repeats internally (pydantic-evals' `evaluate(repeat=N)`) produces N attempts per case in one round.
The marker adds rounds with `flake_reruns` and `repeats`, so one case can have several attempts that
way too.

When the eval runs, your task produces an output for each case. The case's **evaluators** then judge
that output. An evaluator is one check on an attempt, and it can answer with more than one result.

A result is an **assertion** or a **score**. An assertion is true or false. A score is a number, and
it needs a bar from the runner or from the marker's `score_bars`. evaltrack gates on every result,
so an evaluator that answers with anything else is refused.

An eval runner must [fit this shape](./translators.md#the-model) to work with evaltrack. For
example, pydantic-evals has a `Case` and DeepEval an `LLMTestCase`. Both become a case in
evaltrack's model. [Eval runners](./eval-runners.md#what-each-runner-calls-these) lists the word
each runner uses for each of these.

---

**Related:** [The evaltrack marker](./marker.md) · [Eval runners](./eval-runners.md) ·
[Translators](./translators.md)
