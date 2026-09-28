"""Uniform schemas, bounded retry/fallback, audit, and explicitly constrained execution."""
import ast
import asyncio
import json
import math
import operator
import re
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import httpx
from jsonschema import Draft202012Validator
from langsmith import traceable


class ToolFault(Exception):
    def __init__(self, code, message, retryable=False):
        super().__init__(message)
        self.code, self.retryable = code, retryable


@dataclass
class Tool:
    name: str
    description: str
    capability: str
    schema: dict
    handler: Callable[[dict], Awaitable[Any]]
    timeout: float = 10
    retries: int = 1
    fallback: str | None = None
    permission: str = "read"
    output_description: str = "JSON result, stored as Evidence"


@dataclass
class Registry:
    tools: dict[str, Tool] = field(default_factory=dict)
    # Injectable deterministic failures for tests/eval; never hidden in production.
    faults: dict[str, list[str]] = field(default_factory=dict)

    def add(self, tool):
        Draft202012Validator.check_schema(tool.schema)
        if tool.name in self.tools:
            raise ValueError(f"duplicate tool: {tool.name}")
        self.tools[tool.name] = tool

    def catalog(self, capability=None):
        return [{"name": t.name, "description": t.description, "capability": t.capability,
                 "input_schema": t.schema, "permission": t.permission, "output": t.output_description}
                for t in self.tools.values() if not capability or t.capability == capability]

    def choose(self, capability, preferred=None, excluded=()):
        candidates = [t for t in self.tools.values() if t.capability == capability and t.name not in excluded]
        if preferred:
            candidates.sort(key=lambda t: t.name != preferred)
        if not candidates:
            raise ToolFault("no_tool", f"no available tool for {capability}")
        return candidates[0].name

    @traceable(name="tool_gateway", run_type="tool")
    async def execute(self, name, args, *, remaining_calls=10):
        started = time.monotonic()
        calls, visited = [], set()
        while name and name not in visited and len(calls) < remaining_calls:
            visited.add(name)
            tool = self.tools.get(name)
            if tool is None:
                calls.append({"tool": name, "arguments": args, "ok": False, "error": "unknown_tool",
                              "retryable": False, "latency_ms": 0, "attempt": 0})
                break
            for attempt in range(tool.retries + 1):
                if len(calls) >= remaining_calls:
                    break
                t0 = time.monotonic()
                call = {"tool": name, "arguments": args, "attempt": attempt, "ok": False,
                        "retryable": False}
                try:
                    errors = list(Draft202012Validator(tool.schema).iter_errors(args))
                    if errors:
                        raise ToolFault("invalid_arguments", errors[0].message[:300])
                    if tool.permission != "read":
                        raise ToolFault("permission_denied", "Only explicitly registered read tools are enabled")
                    queue = self.faults.get(name, [])
                    if queue:
                        code = queue.pop(0)
                        raise ToolFault(code, f"injected fixture fault: {code}", code in {"timeout", "rate_limit", "failure"})
                    result = await asyncio.wait_for(tool.handler(args), timeout=tool.timeout)
                    if result is None or result == [] or result == {} or result == "":
                        raise ToolFault("empty_result", "Tool returned no usable evidence")
                    if len(json.dumps(result, ensure_ascii=False).encode()) > 65536:
                        raise ToolFault("result_too_large", "Narrow query; result exceeds 64 KiB")
                    call.update(ok=True, result=result)
                except asyncio.TimeoutError:
                    call.update(error="timeout", message="tool deadline exceeded", retryable=True)
                except httpx.HTTPStatusError as exc:
                    status = exc.response.status_code
                    call.update(error="rate_limit" if status == 429 else "external_api_failure",
                                message=f"HTTP {status}", retryable=status == 429 or status >= 500)
                except ToolFault as exc:
                    call.update(error=exc.code, message=str(exc), retryable=exc.retryable)
                except Exception as exc:
                    call.update(error="tool_failure", message=type(exc).__name__, retryable=True)
                call["latency_ms"] = (time.monotonic() - t0) * 1000
                calls.append(call)
                if call["ok"]:
                    return {"ok": True, "tool": name, "result": call["result"], "calls": calls,
                            "latency_ms": (time.monotonic() - started) * 1000}
                if not call["retryable"]:
                    break
                if attempt < tool.retries and len(calls) < remaining_calls:
                    await asyncio.sleep(min(0.1 * 2 ** attempt, 1))
            name = tool.fallback
        last = calls[-1] if calls else {"error": "call_budget", "retryable": False}
        return {"ok": False, "error": last["error"], "retryable": last.get("retryable", False),
                "calls": calls, "reflection": f"{last['error']}: inspect arguments, evidence and alternate capabilities",
                "latency_ms": (time.monotonic() - started) * 1000}


def object_schema(properties, required=None):
    return {"type": "object", "properties": properties,
            "required": required if required is not None else list(properties), "additionalProperties": False}


QUERY = object_schema({"query": {"type": "string", "minLength": 1, "maxLength": 1000}})


def evaluate_expression(expression, variables):
    """Small arithmetic DSL. This is not a Python sandbox; no eval/exec/import/calls."""
    if len(expression) > 500 or len(variables) > 30:
        raise ToolFault("invalid_expression", "expression too large")
    tree = ast.parse(expression, mode="eval")
    if len(list(ast.walk(tree))) > 100:
        raise ToolFault("invalid_expression", "expression too complex")
    operations = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
                  ast.Div: operator.truediv, ast.Mod: operator.mod}

    def visit(node):
        if isinstance(node, ast.Expression):
            return visit(node.body)
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            value = node.value
        elif isinstance(node, ast.Name) and node.id in variables:
            value = variables[node.id]
        elif isinstance(node, ast.BinOp) and type(node.op) in operations:
            value = operations[type(node.op)](visit(node.left), visit(node.right))
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = visit(node.operand) * (-1 if isinstance(node.op, ast.USub) else 1)
        else:
            raise ToolFault("invalid_expression", "only numbers, variables and + - * / % are allowed")
        if type(value) not in (int, float) or not math.isfinite(value) or abs(value) > 1e15:
            raise ToolFault("invalid_expression", "number exceeds finite arithmetic bounds")
        return value
    return visit(tree)


def build_registry(settings, corpus, analytics_path, *, fixture=True, memory=None, namespace="default"):
    registry = Registry()

    async def rag(args):
        tokens = set(re.findall(r"\w+", args["query"].lower()))
        scored = sorted([(len(tokens & set(re.findall(r"\w+", d["text"].lower()))), d)
                         for d in corpus], key=lambda x: x[0], reverse=True)
        docs = [dict(d, score=score) for score, d in scored[:3] if score]
        return {"documents": docs} if docs else []

    async def web(args):
        if not settings.tavily_api_key:
            raise ToolFault("configuration", "AF_TAVILY_API_KEY is required for real web search")
        async with httpx.AsyncClient(timeout=8) as client:
            response = await client.post("https://api.tavily.com/search",
                json={"api_key": settings.tavily_api_key, "query": args["query"], "max_results": 3})
            response.raise_for_status()
            rows = response.json().get("results", [])
            return {"documents": [{"url": d["url"], "text": d["content"][:5000]} for d in rows]} if rows else []

    async def sql(args):
        def query():
            # Read-only URI + authorizer; keywords alone are not a security boundary.
            with sqlite3.connect(analytics_path.resolve().as_uri() + "?mode=ro", uri=True) as conn:
                conn.execute("PRAGMA query_only=ON")
                allowed = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION}
                conn.set_authorizer(lambda action, *rest: sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY)
                deadline = time.monotonic() + 2
                conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
                cursor = conn.execute(args["query"], args.get("params", {}))
                names = [d[0] for d in cursor.description]
                rows = [dict(zip(names, r)) for r in cursor.fetchmany(101)]
                if len(rows) > 100:
                    raise ToolFault("result_too_large", "SQL result exceeds 100 rows")
                return {"rows": rows} if rows else []
        try:
            return await asyncio.to_thread(query)
        except sqlite3.Error:
            raise ToolFault("sql_error", "SQL rejected, timed out, or invalid; inspect schema/query") from None

    async def code(args):
        try:
            return {"value": evaluate_expression(args["expression"], args.get("variables", {}))}
        except (SyntaxError, ZeroDivisionError):
            raise ToolFault("invalid_expression", "syntax error or division by zero") from None

    registry.add(Tool("web.search", "Search external web; fixture mode returns explicitly synthetic documents",
                      "web", QUERY, rag if fixture else web, fallback="rag.search",
                      output_description='{"documents":[{"id":"...","url":"...","text":"..."}]}'))
    registry.add(Tool("rag.search", "Retrieve local corpus passages with source IDs (lexical baseline)",
                      "rag", QUERY, rag, output_description='{"documents":[{"id":"...","url":"...","text":"..."}]}'))
    registry.add(Tool("sql.query", "Read analytics: metrics(domain TEXT, period TEXT, value REAL, cost REAL). "
                      "Valid periods: previous, current. Use domain to filter; include period in SELECT when reading both.",
                      "sql", object_schema({"query": {"type": "string", "maxLength": 3000},
                        "params": {"type": "object"}}, ["query"]), sql,
                      output_description='{"rows":[{"selected_column":123}]}; row order follows SQL ORDER BY, never infer period from position'))
    registry.add(Tool("code.calculate", "Execute bounded arithmetic expressions with numeric variables", "code",
        object_schema({"expression": {"type": "string", "maxLength": 500},
                       "variables": {"type": "object", "additionalProperties": {"type": "number"}}},
                      ["expression"]), code, output_description='{"value":123}; dependency reference is step_id.value, not step_id.rows.0.value'))
    if memory:
        async def memory_search(args):
            return await asyncio.to_thread(memory.search, namespace, args["query"])
        registry.add(Tool("memory.search", "Retrieve namespaced cross-session vector memory", "rag", QUERY,
                          memory_search))
    return registry


async def docker_python(args):
    """Optional real Python execution in a disposable, resource-constrained container."""
    import uuid
    name = "af-code-" + uuid.uuid4().hex[:12]
    process = await asyncio.create_subprocess_exec(
        "docker", "run", "--rm", "--name", name, "--network", "none", "--read-only",
        "--memory", "128m", "--cpus", "0.5", "--pids-limit", "32", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges", "--user", "65534:65534",
        "--tmpfs", "/tmp:rw,noexec,nosuid,size=16m", "-i", "python:3.13-slim", "python", "-I", "-",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    tasks = []

    async def read_bounded(stream):
        chunks = bytearray()
        while data := await stream.read(4096):
            if len(chunks) + len(data) > 16384:
                raise ToolFault("output_limit", "Python output exceeds 16 KiB per stream")
            chunks.extend(data)
        return bytes(chunks)

    try:
        process.stdin.write(args["code"].encode())
        await process.stdin.drain()
        process.stdin.close()
        tasks = [asyncio.create_task(read_bounded(process.stdout)),
                 asyncio.create_task(read_bounded(process.stderr)), asyncio.create_task(process.wait())]
        stdout, stderr, _ = await asyncio.wait_for(asyncio.gather(*tasks), 8)
        if process.returncode:
            raise ToolFault("code_failure", stderr.decode(errors="replace")[:500])
        return {"stdout": stdout.decode(errors="replace")[:16000]}
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        cleanup = await asyncio.create_subprocess_exec("docker", "rm", "-f", name,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        try:
            await asyncio.wait_for(cleanup.wait(), 3)
        except TimeoutError:
            cleanup.kill()
            await cleanup.wait()
        if process.returncode is None:
            process.kill()
            await process.wait()
