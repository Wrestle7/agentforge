"""Real-provider synthetic retrieval smoke. No keys or source memories in report."""
import argparse
import json
import sys
import tempfile
import time
import uuid
from pathlib import Path

from agentforge.config import Settings
from agentforge.memory import Memory
from agentforge.storage import Store

CASES = [
    ("checkpoint", "执行中断后，系统通过保存的检查点恢复任务，不必从头开始。", "程序崩溃后怎样接着干活？"),
    ("retry", "接口遇到限流时，遵循 Retry-After，采用指数退避重试。", "请求太频繁被服务商拒绝怎么办？"),
    ("safety", "仓库文件写入之前，必须展示差异并获得人工审批。", "如何避免智能体未经同意修改代码？"),
    ("context", "上下文选择按照预算挑选相关函数、调用方和测试，省略无关代码。", "项目太大，模型读不下全部源码怎么办？"),
    ("vector", "向量数据库通过语义相似度召回历史记忆，不依赖字面关键词完全一致。", "换个说法提问还能找到过去的经验吗？"),
    ("dag", "有依赖关系的任务组成有向无环图；前置任务成功后才调度后续任务。", "多个步骤有先后条件，该按什么顺序执行？"),
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--postgres", action="store_true", help="Use the configured PostgreSQL DB")
    parser.add_argument("--output", type=Path, default=Path("reports/embedding-smoke.json"))
    args = parser.parse_args()
    cfg = Settings()
    if cfg.embedding_backend == "hash":
        parser.error("Configure api or local first; hash is not a semantic model")
    if cfg.embedding_backend == "api" and (not cfg.embedding_api_key or "YOUR_WORKSPACE_ID" in cfg.embedding_base_url):
        parser.error("Fill AF_EMBEDDING_API_KEY and AF_EMBEDDING_BASE_URL in agentforge/.env")
    if args.postgres and not cfg.database_url:
        parser.error("--postgres requires AF_DATABASE_URL")
    namespace = "embedding-smoke-" + uuid.uuid4().hex[:12]
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="af-embedding-") as temp:
        store = Store(Path(temp), cfg.database_url if args.postgres else "")
        try:
            memory = Memory(store, cfg)
            for label, document, _ in CASES:
                memory.add(namespace, label, {"text": document})
            rows = []
            for label, _, question in CASES:
                found = memory.search(namespace, question, limit=1)
                rows.append({"expected": label, "actual": found[0]["source_run"],
                             "success": found[0]["source_run"] == label})
            isolated = memory.search(namespace + "-other", CASES[0][2]) == []
            report = {"kind": "synthetic semantic retrieval smoke; not the task benchmark",
                "backend": cfg.embedding_backend, "model": cfg.embedding_model, "dimension": cfg.embedding_dim,
                "database": store.engine.dialect.name, "memory_table": memory.table.name,
                "queries": len(rows), "top1_hits": sum(row["success"] for row in rows),
                "namespace_isolation": isolated, "elapsed_seconds": round(time.perf_counter() - started, 3),
                "rows": rows}
        finally:
            store.close()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Provider exception bodies may echo credentials or private input.
        print(f"Embedding check failed: {type(exc).__name__}. Check endpoint, region, key and network.", file=sys.stderr)
        raise SystemExit(1)
