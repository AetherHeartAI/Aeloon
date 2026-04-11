"""Typed exceptions for the memory backend framework."""


class MemoryBackendError(Exception):
    """Base exception for memory backend failures."""


class UnknownMemoryBackendError(MemoryBackendError, ValueError):
    """Raised when a backend name is not registered."""


class InvalidMemoryBackendClassError(MemoryBackendError, TypeError):
    """Raised when a resolved backend class does not implement the contract."""


class MissingMemoryBackendDependencyError(MemoryBackendError, RuntimeError):
    """Raised when a selected backend needs an optional dependency that is unavailable."""
