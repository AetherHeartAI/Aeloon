"""Benchmark flow implementation."""

from __future__ import annotations

import asyncio

import typer

from aeloon.cli.app import console
from aeloon.cli.runtime_helpers import (
    load_runtime_config,
    make_provider,
    print_deprecated_memory_window_notice,
)
from aeloon.utils.helpers import sync_workspace_templates


def run_benchmark(
    *,
    scenario: str | None,
    repeat: int,
    output: str,
    workspace: str | None,
    config: str | None,
) -> None:
    """Run profiling benchmarks across predefined scenarios."""
    from aeloon.core.agent.loop import AgentLoop
    from aeloon.core.bus.queue import MessageBus
    from aeloon.core.config.paths import get_cron_dir
    from aeloon.services.cron.service import CronService
    from benchmarks.runner import (
        aggregate_results,
        format_results_table,
        load_scenarios,
        results_to_json,
        run_scenarios,
    )

    if output not in {"table", "json"}:
        console.print("[red]Error: --output must be one of: table, json[/red]")
        raise typer.Exit(1)

    loaded_config = load_runtime_config(config, workspace)
    print_deprecated_memory_window_notice(loaded_config)
    sync_workspace_templates(
        loaded_config.workspace_path,
        include_file_memory=loaded_config.memory.prompt.enabled,
    )

    agent_loop = AgentLoop(
        bus=MessageBus(),
        provider=make_provider(loaded_config),
        workspace=loaded_config.workspace_path,
        model=loaded_config.agents.defaults.model,
        max_iterations=loaded_config.agents.defaults.max_tool_iterations,
        context_window_tokens=loaded_config.agents.defaults.context_window_tokens,
        web_search_config=loaded_config.tools.web.search,
        web_proxy=loaded_config.tools.web.proxy or None,
        exec_config=loaded_config.tools.exec,
        cron_service=CronService(get_cron_dir() / "jobs.json"),
        restrict_to_workspace=loaded_config.tools.restrict_to_workspace,
        mcp_servers=loaded_config.tools.mcp_servers,
        channels_config=loaded_config.channels,
        memory_config=loaded_config.memory,
        output_mode=loaded_config.agents.defaults.output_mode,
        fast=loaded_config.agents.defaults.fast,
    )
    scenarios = load_scenarios(selector=scenario)
    if not scenarios:
        console.print(f"[red]No benchmark scenarios matched: {scenario}[/red]")
        raise typer.Exit(1)

    async def _run_suite() -> None:
        try:
            run_results = await run_scenarios(agent_loop, scenarios, repeat=repeat)
            aggregates = aggregate_results(run_results)
            console.print(
                results_to_json(aggregates)
                if output == "json"
                else format_results_table(aggregates)
            )
        finally:
            await agent_loop.close_mcp()

    asyncio.run(_run_suite())
