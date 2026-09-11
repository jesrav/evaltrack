"""How an eval runner's own result becomes an `EvalRound`.

Dispatch reads the result's type as a dotted path compared as text, so
registering imports no runner.

`register` and `Translator` are what a translator author uses. `find_translator`
is evaltrack's own, called on each round's result.
"""

import importlib
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from functools import cache
from typing import Any

from evaltrack.core.errors import TranslatorNotFoundError
from evaltrack.core.eval_round import EvalRound as EvalRound
from evaltrack.translators.protocol import Translator as Translator


@dataclass(frozen=True)
class _Entry:
    native_types: tuple[str, ...]
    load: Callable[[], Translator]


def _builtin(module: str, *, native_types: tuple[str, ...]) -> _Entry:
    """A shipped translator, loaded only when a result needs it, so an uninstalled
    runner costs nothing."""
    return _Entry(
        native_types=native_types,
        load=cache(lambda: importlib.import_module(module).TRANSLATOR),
    )


_registry: dict[str, _Entry] = {
    "pydantic-evals": _builtin(
        "evaltrack.translators.pydantic_evals",
        native_types=("pydantic_evals.reporting.EvaluationReport",),
    ),
    "deepeval": _builtin(
        "evaltrack.translators.deepeval",
        native_types=("deepeval.evaluate.types.EvaluationResult",),
    ),
}


def register(
    name: str,
    *,
    native_types: tuple[str, ...],
    load: Callable[[], Translator],
) -> None:
    """Register a translator. A later `register` under the same name replaces the
    earlier one. That is also how to replace a shipped translator.

    Args:
        name: The registry key, and what refusals here call the translator. What
            a round reports in `EvalRound.runner` comes from the translator
            itself, not from this.
        native_types: Dotted paths of the result types this translator handles,
            compared as text. A claim on a subclass of a claimed type wins for
            results of that subclass.
        load: Builds the translator. Called at most once per process, so an
            expensive translator is built once however many rounds look it up.

    Raises:
        ValueError: when `native_types` is empty, or when another name already
            claims one of them.
    """
    if not native_types:
        raise ValueError(
            f"the {name!r} translator names no native_types, so no result can "
            "ever dispatch to it. Give it a native_types tuple of the dotted "
            "paths its runner returns, for example "
            '("pydantic_evals.reporting.EvaluationReport",).'
        )
    # A re-register under the same name is a replacement, so it can reclaim its
    # own types. A different name claiming them makes dispatch depend on import
    # order.
    for other, entry in _registry.items():
        if other == name:
            continue
        clash = sorted(set(native_types) & set(entry.native_types))
        if clash:
            raise ValueError(
                f"the {name!r} translator claims {clash[0]}, which the "
                f"{other!r} translator already claims. Two translators for one "
                "result type would make dispatch depend on import order. To "
                f"replace {other!r}, register under that name instead."
            )
    _registry[name] = _Entry(native_types=native_types, load=cache(load))


def _qualified_names(value: Any) -> Iterator[str]:
    """The value's own type first, then the types it inherits from, so a subclass
    of a registered type still finds a translator."""
    for cls in type(value).__mro__:
        yield f"{cls.__module__}.{cls.__qualname__}"


def find_translator(result: Any) -> Translator:
    """The translator that handles `result`, by its own type first and its base classes
    after.

    Raises:
        TranslatorNotFoundError: when nothing registered handles the type.
    """
    for qualified in _qualified_names(result):
        for entry in _registry.values():
            if qualified in entry.native_types:
                return entry.load()
    known = ", ".join(sorted(_registry)) or "none"
    raise TranslatorNotFoundError(
        f"no evaltrack translator handles {type(result).__module__}."
        f"{type(result).__qualname__} (registered runners: {known}). Install a "
        "translator for this runner, register one with "
        "evaltrack.translators.register, or build an evaltrack.EvalRound "
        "yourself and record that."
    )
