"""Every public evaltrack exception. One `except EvaltrackError` catches them all."""


class EvaltrackError(Exception):
    """Base class of every public evaltrack exception."""


class EvalExecutionError(EvaltrackError):
    """A task or evaluator raised."""


class EvalDefinitionError(EvaltrackError):
    """The eval as defined cannot produce a verdict. Some of this shows only
    after the eval ran, but none of it depends on what the model answered, so
    running the eval again cannot change it."""


class InvalidIdentifierError(EvaltrackError):
    """A ref name or run id that breaks the naming rules."""


class CorruptRecordError(EvaltrackError):
    """Stored bytes that cannot be read as the record they are keyed as, a run or
    a reflog. Raised only when a caller asked for that record."""


class UnsupportedSchemaError(EvaltrackError):
    """A run stamped with a stored format this evaltrack does not read. The fix
    is to use the evaltrack that wrote it, not to delete the run."""


class RefNotFoundError(EvaltrackError):
    """No ref exists under `name`."""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"ref does not exist: {name}")


class RunReferencedError(EvaltrackError):
    """A run deletion refused because `refs` still reach the run."""

    def __init__(self, run_id: str, refs: list[str]) -> None:
        self.run_id = run_id
        self.refs = refs
        super().__init__(f"run {run_id} is referenced by: {', '.join(refs)}")


class BaselineRefProtectedError(EvaltrackError):
    """A delete targets the reserved `baseline` ref."""


class RepositoryUnavailableError(EvaltrackError):
    """The storage cannot serve the request. The cause is the environment, not a
    bug and not a missing run or ref. No storage SDK exception type escapes."""


class TranslatorNotFoundError(EvaltrackError):
    """No registered translator handles a result."""
