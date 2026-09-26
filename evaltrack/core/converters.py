"""How a value of a registered type is stored.

A task can return an object that holds much more than a reader of the run
needs, for example the result of an agent framework. A converter turns such an
object into the value to store. Types are matched by dotted path, compared as
text, so a registration imports nothing.

Conversion applies to a whole stored value, such as an attempt's output. It
does not look inside a value for a registered type.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from evaltrack.core.type_paths import qualified_names

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Entry:
    native_types: tuple[str, ...]
    convert: Callable[[Any], Any]


_registry: dict[str, _Entry] = {}


def register(
    name: str,
    *,
    native_types: tuple[str, ...],
    convert: Callable[[Any], Any],
) -> None:
    """Record every value of the given types as `convert` returns it.

    A later `register` under the same name replaces the earlier one.

    Args:
        name: The registry key.
        native_types: Dotted paths of the types to convert, compared as text.
            A subclass of a registered type is converted too.
        convert: Takes the value and returns what to store. It runs after the
            evaluators, so they still see the value as the task returned it.
            When it raises, the value is stored as it is, and a warning is
            logged.

    Raises:
        ValueError: when `native_types` is empty, or when another name already
            claims one of them.
    """
    if not native_types:
        raise ValueError(
            f"the {name!r} converter names no native_types, so no value can "
            "match it. Give it a tuple of dotted type paths, for example "
            '("pydantic_ai.run.AgentRunResult",).'
        )
    # Two converters for one type make the stored value depend on import order.
    for other, entry in _registry.items():
        if other == name:
            continue
        clash = sorted(set(native_types) & set(entry.native_types))
        if clash:
            raise ValueError(
                f"the {name!r} converter claims {clash[0]}, which the "
                f"{other!r} converter already claims. To replace {other!r}, "
                "register under that name."
            )
    _registry[name] = _Entry(native_types=native_types, convert=convert)


def convert_registered(value: Any) -> Any:
    """`value` as its registered converter returns it, or `value` itself when no
    converter claims its type, or when the converter raises."""
    if not _registry:
        return value
    for qualified in qualified_names(value):
        for name, entry in _registry.items():
            if qualified in entry.native_types:
                return _convert(value, name=name, convert=entry.convert)
    return value


def _convert(value: Any, *, name: str, convert: Callable[[Any], Any]) -> Any:
    # Logged, not warned, because a warning fails a suite that runs under
    # `filterwarnings = error`. A converter must never fail an eval.
    try:
        return convert(value)
    except Exception:
        _logger.warning(
            "the %r converter raised, so the value is stored as it is",
            name,
            exc_info=True,
        )
        return value
