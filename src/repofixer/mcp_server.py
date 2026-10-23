"""MCP (Model Context Protocol) server exposing RepoFixer's tool set.

Scope: the tools are identical to the in-process agent's; wrapping them as MCP
means any MCP client (Claude Code, Gemini CLI, Cline, custom UIs) can use them,
and the standard can drive heterogeneous tool ecosystems.

Install the optional dependency first:  uv sync --extra mcp
Run:  repofixer mcp --workspace <repo>
"""
from __future__ import annotations

from pathlib import Path

from .environment import LocalEnv
from .tools import Tool, build_tools


def to_mcp_tools(tools: list[Tool]) -> list[dict]:
    """Pure conversion (no `mcp` import needed) so it stays unit-testable."""
    return [
        {"name": t.name, "description": t.description, "inputSchema": t.parameters}
        for t in tools
    ]


def serve(workspace: Path) -> None:  # pragma: no cover - requires the mcp SDK
    try:
        from mcp import types
        from mcp.server import Server
        from mcp.server.stdio import stdio_server
    except ImportError as e:  # pragma: no cover
        raise SystemExit(
            "MCP support is optional: run `uv sync --extra mcp` first "
            f"(import error: {e})"
        )
    import asyncio

    env = LocalEnv(workspace.resolve())
    tools = build_tools(env)
    by_name = {t.name: t for t in tools}
    server = Server("repofixer")

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [
            types.Tool(name=spec["name"], description=spec["description"], inputSchema=spec["inputSchema"])
            for spec in to_mcp_tools(tools)
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
        tool = by_name.get(name)
        if tool is None:
            return [types.TextContent(type="text", text=f"unknown tool: {name}")]
        result = tool.handler(**arguments)
        return [types.TextContent(type="text", text=result.output)]

    async def _main() -> None:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())

    asyncio.run(_main())
