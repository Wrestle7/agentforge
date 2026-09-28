import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from .config import Settings
from .evaluation import replay, run_evaluation
from .fixtures import initialize, write_fixtures
from .mcp_gateway import MCPGateway
from .memory import Memory
from .storage import Store
from .runtime import Runtime
from .tools import Tool, build_registry, docker_python, object_schema


def output(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def configure_registry(runtime, settings, *, fixture=True, mcp_path=None, with_mcp=False,
                       docker_code=False, namespace="default", variant="full"):
    corpus, _, analytics = initialize(settings.data_dir)
    if settings.corpus_path:
        corpus = json.loads(settings.corpus_path.read_text(encoding="utf-8"))
    registry = build_registry(settings, corpus, analytics, fixture=fixture,
                              memory=runtime.memory if variant != "no_memory" else None, namespace=namespace)
    if with_mcp or mcp_path:
        cfg = json.loads(Path(mcp_path).read_text(encoding="utf-8")) if mcp_path else {
            "command": sys.executable, "args": [str(Path(__file__).with_name("sample_mcp.py"))],
            "allow_tools": ["policy_lookup"]}
        asyncio.run(MCPGateway(cfg).register(registry))
    if docker_code:
        registry.add(Tool("code.python", "Execute Python in a disposable Docker sandbox", "code",
             object_schema({"code": {"type": "string", "maxLength": 8000}}), docker_python, timeout=15, retries=0))
    runtime.registry = registry


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description="AgentForge durable task harness")
    parser.add_argument("--data-dir", type=Path)
    subs = parser.add_subparsers(dest="command", required=True)
    subs.add_parser("init")
    run = subs.add_parser("run")
    run.add_argument("goal", nargs="?")
    run.add_argument("--case", type=Path, default=Path("fixtures/demo.json"))
    run.add_argument("--mode", choices=["fixture", "live"], default="fixture")
    run.add_argument("--tool-mode", choices=["fixture", "live"])
    run.add_argument("--variant", choices=["full", "react", "no_critic", "no_memory"], default="full")
    run.add_argument("--pause-after", type=int, default=0)
    run.add_argument("--run-id")
    run.add_argument("--namespace", default="default")
    run.add_argument("--with-mcp", action="store_true")
    run.add_argument("--mcp-config")
    run.add_argument("--docker-code", action="store_true")
    resume = subs.add_parser("resume")
    resume.add_argument("run_id")
    resume.add_argument("--with-mcp", action="store_true")
    resume.add_argument("--mcp-config")
    resume.add_argument("--docker-code", action="store_true")
    inspect = subs.add_parser("inspect")
    inspect.add_argument("run_id")
    inspect.add_argument("--events", action="store_true")
    evaluate = subs.add_parser("eval")
    evaluate.add_argument("--cases", type=Path, default=Path("fixtures/benchmark.json"))
    evaluate.add_argument("--output", type=Path, required=True)
    evaluate.add_argument("--mode", choices=["fixture", "live"], default="fixture")
    evaluate.add_argument("--limit", type=int, default=0)
    evaluate.add_argument("--repeats", type=int, default=1)
    evaluate.add_argument("--workers", type=int, default=1)
    evaluate.add_argument("--tool-mode", choices=["fixture", "live"], default="fixture")
    rep = subs.add_parser("replay")
    rep.add_argument("path", type=Path)
    subs.add_parser("doctor")
    migrate = subs.add_parser("memory-reindex", help="Copy old memory into the configured embedding space")
    migrate.add_argument("--source-table", default="af_memory")
    migrate.add_argument("--batch-size", type=int, choices=range(1, 11), default=10)
    args = parser.parse_args()
    cfg = Settings(**({"data_dir": args.data_dir} if args.data_dir else {}))
    if args.command == "init":
        _, cases, analytics = initialize(cfg.data_dir)
        write_fixtures(Path("fixtures"))
        output({"analytics": str(analytics), "cases": len(cases), "kind": "synthetic"})
        return
    if args.command == "doctor":
        output({"python": sys.version.split()[0], "data_dir": str(cfg.data_dir),
                "database": "postgresql" if cfg.database_url else "sqlite",
                "llm_model_configured": bool(cfg.llm_model), "llm_key_configured": bool(cfg.llm_api_key),
                "search_configured": bool(cfg.tavily_api_key), "embedding_backend": cfg.embedding_backend,
                "langsmith_tracing": os.getenv("LANGSMITH_TRACING", "false")})
        return
    if args.command == "replay":
        output(replay(args.path))
        return
    if args.command == "memory-reindex":
        store = Store(cfg.data_dir, cfg.database_url)
        try:
            output(Memory(store, cfg).reembed(args.source_table, args.batch_size))
        finally:
            store.close()
        return
    if args.command == "eval":
        cases = json.loads(args.cases.read_text(encoding="utf-8"))
        if args.limit:
            cases = cases[:args.limit]
        output(run_evaluation(cfg, cases, args.output, mode=args.mode, repeats=args.repeats,
                              workers=args.workers, tool_mode=args.tool_mode))
        return
    with Runtime(cfg) as runtime:
        if args.command == "inspect":
            output(runtime.store.history(args.run_id) if args.events else runtime.store.load(args.run_id))
            return
        if args.command == "resume":
            state = runtime.store.load(args.run_id)
            configure_registry(runtime, cfg, fixture=state.get("tool_mode", state["mode"]) == "fixture", with_mcp=args.with_mcp,
                mcp_path=args.mcp_config, docker_code=args.docker_code, namespace=state["namespace"], variant=state["variant"])
            result = runtime.resume(args.run_id)
        else:
            case = json.loads(args.case.read_text(encoding="utf-8")) if args.mode == "fixture" else {}
            goal = args.goal or case.get("goal")
            if not goal:
                parser.error("live run requires a goal")
            configure_registry(runtime, cfg, fixture=(args.tool_mode or args.mode) == "fixture", with_mcp=args.with_mcp,
                mcp_path=args.mcp_config, docker_code=args.docker_code, namespace=args.namespace, variant=args.variant)
            result = runtime.run(goal, mode=args.mode, variant=args.variant, fixture_plan=case.get("fixture_plan"),
                pause_after=args.pause_after, namespace=args.namespace, run_id=args.run_id,
                tool_mode=args.tool_mode)
        output({k: result[k] for k in ("run_id", "status", "stop_reason", "iterations", "tool_calls", "usage", "answer")})
        if result["status"] not in ("completed", "paused"):
            raise SystemExit(2)


if __name__ == "__main__":
    main()
