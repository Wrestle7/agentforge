"""Summarize a COMPLETE paired experiment, including unfavorable outcomes."""
import argparse
import json
import random
import statistics
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    directory = args.directory
    rows = json.loads((directory / "metrics.json").read_text(encoding="utf-8"))
    metadata = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
    variants = ("full", "react", "no_critic", "no_memory")
    if metadata.get("interrupted"):
        raise SystemExit("Interrupted experiment cannot be reported as a complete comparison")
    groups = {v: [r for r in rows if r["variant"] == v] for v in variants}
    if any(len(g) != metadata["case_count"] * metadata["repeats"] for g in groups.values()):
        raise SystemExit("Incomplete or unbalanced experiment")
    means = {v: {m: statistics.mean(r[m] for r in group) for m in
              ("task_success", "invalid_tool_calls", "execution_rounds", "token_cost", "latency_ms",
               "trajectory_length", "tool_selection_accuracy")} for v, group in groups.items()}
    # Average repeats per task before pairing. Cluster bootstrap across 10 template families.
    by_case = {}
    for v in ("full", "react"):
        for case_id in {r["case_id"] for r in groups[v]}:
            selected = [r for r in groups[v] if r["case_id"] == case_id]
            by_case.setdefault(case_id, {})[v] = {m: statistics.mean(r[m] for r in selected)
                                                for m in means[v]}
    if any(set(case) != {"full", "react"} for case in by_case.values()):
        raise SystemExit("Cases do not pair")
    clusters = {}
    for case_id, values in by_case.items():
        category = case_id.split("-", 1)[1]
        clusters.setdefault(category, []).append({m: values["full"][m] - values["react"][m]
                                                  for m in means["full"]})
    rng = random.Random(20260928)
    intervals = {}
    for metric in ("task_success", "invalid_tool_calls", "execution_rounds", "token_cost"):
        samples = []
        for _ in range(3000):
            picked = rng.choices(list(clusters.values()), k=len(clusters))
            samples.append(statistics.mean(row[metric] for cluster in picked for row in cluster))
        samples.sort()
        intervals[metric] = {"mean_full_minus_react": means["full"][metric] - means["react"][metric],
                             "cluster_bootstrap_95pct": [samples[75], samples[2924]]}
    (directory / "comparison.json").write_text(json.dumps(intervals, indent=2), encoding="utf-8")
    model_label = metadata["model"] if metadata["mode"] == "live" else "未调用模型（脚本控制）"
    lines = ["# AgentForge 实测结果", "", f"模型：{model_label}；模型模式：{metadata['mode']}；工具数据：{metadata['tool_mode']}。",
             f"每配置 {len(groups['full'])} 次运行，{metadata['case_count']} 个合成任务，{metadata['repeats']} 次重复。", "",
             "| 配置 | 成功率代理 | 无效调用/任务 | 执行轮数 | Tokens/任务 | 墙钟秒/任务 | 图节点数 | 工具相关率 |",
             "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for variant in variants:
        m = means[variant]
        lines.append(f"| {variant} | {m['task_success']:.1%} | {m['invalid_tool_calls']:.2f} | "
                     f"{m['execution_rounds']:.2f} | {m['token_cost']:.0f} | {m['latency_ms']/1000:.2f} | "
                     f"{m['trajectory_length']:.2f} | {m['tool_selection_accuracy']:.1%} |")
    lines += ["", "Full - ReAct 的配对差异（按模板家族聚类 bootstrap，3000 次；单次实验，仅作描述性参考）：", ""]
    for metric, values in intervals.items():
        lo, hi = values["cluster_bootstrap_95pct"]
        lines.append(f"- {metric}: {values['mean_full_minus_react']:.4f}，区间 [{lo:.4f}, {hi:.4f}]")
    full, react = means["full"], means["react"]
    target = (full["invalid_tool_calls"] < react["invalid_tool_calls"]
              and full["execution_rounds"] < react["execution_rounds"] and full["token_cost"] <= react["token_cost"])
    lines += ["", "相对于 Single ReAct，同时减少无效调用和执行轮数，且 token 不增加：" + ("本实验观察到。" if target else "本实验未达到。"),
              "", "这是固定合成资料上的" + ("真实模型" if metadata["mode"] == "live" else "脚本控制流")
              + "比较；成功率是数值与来源覆盖代理，工具相关率为任务级标签代理。",
              "所有失败保留在分母；不能据此推断真实互联网深度研究、跨会话记忆或所有复杂任务的效果。",
              "原始数据：traces.jsonl；逐条指标：metrics.json；版本与哈希：metadata.json。"]
    (directory / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
