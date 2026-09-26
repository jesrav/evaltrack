"""Recognise a value's type by its dotted path, compared as text. A check done
this way imports no package."""

from collections.abc import Iterator
from typing import Any


def qualified_names(value: Any) -> Iterator[str]:
    """The dotted path of the value's own type first, then of the types it
    inherits from, so that a subclass of a recognised type is also recognised."""
    for cls in type(value).__mro__:
        yield f"{cls.__module__}.{cls.__qualname__}"
