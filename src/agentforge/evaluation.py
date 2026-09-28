"""Offline execution and replay scoring. Gold is never sent to model/runtime."""
import hashlib
import json
import math
import platform
import re
import statistics
import uuid
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from .fixtures import initialize
from .runtime import Runtime
from .tools import build_registry

VARIANTS = ("full", "react", "no_critic", "no_memory")


def numbers(value):
    if type(value) in (int, float):
        return [float(value)]
    if isinstance(value, dict):
        return [n for v in value.values() for n in numbers(v)]
    if isinstance(value, list):
        return [n for v in value for n in numbers(v)]
    return []


def score(record):
    state, gold, events = record["state"], record["gold"], record["events"]
    calls = [e["payload"] for e in events if e["kind"] == "tool_call"]
    evidence = record["evidence"]
    calculated = [n for c in calls if c["ok"] and c["tool"].startswith("code.") for n in numbers(c.get("result", {}))]
    answer_numbers = [float(n) for n in re.findall(r"(?<![\w.])-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?", state["answer"])]
    numeric_ok = all(any(math.isclose(expected, n, rel_tol=.001, abs_tol=.001) for n in calculated)
                     and any(math.isclose(expected, n, rel_tol=.001, abs_tol=.001) for n in answer_numbers)
                     for expected in gold["expected_values"])
    sources = {d.get("url", d.get("id", "")) for e in evidence.values()
               for d in e.get("documents", []) if d.get("url") or d.get("id")}
    selection_accuracy = sum(c["tool"] in gold["relevant_tools"] for c in calls) / len(calls) if calls else 0
    duplicates, seen = 0, set()
    for call in calls:
        signature = json.dumps([call["tool"], call["arguments"]], sort_keys=True)
        if call["ok"] and signature in seen:
            duplicates += 1
        if call["ok"]:
            seen.add(signature)
    invalid = sum(not c["ok"] for c in calls)
    return {"case_id": record["case_id"], "variant": state["variant"], "mode": state["mode"],
            "task_success": int(state["status"] == "completed" and numeric_ok and len(sources) >= gold["min_sources"]),
            "numeric_ok": numeric_ok, "sources": len(sources), "tool_selection_accuracy": selection_accuracy,
            "tool_calls": len(calls), "failed_tool_calls": invalid, "duplicate_tool_calls": duplicates,
            "invalid_tool_calls": invalid + duplicates,
            "trajectory_length": sum(e["kind"] == "node" for e in events), "execution_rounds": state["iterations"],
            "latency_ms": state["elapsed_ms"], "active_latency_ms": state["active_ms"],
            "input_tokens": state["usage"]["input_tokens"], "output_tokens": state["usage"]["output_tokens"],
            "token_cost": state["usage"]["total_tokens"], "estimated_usage": state["usage"]["estimated"],
            "usd_cost": record.get("usd_cost"), "status": state["status"]}


def summarize(rows):
    groups = defaultdict(list)
    for row in rows:
        groups[row["variant"]].append(row)
    metrics = ["task_success", "tool_selection_accuracy", "tool_calls", "invalid_tool_calls",
               "trajectory_length", "execution_rounds", "latency_ms", "token_cost"]
    summary = {variant: {"n": len(items), **{m: statistics.mean(r[m] for r in items) for m in metrics}}
               for variant, items in groups.items()}
    return summary


def export_record(runtime, case, state):
    cfg, usage = runtime.settings, state["usage"]
    usd = None
    if cfg.input_price is not None and cfg.output_price is not None and not usage["estimated"]:
        usd = (usage["input_tokens"] * cfg.input_price + usage["output_tokens"] * cfg.output_price) / 1e6
    return {"schema_version": 1, "case_id": case["id"], "gold": case["gold"], "state": state,
            "events": runtime.store.history(state["run_id"]), "usd_cost": usd,
            "evidence": {e["id"]: runtime.store.get_evidence(e["id"]) for e in state["evidence"]}}


def run_evaluation(settings, cases, output: Path, *, mode="fixture", repeats=1, variants=VARIANTS,
                   workers=1, tool_mode="fixture"):
    if not 1 <= workers <= 8 or repeats < 1:
        raise ValueError("workers must be 1..8; repeats must be positive")
    output.mkdir(parents=True, exist_ok=True)
    corpus, _, analytics = initialize(settings.data_dir)
    rows = []
    started = datetime.now(timezone.utc).isoformat()
    source_hash = hashlib.sha256(b"".join(p.read_bytes() for p in sorted(Path(__file__).parent.glob("*.py")))).hexdigest()
    # Exclusive file creation prevents accidental overwriting of a previous experiment.
    trace_path = output / "traces.jsonl"
    # Initialize shared PostgreSQL schemas before workers to avoid concurrent CREATE TABLE races.
    with Runtime(settings):
        pass

    def evaluate_one(repeat, case, variant):
        if (output / "STOP").exists():
            return None
        namespace = f"eval-{uuid.uuid4().hex}"
        cfg = settings.model_copy(update={"data_dir": settings.data_dir / "eval" / namespace})
        with Runtime(cfg) as runtime:
            runtime.registry = build_registry(cfg, corpus, analytics, fixture=tool_mode == "fixture",
                memory=runtime.memory if variant != "no_memory" else None, namespace=namespace)
            runtime.registry.faults = {k: list(v) for k, v in case.get("faults", {}).items()}
            state = runtime.run(case["goal"], mode=mode, variant=variant, tool_mode=tool_mode,
                fixture_plan=case["fixture_plan"] if mode == "fixture" else None, namespace=namespace)
            record = export_record(runtime, case, state)
            record["repeat"] = repeat
            return record

    with trace_path.open("x", encoding="utf-8") as trace_file, ThreadPoolExecutor(max_workers=workers) as pool:
        tasks = [pool.submit(evaluate_one, repeat, case, variant)
                 for repeat in range(repeats) for case in cases for variant in variants]
        for future in as_completed(tasks):
            record = future.result()
            if record is None:
                continue
            trace_file.write(json.dumps(record, ensure_ascii=False) + "\n")
            trace_file.flush()
            rows.append(score(record))
    summary = summarize(rows)
    metadata = {"started_at": started, "completed_at": datetime.now(timezone.utc).isoformat(),
                "dataset_kind": "synthetic", "mode": mode, "tool_mode": tool_mode, "workers": workers,
                "repeats": repeats, "model": settings.llm_model or None,
                "python": platform.python_version(), "case_count": len(cases),
                "case_sha256": hashlib.sha256(json.dumps(cases, sort_keys=True).encode()).hexdigest(),
                "trace_sha256": hashlib.sha256(trace_path.read_bytes()).hexdigest(), "scorer_version": 2,
                "source_sha256": source_hash, "interrupted": (output / "STOP").exists(),
                "limits": {"max_tokens": settings.max_tokens, "max_iterations": settings.max_iterations,
                           "max_tool_calls": settings.max_tool_calls, "max_seconds": settings.max_seconds},
                "limitations": ["fixture mode uses scripted plans, not a language model",
                    "success is numeric + source coverage proxy, not open-domain research quality",
                    "tool selection accuracy uses per-task relevant-tool labels, not per-decision oracle",
                    "cold namespaced memory prevents leakage; no_memory effect requires a separate warm-memory study"]}
    for name, payload in [("metrics.json", rows), ("summary.json", summary), ("metadata.json", metadata)]:
        (output / name).write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return summary


def replay(path: Path):
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return summarize([score(record) for record in records])
