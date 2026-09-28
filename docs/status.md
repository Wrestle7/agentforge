# 实现和验证状态

本文件与原始 reports 一起阅读。实现存在不等于外部服务已经联调；流程 completed 不等于评测成功。

| 项目 | 已实现 | 实际验证 | 范围/限制 |
|---|---|---|---|
| LangGraph 多角色闭环 | 是 | SQLite、PG、真实 DeepSeek | 单机串行 DAG 就绪调度 |
| 依赖校验与 Re-plan | 是 | 环/未知 ID/Retry/Re-plan/Terminate 测试 | 模型仍可能选错能力；严格拒绝后记失败 |
| 统一工具治理 | 是 | JSON Schema、空结果、SQL、算术、故障注入、真实超时 | 429 有界退避，无分布式限流器 |
| RAG | 是 | 固定合成文档检索 | 默认词项检索，无重排模型；可配置本地 corpus |
| Web Search | Tavily 接口已实现 | 合成搜索和本地降级已跑 | 密钥已配置；真实请求尚未在本次验证 |
| SQL | 是 | 实际只读 SQLite 查询 | 演示业务数据 SQLite；运行审计存 PostgreSQL |
| Code Execution | 是 | AST 算术和真正 Docker Python 均通过 | Docker 要求主机引擎；非多租户安全产品 |
| MCP Gateway | 是 | 真正 stdio initialize/list/call | 远程 HTTP 连接器、第三方服务待联调 |
| PostgreSQL/pgvector | 是 | 真正容器服务、向量列与距离查询 | 小数据精确检索，未测大规模吞吐 |
| Pause/Resume | 是 | 两个独立 CLI 进程从 2 次恢复到 5 次调用 | 不跨数据库迁移，不保证节点外副作用 exactly-once |
| 长期记忆 | 是 | 跨会话、namespace 隔离、No-Memory | 百炼 text-embedding-v4 / 1024 维，SQLite 与 PG 中文小样本均 5/6 Top-1 |
| DeepSeek | 是 | HTTP 200、完整链路及批量评测 | 请求旧别名，响应 deepseek-flash；非思考模式 |
| LangSmith | 是 | 实际云端上传并读回 47 条记录 / 5 个工具调用 | 当前项目 agentforge；合成任务 smoke |
| Eval Harness | 是 | 60 × 4 fixture；回放精确一致 | 成功和工具选择为已说明的代理口径 |
| 真实模型正式对照 | 是 | 60 任务 × 4 配置，共 240 次；精确回放与 SHA256 校验通过 | reports/deepseek-v2/REPORT.md |
| Docker 应用 | 是 | 构建并运行到 completed | 应用容器默认不挂宿主 Docker socket |
| 测试 | 是 | 本次全套 39 通过 / 4 跳过；随后记忆与新增 PG 迁移回归 12 项通过 | reports/pytest-semantic.xml、reports/pytest-migration.xml；部分测试重叠，不相加 |

## 交付文件

- 全部应用代码：`src/agentforge/`。
- 依赖：`pyproject.toml`、`requirements.lock`，包含 Windows pywin32 平台标记。
- 示例配置：`.env.example`、`configs/`、`compose.yaml`。真实 `.env` 不分发。
- 示例和评测输入：`fixtures/`；数据源全部标注 synthetic / fixture。
- 命令与源码对应：`README.md`、`docs/implementation.md`。
- 技术解释：`docs/walkthrough.md`。
- 故障定位与修复：`docs/issues.md`。
- 实测原始输出：`reports/`，其中 deepseek-v1 是中止的诊断批次，不能当作完整基准。

## 尚未完成或未验证

正式真实模型结果：Full / ReAct 的成功率代理均为 48.3%；平均无效调用为 1.25 / 4.18，平均轮数为 7.53 / 7.75，但平均 tokens 为 41,785 / 11,874。没有达到同时降低无效调用、减少轮数并控制 token 成本的联合目标。No-Critic 成功率代理为 63.3%，这说明当前 Critic 并未在本数据上带来预期收益，值得继续研究。不要把机制实现和测试通过写成性能目标已实现。

语义 embedding 服务与 LangSmith 云端已完成真实联调，见 embedding-and-tracing.md。Tavily 密钥已填但本次尚未执行服务 smoke；第三方 HTTP MCP 仍待联调。性能目标必须以 deepseek-v2 的完整四组结果判断，不能因工程测试通过就宣称提高任务成功率或降低成本。

尚未开发的生产能力包括同 thread 并发锁、严格全局 deadline、任意外部写操作幂等、检查点保留清理、数据库 schema 升级迁移、租户认证和服务化部署。它们不是这版可运行演示已实现的能力。

DevPilot MCP 已在相邻独立目录交付；MCP SDK、Codex、Claude Code 实测及用户完成的 Cursor 联调见该项目 README。
