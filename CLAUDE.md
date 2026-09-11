# CLAUDE.md

You should ALWAYS run the linting and tests when you are done with the changes you are working on.
When adding new functionality or fixing a bug you should add corresponding tests. Prefer to use
dependency injection so we can use fakes for the tests instead of mocking. Avoid testing internal
behavior.

## Function signatures

Make parameters keyword-only past the first, `(subject, *, option)`, unless positional reads better,
as when two peers are combined. The lint only enforces a cap of two positional.

## Comments and docstrings

Say what the code can't: constraints, invariants, non-obvious decisions.

- Why, not what. Delete any comment that restates the code.
- Keep knowledge local: don't describe other modules' internals or name callers.
- State a rationale once, briefly.
- Public API docstrings document the contract. Internal helpers get one line or nothing.

## Tests

Give an assert a message when the test name and the compared values don't say what failure means, as
for an exit code or a helper shared by many tests. Never a message that restates the comparison.

## Testing and linting

`just test` runs the tests and `just lint` runs the linters. `CONTRIBUTING.md` has the rest: the
`integration`-marked tests that need live Azure storage.
