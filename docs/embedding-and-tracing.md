# 百炼 embedding 与 LangSmith 联调

本机已选择百炼 `text-embedding-v4`，没有下载 AgentForge 本地 embedding 模型。DeepSeek 继续负责生成和规划，百炼只负责向量化，两套凭据分开。

## 百炼配置与实际结果

修改项目私有 `.env`，不要修改 `.env.example` 中的占位示例，更不要把密钥发到对话或截图里：

```dotenv
AF_EMBEDDING_BACKEND=api
AF_EMBEDDING_MODEL=text-embedding-v4
AF_EMBEDDING_DIM=1024
AF_EMBEDDING_API_KEY=填自己的百炼密钥
AF_EMBEDDING_BASE_URL=填控制台提供的OpenAI兼容BaseURL
```

Base URL 末尾不能重复加 `/embeddings`。当前北京地域官方示例使用 `https://业务空间ID.cn-beijing.maas.aliyuncs.com/compatible-mode/v1`；以自己的控制台实际地址与地域为准。

执行位置：`D:\project\agent\agent4interview\agentforge`。

```powershell
..\.venv\Scripts\python.exe scripts/embedding_smoke.py
..\.venv\Scripts\python.exe scripts/embedding_smoke.py --postgres --output reports/embedding-postgres-smoke.json
..\.venv\Scripts\python.exe -m agentforge.cli memory-reindex
```

真实接口返回 1024 维浮点向量。6 条自建中文改写查询在 SQLite 与 PostgreSQL 中均 Top-1 命中 5 条；namespace 隔离均通过。原始输入在脚本 `CASES`，结果在上述两份 JSON。`context` 用例误召回 `safety`，没有修改查询或删除失败样本以提高成绩。这是联通和小样本功能检查，不能当成生产召回率，也没有替代原 60 任务四组 Agent Benchmark。

`Memory._api_embed` 校验维度、有限值、非零范数和批量响应索引；统一归一化使 SQLite 点积与 pgvector 余弦排序一致。服务地址、模型和维度共同生成独立表名，原 `af_memory` 的 256 维 hash 向量保留。`memory-reindex` 每批最多 10 条重新向量化，按原记录 ID 防重，支持失败后重跑；不会覆盖旧表。当前检索读取新表，回退 hash 配置仍可读取旧数据。

本机 337 条历史记忆已全部迁移，原表仍为 337 条；重跑复制 0 条、跳过 337 条。过程中真实遇到连接中断，已完成记录保留，复查外网连通性后续跑完成。`--batch-size 1` 到 `10` 可控制每次请求的文档数。还复现了 psycopg 的 INSERT `rowcount=-1` 导致迁移统计错误，改用 `RETURNING id` 判断是否插入，独立 PostgreSQL 回归通过；数据库 ID 对照验证实际数据无缺失。原始计数与修正说明保留在 `reports/embedding-migration.json`，回归见 `reports/pytest-migration.xml`。

语义向量目前用于长期记忆及 `memory.search`；`rag.search` 的固定文档检索仍是词项基线，不把它描述成已经升级的语义 RAG。旧评测报告使用当时的 hash 配置，没有重跑为百炼结果。

费用核对日期：2026-09-28。北京地域 `text-embedding-v4` 标准输入价为 0.0005 元/千 Token，即 0.5 元/百万 Token。100 万 Token 首次入库加 1 万次、每次 100 Token 的查询，约 1 元；实际按供应商分词和账单计费，不包括 DeepSeek 生成费用。[官方接口与价格](https://help.aliyun.com/zh/model-studio/text-embedding-synchronous-api)

## LangSmith 开关

```dotenv
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=填自己的LangSmith密钥
LANGSMITH_PROJECT=agentforge
LANGSMITH_ENDPOINT=https://api.smith.langchain.com
```

本机已经把 `.env` 中开关改成 true。保存后必须重启原先运行的 Python 进程。若 PowerShell 曾设过 `$env:LANGSMITH_TRACING='false'`，它会优先于 `.env`；在同一个终端执行下面命令移除覆盖，再重启：

```powershell
Remove-Item Env:LANGSMITH_TRACING -ErrorAction SilentlyContinue
..\.venv\Scripts\python.exe -m agentforge.cli doctor
..\.venv\Scripts\python.exe scripts/langsmith_smoke.py
```

`doctor` 中 `langsmith_tracing: true` 只说明开关；`langsmith_smoke.py` 执行真正的 AgentForge 合成任务，上传并从云端读回记录，才验证服务联通。实际读回 47 条记录，其中 5 条工具调用，任务 completed；公开统计见 `reports/langsmith-smoke.json`，私人访问链接仅保存在不提交 Git 的 `data/langsmith-smoke.private.json`。

在 LangSmith 左侧 Tracing 中选择 **agentforge** 项目。截图中的 `Deep Agents / TypeScript` 快速入门不适用于本项目，无需安装 npm 包或配置 Anthropic Key。这里使用已有 Python / LangGraph 的 `@traceable`，开启后会上传任务输入、输出与工具证据。关闭时恢复 `LANGSMITH_TRACING=false` 并重启即可。[官方开关说明](https://docs.langchain.com/langsmith/annotate-code)

如果密钥出现在截图中，应在 LangSmith 控制台撤销旧 Key、生成新 Key，只更新 `.env` 的对应值，再运行 smoke。工程测试请显式关闭追踪，避免将测试样例误上传。

## 源码对应

| 功能 | 实现 | 验证 |
|---|---|---|
| 独立 embedding 服务 | `config.py`, `Memory._api_embed` | API 契约测试、真实中文 smoke |
| pgvector 维度隔离 | `Memory.__init__` | PostgreSQL smoke，新旧表共存 |
| 可恢复记忆重建 | `Memory.reembed`, CLI `memory-reindex` | 重跑跳过、失败恢复、源记录保留测试 |
| 外部错误隐藏敏感响应 | `Memory._api_embed` | HTTP 401 错误不回显响应正文 |
| 云端追踪 | `Runtime.run`, `Registry.execute`, `JsonModel.ask` 的 `traceable` | `scripts/langsmith_smoke.py` 云端读回 |

可选 local 后端代码与 FastEmbed 依赖已准备，但按用户后续决定停止本地模型安装；没有模型下载/推理实测，当前运行配置明确为 api。
