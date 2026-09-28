"""Send a synthetic AgentForge task trace and read it back from LangSmith."""
import json
import logging
import os
import sys
import time
import uuid
from pathlib import Path

from dotenv import load_dotenv
from langsmith import Client, tracing_context

from agentforge.cli import configure_registry
from agentforge.config import Settings
from agentforge.runtime import Runtime


def main():
    load_dotenv(Path(".env"))
    if os.getenv("LANGSMITH_TRACING", "").lower() != "true":
        raise ValueError("LANGSMITH_TRACING must be true")
    cfg = Settings()
    project = os.getenv("LANGSMITH_PROJECT", "agentforge")
    trace_id = uuid.uuid4()
    client = Client(timeout_ms=15000)
    case = json.loads(Path("fixtures/demo.json").read_text(encoding="utf-8"))
    try:
        with tracing_context(client=client, project_name=project):
            with Runtime(cfg) as runtime:
                namespace = "langsmith-smoke-" + trace_id.hex[:12]
                configure_registry(runtime, cfg, fixture=True, namespace=namespace)
                result = runtime.run(case["goal"], fixture_plan=case["fixture_plan"], namespace=namespace,
                    langsmith_extra={"run_id": trace_id})
        client.flush(timeout=20)
        record = None
        for attempt in range(10):
            try:
                record = client.read_run(trace_id, load_child_runs=True)
                if record.end_time and record.child_runs:
                    break
            except Exception:
                if attempt == 9:
                    raise
            time.sleep(2)
        def walk(node):
            rows = [{"name": node.name, "type": node.run_type, "error": bool(node.error)}]
            for child in node.child_runs or []:
                rows.extend(walk(child))
            return rows
        rows = walk(record)
        report = {"kind": "synthetic AgentForge workflow with real cloud trace",
            "project": project, "trace_id": str(trace_id), "trace_url": record.url,
            "workflow_status": result["status"], "cloud_readback": True,
            "cloud_run_count": len(rows), "tool_runs": sum(r["type"] == "tool" for r in rows),
            "runs": rows}
        private = Path("data/langsmith-smoke.private.json")
        private.parent.mkdir(parents=True, exist_ok=True)
        private.write_text(json.dumps(report, indent=2), encoding="utf-8")
        public = {k: v for k, v in report.items() if k not in {"trace_id", "trace_url"}}
        public["private_trace_details"] = "data/langsmith-smoke.private.json (not published)"
        Path("reports/langsmith-smoke.json").write_text(json.dumps(public, indent=2), encoding="utf-8")
        print(json.dumps({k: v for k, v in report.items() if k != "runs"}, indent=2))
    finally:
        client.close()


if __name__ == "__main__":
    logging.getLogger("langsmith").setLevel(logging.CRITICAL)
    try:
        main()
    except Exception as exc:
        print(f"LangSmith check failed: {type(exc).__name__}. Check key, region, project and network.", file=sys.stderr)
        raise SystemExit(1)
