"""The contract an eval runner's translator fills in."""

from typing import Any, Protocol

from evaltrack.core.eval_round import EvalRound


class Translator(Protocol):
    """Turns one eval runner's results into evaltrack's model."""

    def translate(self, result: Any) -> EvalRound:
        """Build an `EvalRound` from the runner's own result object."""
        ...
