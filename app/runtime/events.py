"""Versioned event reading without rewriting historical events."""

from collections.abc import Callable

from pydantic import BaseModel

from app.runtime.contracts import VersionedEvent

Upcaster = Callable[[BaseModel], BaseModel]


class EventReader:
    def __init__(self) -> None:
        self._readers: dict[tuple[str, int], type[BaseModel]] = {}
        self._upcasters: dict[tuple[str, int], Upcaster] = {}

    def register(self, event_type: str, schema_version: int, payload_type: type[BaseModel]) -> None:
        key = (event_type, schema_version)
        if key in self._readers:
            raise ValueError(f"event reader already registered: {event_type}@{schema_version}")
        self._readers[key] = payload_type

    def register_upcaster(self, event_type: str, from_version: int, upcaster: Upcaster) -> None:
        key = (event_type, from_version)
        if key in self._upcasters:
            raise ValueError(f"event upcaster already registered: {event_type}@{from_version}")
        self._upcasters[key] = upcaster

    def read(self, event: VersionedEvent, target_version: int | None = None) -> BaseModel:
        payload, version = event.payload, event.schema_version
        target = version if target_version is None else target_version
        while version < target:
            upcaster = self._upcasters.get((event.event_type, version))
            if upcaster is None:
                raise ValueError(f"no upcaster for {event.event_type}@{version}")
            payload, version = upcaster(payload), version + 1
        reader = self._readers.get((event.event_type, version))
        if reader is None:
            raise ValueError(f"unknown event schema: {event.event_type}@{version}")
        return reader.model_validate(payload)
