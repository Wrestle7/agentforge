# AgentForge

可运行、可检查源码的 LangGraph Agent Harness。运行目标：把一个研究/分析请求拆成 DAG，调用工具取得证据，让 Critic 决定继续、重试、重规划或终止，最后输出报告并保存轨迹。

本项目从零编写应用层；使用 LangGraph、MCP Python SDK、SQLAlchemy、psycopg、pgvector 和 LangSmith 库，没有复制候选项目的应用源码。[选型依据](docs/implementation.md) · [技术讲解](docs/walkthrough.md) · [评测口径](docs/evaluation.md) · [验证状态](docs/status.md)

本次补充后的工程测试：39 通过、4 跳过；此前 60 × 4 离线脚本运行和 60 × 4 真实 DeepSeek 运行均已完成。[真实模型结果](reports/deepseek-v2/REPORT.md)显示 Full 与 ReAct 成功率代理均为 48.3%，无效调用与轮数有下降，但 tokens 增至约 3.52 倍，联合成本目标未达到。数据为合成多步任务，原基准使用 hash 记忆，不代表当前百炼配置或真实互联网研究效果。

## 一次运行

从独立仓库安装（Python 3.11+，此次验证版本 3.13.5）。在 PowerShell 中：

```powershell
git clone https://github.com/Wrestle7/agentforge.git
cd agentforge
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe -m pip install --no-build-isolation --no-deps -e .
if (!(Test-Path .env)) { Copy-Item .env.example .env }
.\.venv\Scripts\python.exe -m agentforge.cli init
.\.venv\Scripts\python.exe -m agentforge.cli doctor
.\.venv\Scripts\python.exe -m agentforge.cli run
```

输出 `status: completed`、5 次工具调用、增长率 `30.0` 和带证据 ID 的示例报告。默认使用 SQLite 和合成数据，无需模型密钥。`fixture://` 不代表真实网页；真实服务需自行配置 `.env`。

Linux/macOS 将 `.\.venv\Scripts\python.exe` 换成 `.venv/bin/python`，创建环境使用 `python3 -m venv .venv`。原双项目开发工作区使用父目录共享 Python 环境，可以继续把下文 Python 路径替换为 `..\.venv\Scripts\python.exe`，无需移动原环境。

## PostgreSQL / pgvector

```powershell
docker compose up -d --wait postgres
```

在 `.env` 配置：

```dotenv
AF_DATABASE_URL=postgresql://agentforge:agentforge_dev@127.0.0.1:15432/agentforge
```

应用首次启动创建 `af_runs`、`af_events`、`af_evidence`、`af_memory`，并由 `PostgresSaver.setup()` 创建图检查点表。`af_events.kind` 区分 plan、critique、tool_call、model_call、node 等，便于回放。`pgvector` 扩展保存并检索记忆向量。开发密码只用于绑定本机的演示容器；外部部署用 `AF_POSTGRES_PASSWORD` 覆盖，并更新数据库 URL。

## 暂停和恢复

```powershell
.\.venv\Scripts\python.exe -m agentforge.cli run --run-id pause-demo-01 --pause-after 2
# 退出终端也不会丢失检查点；在新终端、同一项目目录和数据库中恢复：
.\.venv\Scripts\python.exe -m agentforge.cli resume pause-demo-01
.\.venv\Scripts\python.exe -m agentforge.cli inspect pause-demo-01 --events
```

第一次输出 `paused`，已执行 2 次调用。恢复后累计 5 次；再次恢复完成任务不重复执行。运行 ID 已存在时不能重新覆盖，换一个 ID 或使用 `resume`。暂停点在两次 DAG 调度之间，当前工具调用不会被中途强杀。异常中断可以从最后持久化检查点恢复；节点内的 read 工具可能至少执行一次，不能声称任意外部写操作的 exactly-once。

## MCP 演示

```powershell
.\.venv\Scripts\python.exe -m agentforge.cli run --case fixtures/mcp-demo.json --with-mcp
```

该命令实际启动 `sample_mcp.py` 子进程，通过 MCP SDK initialize → list_tools → call_tool 调用 `policy_lookup`，总计 6 步。返回的是明确标注的合成部署政策。接自己的服务时：

```powershell
.\.venv\Scripts\python.exe -m agentforge.cli run "研究目标" --mode live --mcp-config configs/mcp.example.json
```

配置 `allow_tools` 是应用管理员的显式白名单。所有当前工具都是 Read 权限；服务端声称只读不等于可信保证，请只接可信服务。Streamable HTTP 连接器已实现，第三方远程服务尚未联调。

`configs/mcp.example.json` 使用独立仓库的 Windows `.venv`；其他环境将 command 改成安装了本包的 Python 绝对路径。

## DeepSeek / 真实模型

```dotenv
AF_LLM_BASE_URL=https://api.deepseek.com
AF_LLM_MODEL=deepseek-v4-flash
AF_LLM_API_KEY=填入自己的密钥
AF_LLM_THINKING=disabled
AF_MAX_TOKENS=120000
AF_MAX_MODEL_OUTPUT=4000
AF_MAX_SECONDS=600
```

```powershell
.\.venv\Scripts\python.exe -m agentforge.cli run "For the synthetic retail business, read both periods, compute percentage growth, retrieve market and policy evidence, and cite the result." --mode live --tool-mode fixture
```

这是真实模型 + 固定合成工具数据：Planner、执行选择、Critic、Synthesizer 都调用真实 API，不能称为真实互联网研究评测。若不用 `--tool-mode fixture`，Web Search 使用 Tavily，需要 `AF_TAVILY_API_KEY`；缺配置时有明确错误记录，并可降级到本地 RAG。需要不同资料时设置 `AF_CORPUS_PATH` 为 JSON 数组文件，每条含 `id`、`url`、`text`；SQL 示例数据是 `data/analytics.sqlite` 的 `metrics(domain,period,value,cost)` 表。

官方目前把旧 `deepseek-v4-flash` 名称路由到 `deepseek-flash`；实测响应也返回后者。调用日志同时保存 requested_model 和 served_model。[DeepSeek 官方说明](https://api-docs.deepseek.com/guides/agent_integrations/openclaw)

## 记忆与追踪

`--namespace alice` 将长期记忆隔离在 alice 的空间；不同命名空间不互相检索。示例默认 `AF_EMBEDDING_BACKEND=hash` 是词项哈希向量。当前本机已配置 `api`，实际使用独立百炼服务 `text-embedding-v4` / 1024 维；生成仍用 DeepSeek。`AF_EMBEDDING_BASE_URL` 和 `AF_EMBEDDING_API_KEY` 配置独立地址及密钥。模型、服务或维度改变时自动使用新向量表；运行 `memory-reindex` 复制并重新向量化旧记忆，原表保留。

一旦显式填写独立 embedding 地址，就只使用该服务的独立密钥，不把 DeepSeek 聊天密钥自动发给另一个服务；本地免密钥向量服务可留空。

LangSmith 使用 `LANGSMITH_TRACING=true`、`LANGSMITH_API_KEY`、`LANGSMITH_PROJECT` 开启。任务、图节点、模型和工具调用会进入 Trace；可以按失败节点排查。示例默认关闭；当前本机已开启，并从云端读回示例任务的 47 条记录和 5 条工具调用。项目为 `agentforge`，本地 SQL 审计不依赖云端。

## 测试、评测与回放

```powershell
.\.venv\Scripts\python.exe -m pytest -q
# 包含本地 PostgreSQL 和 Docker 集成测试：
$env:AF_TEST_POSTGRES_URL='postgresql://agentforge:agentforge_dev@127.0.0.1:15432/agentforge'
$env:AF_TEST_DOCKER='1'
.\.venv\Scripts\python.exe -m pytest -q
# 60 个合成任务 × 4 配置；每次使用新输出目录，防止覆盖旧实验：
.\.venv\Scripts\python.exe -m agentforge.cli eval --output reports/local-fixture-01
# 真实模型使用相同固定工具数据，4 个并发任务，各任务独立命名空间：
.\.venv\Scripts\python.exe -m agentforge.cli eval --mode live --tool-mode fixture --workers 4 --output reports/local-live-01
.\.venv\Scripts\python.exe -m agentforge.cli replay reports/local-live-01/traces.jsonl
```

每组产生逐任务 `metrics.json`、聚合 `summary.json`、输入/轨迹哈希 `metadata.json`、可离线重评分的 `traces.jsonl`。所有配置共享任务、工具 Schema、工具故障策略和预算。`full`、`react`、`no_critic`、`no_memory` 的差异见评测说明。

完成后可用 `..\.venv\Scripts\python.exe scripts/report_results.py reports/local-live-01` 生成 Markdown 对照报告和按任务模板聚类的 bootstrap 配对差异；仅完整、平衡的实验可以生成正式对照。

## Docker 应用与代码执行

```powershell
docker compose --profile app build app
docker compose --profile app run --rm app run
docker pull python:3.13-slim
```

默认 `code.calculate` 是有界 AST 算术解释器，不执行任意 Python。`--docker-code` 才注册 `code.python`：容器断网、根文件系统只读、非 root、无宿主目录挂载，并限制内存/CPU/PID/时间。需要主机 Docker 引擎；应用容器默认未挂载 Docker socket，因此应用容器内不提供 `code.python`。容器不是针对敌对多租户代码的完整安全产品，当前定位为开发演示隔离。

## 交付范围

实现源码、锁定依赖、60 个合成任务、示例 MCP 服务、数据库和 Docker 配置、测试和评测脚本均在本目录。真实搜索 API、语义 embedding API 和 LangSmith 云端联调需要相应配置，状态逐项列在 [status.md](docs/status.md)。DevPilot MCP 按约定独立交付，未混入本项目。

本机后续已经接通百炼 `text-embedding-v4` 和 LangSmith 云端。密钥填写位置、验证命令、旧记忆迁移与实测结果见 [embedding 与 tracing](docs/embedding-and-tracing.md)。
