"""Module-level tool policy callbacks for file and exec operations.

Plugins register policy callbacks during activate() to intercept operations
like file writes and shell executions. When no policy is registered, tools
behave exactly as before — zero overhead.

Use ``set_file_policy(policy, append=True)`` to add a policy without
replacing any existing one.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ToolPolicyCallback(Protocol):
    """Protocol for tool policy callbacks.

    before_operation is called before the tool performs its action.
    Return a non-None string to veto the operation (returned as error).
    Return None to allow the operation.

    after_operation is called after the tool completes.
    Receives the result string and must return a (possibly modified) result.
    """

    async def before_operation(self, op: str, target: str, **kwargs: Any) -> str | None: ...

    async def after_operation(self, op: str, target: str, result: str, **kwargs: Any) -> str: ...


class ChainedPolicy:
    """Run multiple file policies in sequence."""

    def __init__(self, *policies: ToolPolicyCallback) -> None:
        self._policies: list[ToolPolicyCallback] = [p for p in policies if p is not None]

    async def before_operation(self, op: str, target: str, **kwargs: Any) -> str | None:
        for policy in self._policies:
            veto = await policy.before_operation(op, target, **kwargs)
            if veto is not None:
                return veto
        return None

    async def after_operation(self, op: str, target: str, result: str, **kwargs: Any) -> str:
        for policy in self._policies:
            result = await policy.after_operation(op, target, result, **kwargs)
        return result


# Module-level registries — plugins set these during activate()
_file_policy: ToolPolicyCallback | None = None
_exec_policy: ToolPolicyCallback | None = None


def set_file_policy(
    policy: ToolPolicyCallback | None,
    *,
    append: bool = False,
) -> None:
    """Register or clear the file operation policy callback."""
    global _file_policy
    if policy is None:
        _file_policy = None
        return
    if append and _file_policy is not None:
        if isinstance(_file_policy, ChainedPolicy):
            _file_policy._policies.append(policy)
        else:
            _file_policy = ChainedPolicy(_file_policy, policy)
    else:
        _file_policy = policy


def set_exec_policy(policy: ToolPolicyCallback | None) -> None:
    """Register or clear the exec operation policy callback."""
    global _exec_policy
    _exec_policy = policy


def get_file_policy() -> ToolPolicyCallback | None:
    """Return the current file operation policy, or None."""
    return _file_policy


def get_exec_policy() -> ToolPolicyCallback | None:
    """Return the current exec operation policy, or None."""
    return _exec_policy
