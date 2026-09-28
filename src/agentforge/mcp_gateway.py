"""MCP Python SDK client: allowlisted stdio / Streamable HTTP tools."""
from contextlib import asynccontextmanager
from datetime import timedelta
import json

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

from .tools import Tool, ToolFault


class MCPGateway:
    def __init__(self, config):
        self.config = config

    @asynccontextmanager
    async def session(self):
        cfg = self.config
        if cfg.get("transport", "stdio") == "stdio":
            params = StdioServerParameters(command=cfg["command"], args=cfg.get("args", []), env=cfg.get("env"))
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=10)) as session:
                    await session.initialize()
                    yield session
        else:
            async with streamable_http_client(cfg["url"]) as (read, write, _):
                async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=10)) as session:
                    await session.initialize()
                    yield session

    async def register(self, registry):
        async with self.session() as session:
            discovered = await session.list_tools()
        allowed = set(self.config.get("allow_tools", []))
        for tool in discovered.tools:
            if tool.name not in allowed:
                continue
            remote_name = tool.name

            async def handler(args, remote_name=remote_name):
                async with self.session() as session:
                    result = await session.call_tool(remote_name, args)
                    if result.isError:
                        raise ToolFault("mcp_error", "MCP server reported an error")
                    if result.structuredContent is not None:
                        return result.structuredContent
                    return {"content": [c.model_dump(mode="json") for c in result.content]}
            registry.add(Tool("mcp." + remote_name, tool.description or remote_name, "mcp", tool.inputSchema,
                              handler, timeout=20, retries=1,
                              output_description=json.dumps(tool.outputSchema) if tool.outputSchema else "MCP structuredContent or content blocks"))
