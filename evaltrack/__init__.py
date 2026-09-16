"""evaltrack records, gates and tracks LLM eval runs from pytest."""

import importlib.metadata

from evaltrack.core.errors import (
    CorruptRecordError,
    EvalDefinitionError,
    EvalExecutionError,
    EvaltrackError,
    InvalidIdentifierError,
    RepositoryUnavailableError,
    TranslatorNotFoundError,
    UnsupportedSchemaError,
)
from evaltrack.core.eval_round import (
    AttemptErrorRecord,
    EvalRound,
    RoundAttempt,
    RoundErrorRecord,
)
from evaltrack.core.refs import ReflogEntry
from evaltrack.core.results import (
    EvaluatorInfo,
    EvaluatorResult,
    RunnerInfo,
)
from evaltrack.core.run_record import (
    AttemptRecord,
    CaseRecord,
    MarkerSettings,
    RecordedTest,
    RunRecord,
)
from evaltrack.marked_test import repeats, run, run_async
from evaltrack.repositories import RunRepository, RunSummary, open_repository

__version__ = importlib.metadata.version("evaltrack")
"""The installed evaltrack version, as PyPI knows it."""

# Hand-maintained. A name belongs here when a user writes it or catches it.
__all__ = [
    "AttemptErrorRecord",
    "AttemptRecord",
    "CaseRecord",
    "CorruptRecordError",
    "EvalDefinitionError",
    "EvalExecutionError",
    "EvalRound",
    "EvaltrackError",
    "EvaluatorInfo",
    "EvaluatorResult",
    "InvalidIdentifierError",
    "MarkerSettings",
    "RecordedTest",
    "ReflogEntry",
    "RepositoryUnavailableError",
    "RoundAttempt",
    "RoundErrorRecord",
    "RunRecord",
    "RunRepository",
    "RunSummary",
    "RunnerInfo",
    "TranslatorNotFoundError",
    "UnsupportedSchemaError",
    "__version__",
    "open_repository",
    "repeats",
    "run",
    "run_async",
]
