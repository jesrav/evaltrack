"""User pass-through values (`UserValue`, `UserStr`), which evaltrack never
reads into, so one may hold anything. What the JSON dump cannot serialize
degrades at validation to a stand-in (a `$binary` fingerprint, a `$cycle`
marker, a plain dict of a model's fields, or `repr()`), so a dump almost never
fails.

Two values still fail it, and neither is a realistic eval output. A value
nested deeper than Python's stack allows fails the walk, and fails the JSON
round trip anyway. A `frozenset` used as a dict key fails the dump, because
the degrade leaves an unknown type alone and JSON has no key for it.

A dataclass is stored without its private fields, the fields whose name starts
with `_`. pydantic leaves out the private attributes of a model in the same
way. Such fields usually hold the internal state of a library, which can be
large.

A `UserValue` of a type with a registered converter is stored as the converter
returns it."""

import dataclasses
import hashlib
from collections.abc import Iterable
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator

from evaltrack.core.converters import convert_registered


def degrade_undumpable(value: Any) -> Any:
    """Degrade what the JSON dump cannot serialize, keeping the structure around it.

    Returns `value` itself when nothing inside it degrades, and never mutates it.
    """
    return _degrade(value, on_path=frozenset(), in_key=False)


def _build_binary_fingerprint(value: bytes | bytearray) -> dict[str, Any]:
    return {
        "$binary": {"sha256": hashlib.sha256(value).hexdigest(), "size": len(value)}
    }


def _degrade(value: Any, *, on_path: frozenset[int], in_key: bool) -> Any:
    """`on_path` holds the ids of the ancestors only. So the back-edge of a cycle
    is cut, and a value that appears twice is kept. Under a dict key (`in_key`)
    a value degrades to `repr()`, since a JSON key must stay a string.
    """
    if isinstance(value, str):
        return _degrade_str(value)
    if isinstance(value, (bytes, bytearray)):
        return _degrade_bytes(value, in_key=in_key)
    if id(value) in on_path:
        return _degrade_cycle(in_key=in_key)
    if isinstance(value, dict):
        return _degrade_dict(value, on_path=on_path, in_key=in_key)
    if isinstance(value, (list, tuple)):
        return _degrade_sequence(value, on_path=on_path, in_key=in_key)
    if isinstance(value, BaseModel):
        return _degrade_model(value, on_path=on_path, in_key=in_key)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _degrade_dataclass(value, on_path=on_path, in_key=in_key)
    return value


def _degrade_str(value: str) -> str:
    """Degrade a string holding a lone surrogate, which has no UTF-8 form."""
    try:
        value.encode()
    except UnicodeEncodeError:
        return repr(value)
    return value


def _degrade_cycle(*, in_key: bool) -> Any:
    # Fresh per call, since the walk compares identity to detect a change.
    return "<cycle>" if in_key else {"$cycle": True}


def _degrade_bytes(value: bytes | bytearray, *, in_key: bool) -> Any:
    """Keep bytes that decode as text. Fingerprint the rest, or repr under a key."""
    try:
        value.decode()
    except UnicodeDecodeError:
        return repr(value) if in_key else _build_binary_fingerprint(value)
    return value


def _degrade_dict(
    value: dict[Any, Any], *, on_path: frozenset[int], in_key: bool
) -> dict[Any, Any]:
    on_path = on_path | {id(value)}
    changed = False
    out: dict[Any, Any] = {}
    for key, item in value.items():
        new_key = _degrade(key, on_path=on_path, in_key=True)
        new_item = _degrade(item, on_path=on_path, in_key=in_key)
        changed = changed or new_key is not key or new_item is not item
        out[new_key] = new_item
    return out if changed else value


def _degrade_sequence(
    value: list[Any] | tuple[Any, ...], *, on_path: frozenset[int], in_key: bool
) -> list[Any] | tuple[Any, ...]:
    on_path = on_path | {id(value)}
    items = [_degrade(item, on_path=on_path, in_key=in_key) for item in value]
    if all(new is old for new, old in zip(items, value, strict=True)):
        return value
    return items if isinstance(value, list) else tuple(items)


def _degrade_attrs(
    value: Any,
    *,
    attrs: Iterable[tuple[str, Any]],
    on_path: frozenset[int],
    in_key: bool,
    dropped: bool = False,
) -> Any:
    """Walk an object's named values. Returns the object itself when none degraded
    and `dropped` is false. Otherwise returns a plain dict of them, or `repr()`
    under a key, so its type cannot re-validate degraded data or run
    construction logic on it. `dropped` is true when `attrs` leaves out some of
    the object's values."""
    on_path = on_path | {id(value)}
    out: dict[str, Any] = {}
    changed = dropped
    for name, old in attrs:
        new = _degrade(old, on_path=on_path, in_key=in_key)
        changed = changed or new is not old
        out[name] = new
    if not changed:
        return value
    return repr(value) if in_key else out


def _degrade_model(value: BaseModel, *, on_path: frozenset[int], in_key: bool) -> Any:
    # An excluded field stays out of the flat form too. Otherwise degrading
    # exposes what the model was told to hide. `hasattr` skips a field that
    # `model_construct` left unset.
    fields = [
        (name, getattr(value, name))
        for name, info in type(value).model_fields.items()
        if not info.exclude and hasattr(value, name)
    ]
    # Not via `getattr`, which hands back a class attribute for an extra of that
    # name.
    extra = (value.__pydantic_extra__ or {}).items()
    return _degrade_attrs(
        value, attrs=[*fields, *extra], on_path=on_path, in_key=in_key
    )


def _degrade_dataclass(value: Any, *, on_path: frozenset[int], in_key: bool) -> Any:
    fields = dataclasses.fields(value)
    public = [f for f in fields if not f.name.startswith("_")]
    attrs = [(f.name, getattr(value, f.name)) for f in public if hasattr(value, f.name)]
    return _degrade_attrs(
        value,
        attrs=attrs,
        on_path=on_path,
        in_key=in_key,
        dropped=len(public) < len(fields),
    )


def _record_user_value(value: Any) -> Any:
    # Converted first, so that a converter gets the object and not its degraded
    # form.
    return degrade_undumpable(convert_registered(value))


# The user's data wholesale, stored as produced, except that a registered type
# is converted and undumpable values degrade at validation.
UserValue = Annotated[Any, BeforeValidator(_record_user_value)]

# Text evaltrack declares but does not author. Only the `repr()` stand-in can
# reach a string.
UserStr = Annotated[str, BeforeValidator(degrade_undumpable)]

# One round's result, exactly as the eval runner returned it. evaltrack never
# reads into it. Stored as a `UserValue`, so a loaded run hands back plain JSON
# data rather than the runner's own types, and cannot be translated again.
RawResult = Any
