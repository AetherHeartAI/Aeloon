"""Additive memory providers."""

from aeloon.memory.providers.base import MemoryProvider
from aeloon.memory.providers.manager import ProviderManager
from aeloon.memory.providers.openviking import OpenVikingProvider
from aeloon.memory.providers.registry import (
    MEMORY_PROVIDER_REGISTRY,
    MemoryProviderRegistry,
    MemoryProviderSpec,
)

MEMORY_PROVIDER_REGISTRY.register(
    MemoryProviderSpec(
        name=OpenVikingProvider.name,
        provider_cls=OpenVikingProvider,
        description="OpenViking additive provider",
    )
)

__all__ = [
    "MEMORY_PROVIDER_REGISTRY",
    "MemoryProvider",
    "MemoryProviderRegistry",
    "MemoryProviderSpec",
    "ProviderManager",
    "OpenVikingProvider",
]
