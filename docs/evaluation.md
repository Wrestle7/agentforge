# 可复现评测说明

## 数据与实验分层

`fixtures/benchmark.json` 是 60 个明确标注的合成多步任务：6 个业务领域 × 10 种 DAG 模板，每条 5–6 个步骤。任务覆盖增长、单位成本、利润率、预测、容量、成本冲击、加权预测、回本代理、盈亏平衡和效率变化；部分任务需要二阶敏感性计算。它不是 60 个彼此完全独立的真实研究问题，也不是外部权威 benchmark。

有两类运行：

1. `--mode fixture`：脚本化规划和规则 Critic，零模型 tokens。用途是验证控制流、存储和评分可复现，不能衡量 LLM 能力。
2. `--mode live --tool-mode fixture`：真实 DeepSeek 进行规划、选择、批判、汇总，工具返回固定合成资料。用途是可控环境下的 Agent 组织方式对照；不评估实时 Web 搜索质量。

live 模式不会把 fixture_plan 或 gold 交给模型。工具 Schema、数据库表结构和任务描述属于可见输入，参考数值留在评分器。各配置使用独立命名空间，不共享同题答案。

## 指标定义

| 指标 | 定义及分母 | 限制 |
|---|---|---|
| Task Success | completed 且 gold 中所有数值出现在 Code 工具成功结果和最终文本中，并满足不同来源数要求；成功任务数/全部任务数 | 数值/来源覆盖代理，不验证长报告全部语义或所有论证是否成立 |
| Tool Selection Accuracy | 选择的工具属于该任务 relevant_tools 的尝试次数/全部工具尝试次数 | 粗粒度相关性代理；不是逐决策人工正确性标签。错误参数单列失败调用 |
| Invalid Tool Calls | 失败尝试 + 同参数的重复成功调用 | 合理的必要重试也可能失败；必须连同成功率解释，不宜单独优化 |
| Execution Rounds | 每次 Agent 执行决策计一轮，Gateway 内部重试不额外计轮 | 不能与节点数混用 |
| Trajectory Length | node 事件数，包含规划、调度、Critic、汇总等 | 多角色结构天然增加节点数 |
| Latency | scorer v2 用任务开始至完成的墙钟毫秒；active_latency_ms 另存节点活动耗时 | 并发吞吐测试下共享主机/服务负载影响延迟；不是独占服务器基准 |
| Token Cost | 模型服务端 input_tokens + output_tokens，各角色与格式重试均累计 | fixture 为 0；estimated 时不能当作精确实际用量 |
| USD Cost | 用户提供 input/output 单价时才折算，否则 null | 未细分缓存折扣，不等同于供应商最终账单 |

终止/失败样本保留在分母中，不删除困难题。每个 case 每个 variant 都有一行结果。正式比较需同时查看成功率、无效调用、轮数、Token 和耗时，不能只选有利指标。

数值匹配使用 `math.isclose(rel_tol=0.001, abs_tol=0.001)`。当前未归一化百分数与小数的等价表达，也未判断表述上的近似取整；要求参考数值出现在计算工具结果中。因此“答案心算正确但工具未验证该数值”、或合理取整超出容差，仍可能被记为失败。这是严格执行代理，不应冒充人工认定的答案正确率。

## 对照差异

| 配置 | Planner DAG | 独立 Critic | 长期记忆 | 工具治理 / Checkpoint |
|---|---:|---:|---:|---:|
| full | 是 | 是 | 是 | 是 |
| react | 否，单 Agent 持续决策 | 否 | 是 | 是 |
| no_critic | 是 | 否 | 是 | 是 |
| no_memory | 是 | 是 | 否 | 是 |

Full/ReAct 使用相同最终汇总器。fixture 的 ReAct 是确定性控制流替身，不是假装真实 ReAct 推理的结果。冷记忆实验中的 No-Memory 没有 warm-start 优势可移除；这组比较只验证消融开关和开销，记忆收益仍需独立实验。

## 结果文件与版本

- `reports/fixture-v1/`：第一版离线 240 次结果；所有配置成功率代理 100%，平均执行轮数/调用数均为 5.5，tokens 为 0。此版本 latency_ms 记录节点活动时间，是 scorer v1；不要直接与 scorer v2 latency 相比。
- `reports/fixture-v2/`：最终代码的离线 240 次结果，scorer v2；已验证回放结果与原 summary 完全一致。
- `reports/deepseek-v1/`：27 次真实模型诊断记录后主动中止，发现输出字段引用错误及周期枚举信息不足；保留原始轨迹，不作为四组正式完整对照。
- `reports/deepseek-v2/`：修复后正式真实模型对照；模型与预算、代码 SHA256、数据 SHA256、轨迹 SHA256 记录在 metadata.json。是否完整以 n 和 interrupted 字段为准。
- `reports/pytest-final-v3.xml`：32 项完整测试原始结果，包含后来补充的 Docker 输出限额与超时测试。

deepseek-v2 运行中的源码固定保存在 `source.zip`，与 metadata 中的 source_sha256 对应。之后交付代码只增加了未参与基准的 Docker Python 输出限额/清理保护，以及独立 embedding 服务地址/密钥配置；基准使用的 code.calculate、hash 记忆、Agent 图和模型提示词未改变。比较时以对应源码快照为准。

逐次原始事件比手写百分比更可靠：`replay` 只读 traces.jsonl，不重调模型、不重跑工具，也不按当前结果“补造”轨迹。它是离线重评分，不是模型的确定性重演；服务端模型更新、温度为零也不保证生成完全一致。

正式后续研究建议至少 3 次独立重复，按任务配对计算差异和 bootstrap 区间，增加不同结构的人工任务及盲评。当前单次合成结果不能泛化为“深度研究全面提升”。验收目标中的收益没有预设百分比。

## 可复制命令

在 `agentforge` 目录执行；新实验必须选择新目录：

```powershell
..\.venv\Scripts\python.exe -m agentforge.cli eval --mode live --tool-mode fixture --workers 8 --output reports/local-live-02
..\.venv\Scripts\python.exe -m agentforge.cli replay reports/local-live-02/traces.jsonl
```

如果要在一次实验结束前停止派发后续任务，可以在输出目录建立空的 `STOP` 文件；当前运行任务结束后退出，metadata 会记录 interrupted=true，不能当作完整对照。整个过程不需要删除数据库或旧实验。
