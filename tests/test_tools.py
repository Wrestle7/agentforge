import asyncio

import pytest

from agentforge.tools import Registry, Tool, ToolFault, evaluate_expression, object_schema


@pytest.mark.parametrize("expression", ["__import__('os').system('echo bad')", "2**99999999", "[1]*1000000", "x.__class__"])
def test_expression_boundary(expression):
    with pytest.raises(ToolFault):
        evaluate_expression(expression, {"x": 1})


async def test_sql_readonly_and_empty(runtime):
    rejected = await runtime.registry.execute("sql.query", {"query": "DELETE FROM metrics"})
    assert rejected["error"] == "sql_error"
    query = await runtime.registry.execute("sql.query", {"query": "SELECT count(*) AS n FROM metrics"})
    assert query["result"]["rows"][0]["n"] == 12
    empty = await runtime.registry.execute("sql.query", {"query": "SELECT * FROM metrics WHERE 1=0"})
    assert empty["error"] == "empty_result"


async def test_real_timeout_cancels_coroutine():
    cancelled = asyncio.Event()

    async def slow(args):
        try:
            await asyncio.sleep(2)
        finally:
            cancelled.set()
    registry = Registry()
    registry.add(Tool("slow", "slow", "web", object_schema({}), slow, timeout=.01, retries=0))
    result = await registry.execute("slow", {})
    assert result["error"] == "timeout"
    assert cancelled.is_set()


async def test_schema_unknown_tools_and_empty(runtime):
    assert (await runtime.registry.execute("missing", {}))["error"] == "unknown_tool"
    assert (await runtime.registry.execute("web.search", {"q": "test"}))["error"] == "invalid_arguments"
    assert (await runtime.registry.execute("rag.search", {"query": "zzzz-absent"}))["error"] == "empty_result"


async def test_mcp_real_stdio_protocol(runtime):
    import sys
    from pathlib import Path
    import agentforge.sample_mcp
    from agentforge.mcp_gateway import MCPGateway
    gateway = MCPGateway({"command": sys.executable, "args": [str(Path(agentforge.sample_mcp.__file__))],
                          "allow_tools": ["policy_lookup"]})
    await gateway.register(runtime.registry)
    result = await runtime.registry.execute("mcp.policy_lookup", {"domain": "retail"})
    assert result["ok"], result
    assert result["result"]["minimum_growth"] == 10
