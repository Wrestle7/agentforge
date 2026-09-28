# 实施中的问题与处理

| 复现问题 | 原因定位 | 处理与验证 |
|---|---|---|
| 默认 python 为 3.9，缺少当前依赖支持 | `python --version` 与 `py -0p` 指向不同解释器 | 显式用 `py -3.13` 建工作区 venv；所有命令写出解释器路径 |
| Docker CLI 存在但 daemon 未启动 | docker info 找不到管道 | 启动已安装的 Docker Desktop；验证 ServerVersion；未重新安装系统组件 |
| pip editable 安装长时间无输出 | 默认缓存/临时目录在受限进程中出现访问问题 | 使用工作区 data/tmp 和 pip-cache；安装 setuptools/wheel 后 `--no-build-isolation --no-deps -e .` 成功 |
| ruff/pytest 写临时文件被拒绝 | 工具沙箱对部分生成目录的访问限制 | ruff 使用 --no-cache；测试在授权的正常本地进程运行，basetemp 固定为项目 data 下，报告保留 |
| 初次 MCP 子进程找不到 agentforge | SDK 子进程环境没有开发期 PYTHONPATH | 内置 demo 用 sample_mcp.py 绝对路径，安装包后外部配置也可 `-m` 启动 |
| MCP structuredContent 多一层 result | 服务返回裸 dict 时 SDK 输出包装与预期不同 | 返回显式 Pydantic Policy，客户端真实协议测试通过 |
| PostgreSQL 容器 healthy，主机仍超时 | inspect 显示 HostConfig 有端口但 NetworkSettings.Ports 为空；原端口附近存在宿主连接 | 保留卷，重建本项目容器改绑 15432；主机连接、checkpoint 和 vector 检索通过。确切 Docker/Windows 端口冲突机制未进一步证明 |
| 数据库不可达时等待过久 | 连接未指定 connect_timeout | SQLAlchemy 与 PostgresSaver 均设置 5 秒连接超时 |
| 用户把真实 key 填在 .env.example | 模板会随代码分发 | 将值移至忽略的 .env，清空模板；仅输出 SET/EMPTY；分发文件扫描通过 |
| 旧 DeepSeek 模型标识与返回标识不同 | 官方兼容别名映射，真实响应 model 为 deepseek-flash | 保留用户请求名称，同时记录 requested_model/served_model，关闭 thinking 做可复现的初轮成本评测 |
| 模型把 code 输出引用为 rows.0.value/result | 工具输出结构未说明；引用解析在 Executor 选工具前发生 | 补输出描述，捕获错误并给出实际 dependency_results 纠正；回归测试验证错误引用可恢复 |
| 模型查询 baseline 但数据周期是 previous | 工具 Schema 描述缺少有效枚举 | 补 previous/current 说明，要求查询两期时返回 period，禁止按行位置猜时间顺序 |
| 真实任务 completed 但数值与工具证据不一致 | Synthesizer 可能给出正确/错误的心算答案，掩盖错误工具计算 | 评分同时检查计算工具结果与最终文本，保留失败；该严格口径的局限写入 evaluation.md |
| 一次 run_id 被重用 | 会覆盖已有检查点、污染回放 | run 前拒绝重复 ID，完成任务 resume 幂等返回 |
| Docker 子进程可能持续输出 | communicate 会在宿主收集整个输出，容器内存限制不能限制宿主缓冲 | 改成每个流最多 16 KiB 的增量读取，超限/8 秒超时后移除容器；补真实 Docker 失败测试 |

代码已处理的故障包括：真实异步超时取消、可注入 timeout/rate_limit、空结果、错误 SQL、格式不正确的 JSON 模型响应、未知工具、错误参数、越界表达式、循环依赖、预算耗尽。Tavily 服务真实 429、远程 MCP 网络故障、embedding 服务和 LangSmith 云端尚未做线上故障演练。

保留的工程边界：当前能力不匹配的工具选择会直接失败关闭，而不是自动改变权限/能力；跨进程竞争同一 run_id 没有分布式锁；SQL 审计与图检查点之间不是单一事务。外部写操作不在 AgentForge 这版范围，不能套用当前恢复逻辑后宣称无重复副作用。
# 语义记忆与云端追踪补充

- 百炼 text-embedding-v4 已实际联通，SQLite / pgvector 返回一致的 5/6 中文小样本 Top-1，保留失败样本。
- 老表为 vector(256)，新模型为 1024 维；按服务/模型/维度创建独立表，保留原表并重建 337 条记忆。
- 真实连接中断后按记录 ID 续跑成功；没有清空数据库或从头重复全部 API 调用。
- PostgreSQL 驱动曾返回 INSERT rowcount=-1，影响统计；改用 RETURNING，并通过真实 PostgreSQL 回归，迁移结果以数据库 ID 对照核验。
- LangSmith `.env` 开关原为 false；改为 true，运行示例并从云端读回 47 条记录与 5 条工具调用。设置开关和真实云端联通分别验证。
