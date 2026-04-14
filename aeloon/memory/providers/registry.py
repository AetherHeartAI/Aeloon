from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, cast

from aeloon.memory.providers.base import MemoryProvider
from aeloon.memory.types import MemoryRuntimeDeps


@dataclass(frozen=True, slots=True)
class MemoryProviderSpec:
    name: str
    provider_cls: type[MemoryProvider]
    description: str = ""


class MemoryProviderRegistry:
    def __init__(self) -> None:
        self._specs: dict[str, MemoryProviderSpec] = {}

    def register(self, spec: MemoryProviderSpec) -> None:
        self._specs[spec.name] = spec

    def get(self, name: str) -> MemoryProviderSpec | None:
        return self._specs.get(name)

    def list(self) -> list[MemoryProviderSpec]:
        return list(self._specs.values())

    def build(
        self,
        name: str,
        config: dict[str, object],
        deps: MemoryRuntimeDeps,
    ) -> MemoryProvider:
        spec = self.get(name)
        if spec is None:
            raise ValueError(f"Unknown memory provider: {name}")
        factory = cast(
            Callable[[dict[str, object], MemoryRuntimeDeps], MemoryProvider],
            spec.provider_cls,
        )
        return factory(config, deps)


MEMORY_PROVIDER_REGISTRY = MemoryProviderRegistry()
