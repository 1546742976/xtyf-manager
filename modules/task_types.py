"""Shared JSON and authenticated-actor types for task modules."""
from __future__ import annotations

from typing import TypeAlias, TypedDict

JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]


class Actor(TypedDict):
    id: int
    name: str
    role: str
