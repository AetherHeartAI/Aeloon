"""Repository-level pytest helpers for optional components and async tests."""

from __future__ import annotations

import asyncio
import importlib.util
import inspect
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

_OPTIONAL_TEST_NAME_PREFIXES: dict[str, str] = {
    "test_kb_": "aeloon.plugins.KnowledgeBase",
    "test_market_": "aeloon.plugins.MarketResearch",
    "test_pet_manor_": "aeloon.plugins.PetManor",
    "test_se_": "aeloon.plugins.SoftwareEngineering",
    "test_soulanchor_": "aeloon.plugins.SoulAnchor",
}

_OPTIONAL_TEST_NODE_PREFIXES: dict[str, tuple[str, ...]] = {
    "aeloon.plugins.FilesystemSnapshot": (
        "tests/test_fs_plugin.py::TestFsPluginManifest::",
        "tests/test_fs_plugin.py::TestFsConfig::",
        "tests/test_fs_plugin.py::TestFsCommand::",
        "tests/test_fs_plugin.py::TestSnapshotService::",
        "tests/test_fs_plugin.py::TestAuditBufferService::",
        "tests/test_fs_plugin.py::TestFsPluginRegistration::",
        "tests/test_fs_plugin.py::TestSnapshotControlTool::",
    ),
    "aeloon.plugins.SoftwareEngineering": ("tests/test_se_",),
    "aeloon.plugins.SoulAnchor": ("tests/test_soulanchor_",),
}


def _module_missing(module_name: str) -> bool:
    return importlib.util.find_spec(module_name) is None


def pytest_addoption(parser: Any) -> None:
    """Register lightweight asyncio config compatibility."""
    parser.addini("asyncio_mode", "Compatibility shim for async tests", default="auto")


def pytest_configure(config: Any) -> None:
    """Register local async marker documentation."""
    config.addinivalue_line("markers", "asyncio: run test in a fresh asyncio event loop")


def pytest_ignore_collect(collection_path: Path, config: Any) -> bool:  # noqa: ARG001
    """Skip optional-plugin test modules when the plugin package is absent."""
    test_name = collection_path.name
    for prefix, module_name in _OPTIONAL_TEST_NAME_PREFIXES.items():
        if test_name.startswith(prefix):
            return _module_missing(module_name)
    return False


def pytest_collection_modifyitems(config: Any, items: list[Any]) -> None:  # noqa: ARG001
    """Skip collected plugin-specific tests when optional packages are absent."""
    for item in items:
        for module_name, node_prefixes in _OPTIONAL_TEST_NODE_PREFIXES.items():
            if _module_missing(module_name) and any(
                item.nodeid.startswith(node_prefix) for node_prefix in node_prefixes
            ):
                item.add_marker(
                    pytest.mark.skip(reason=f"Optional plugin package not present: {module_name}")
                )
                break


@pytest.fixture(autouse=True)
def _restore_memory_registry() -> Iterator[None]:
    try:
        from aeloon.memory import registry as memory_registry
    except ImportError:
        yield
        return

    snapshot = dict(memory_registry._REGISTRY)
    try:
        yield
    finally:
        memory_registry._REGISTRY.clear()
        memory_registry._REGISTRY.update(snapshot)


def pytest_pyfunc_call(pyfuncitem: Any) -> bool | None:
    """Run coroutine tests without requiring external pytest-asyncio."""
    test_function = pyfuncitem.obj
    if not inspect.iscoroutinefunction(test_function):
        return None

    kwargs = {
        name: pyfuncitem.funcargs[name]
        for name in pyfuncitem._fixtureinfo.argnames
        if name in pyfuncitem.funcargs
    }
    asyncio.run(test_function(**kwargs))
    return True
