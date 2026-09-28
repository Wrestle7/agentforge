# 按源码讲解项目

这份讲解区分“选用技术提供的能力”和“我们写出的应用逻辑”。面试时可以打开对应函数，再运行示例验证，避免只讲框架名。

## 1. LangGraph StateGraph：执行状态机

StateGraph 把状态、节点和边组织成可执行图。`runtime.py:_build_graph` 注册 Recall、Planner、Scheduler、Researcher、Executor、Critic、Replanner、Synthesizer、Finish；条件边读取状态中的 `next`。各角色使用同一模型配置但不同职责提示词和输入；它们不是训练过的不同模型，也不是多个操作系统进程。

选择它的原因是图状态持久化和中断恢复可以复用成熟接口。手写 while-loop 同样能执行，但要自行设计每个边界的 checkpoint、resume 和错误恢复；Celery/Temporal 更适合任务队列和分布式编排，本项目暂时不需要这些部署成本。

实施难点是同时存在两种图：任务 DAG 和控制流图。前者描述工作依赖，后者允许循环。我们让控制流反复进入 Scheduler，而不是把任务 DAG 的每个 step 动态注册为 LangGraph 节点。这样 Re-plan 只改数据，不必重新编译执行图。

## 2. DAG：控制任务依赖

`schema.py:Plan.valid_dag` 拒绝重复 ID、自依赖、未知依赖和环。`runtime.py:_scheduler` 只选择依赖集合包含于 done 集合的步骤。比如先读 previous/current 两组数据，计算步骤依赖二者，敏感性步骤再依赖计算。

执行完工具不等于任务完成：Full 配置还要经过 Critic 接受，才进入 done。当前就绪步骤顺序执行，拓扑正确不代表已经实现并发调度。替代方式包括拓扑层批量执行、Dask、任务队列；若采用并发，还要加入预算预留、结果合并和同一步骤的分布式锁。

## 3. Critic：证据驱动的控制

`_critic` 读取目标、当前步骤、工具结果摘要、依赖原始结果和失败反思，输出 Continue/Retry/Re-plan/Terminate。Continue 需要有成功结果；Retry 达到每步上限后转 Re-plan；Re-plan 有独立次数上限，并且不能改掉已经完成的步骤。

真实调试中，模型把计算返回值误引用为 `forecast.rows.0.value`，实际是 `forecast.value`。最初这个 KeyError 在 Critic 前直接中断。现在 Executor 捕获引用错误，提供实际 dependency_results 让模型修正，且记录 reference_error。工具说明也补了输入与输出样式。回归测试直接复现错误字段，验证修复后的数值为 27。

可以用确定性规则替代 Critic，成本更低；也可用独立 verifier 模型或执行后断言。这里保留真实模型 Critic 和严格的工程边界，方便研究其额外开销与收益，而不预设其一定更好。fixture 模式的 Critic 只做规则判断，不能当成真实模型评测。

## 4. Tool Harness：协议与策略集中管理

`tools.py:Tool` 统一名称、能力、输入 JSON Schema、输出描述、超时、重试、Fallback 和权限。`Registry.execute` 验参数，运行工具，将超时、429、空结果、服务错误标准化为可观察事件，再根据策略重试或降级。实际尝试次数计入预算，失败调用也算。

选择集中封装是为了防止每个 Agent 各写一套错误处理。框架自带 ToolNode 很适合调用 LangChain Tools，但还需补充本项目的预算、输出限制、错误分类和审计策略。当前没有全局分布式 Rate Limiter；429 只有有界退避重试，属于原型范围。

SQL 不依赖关键词黑名单：只读 SQLite URI、query_only、authorizer、执行时限、100 行上限共同约束。基础 Code 工具用 AST 解释有限算术语法，拒绝属性、函数调用、Import 和任意幂运算，不调用 eval。真实 Python 工具才使用 Docker；断网、非 root、资源限制是隔离措施，不把容器等同于数学上不可突破的沙箱。

## 5. MCP：标准工具接口

MCP 是客户端和工具服务之间的协议。`mcp_gateway.py` 使用官方 SDK 管理初始化、工具发现和调用，将允许的远程工具转换成统一 Registry 条目。服务端 `sample_mcp.py` 通过 FastMCP 暴露结构化 Policy。

实际遇到两个问题：SDK 的子进程环境没有自动保留开发期 PYTHONPATH，因此改用样例服务的绝对脚本路径；未明确声明返回类型时 structuredContent 的包装结构与预期不同，因此改成 Pydantic Policy 输出。真实 SDK 测试覆盖了进程启动、list_tools 和 call_tool。

替代方案是直接 REST API 或应用内函数。MCP 的价值在于统一发现/Schema/调用和跨客户端复用，而不是自动带来权限安全。当前 Gateway 白名单只读工具；不支持在模型输入中动态指定任意子进程命令。第三方远程 MCP 和具体 Coding Client 联调不是本项目当前已验证内容。

## 6. Working State → Checkpoint → Memory

Working State 保存计划、done、有限反思、Evidence ID 和摘要；原始 Evidence 放在 SQL。`pack_context` 按字节预算选择最近摘要，避免把全部轨迹不断拼进 prompt。字节估算不是模型 tokenizer；当前仍保留完整计划及关键字段，大型计划可能被模型预算拒绝，未实现通用最优上下文压缩算法。

Checkpoint 保存图运行位置和状态，使用 thread_id 定位。`interrupt` 在调度边界暂停；恢复使用 `Command(resume=True)` 或中断异常后的 `invoke(None)`。成功完成的图再次恢复不重新调用工具。当前外部工具都是读取，异常边界是 at-least-once；若扩展写操作，必须新增业务幂等键和事务 outbox，不能仅凭 checkpoint 宣称 exactly-once。

Long-term Memory 独立于 thread，按 namespace 隔离，用于跨会话回忆。当前完成报告摘要写入记忆，下一任务先召回，或通过 memory.search 查询。Memory 和 Checkpoint 不是同一件事：前者取相似的历史内容，后者恢复精确执行位置。

替代方案包括 Redis 临时状态、对象存储轨迹、专用向量数据库。这里使用 PostgreSQL 统一事务数据与向量，减少部署组件；SQLite 用于没有服务依赖的本地演示。

## 7. PostgreSQL / pgvector / RAG

PostgreSQL 保存审计和运行数据；pgvector 让数据库支持向量列和距离查询。`Memory.search` 的 PG 路径直接用 cosine_distance 排序，而 SQLite 开发路径在 Python 中计算点积。当前数据量小，使用精确检索，没有声称已调优 HNSW 索引或做大规模吞吐测试。

默认 embedding 是稳定的词项哈希向量，验证存储与召回机制；它没有语言模型语义理解能力。API embedding 路径能调用兼容 `/embeddings` 的服务，但本次 DeepSeek chat 配置并不等于已有 embedding 服务。不同 embedding_id 不混合检索，维度变更需迁移。

RAG 是先检索资料再据资料生成的链路。当前本地语料检索使用词项重叠排序，返回带来源的段落，进入模型 Evidence；长期记忆另用向量召回。BM25、稠密向量与重排模型可以替换本地检索器。我们先选择简单可解释的本地检索，使评测数据可固定，并明确没有把词项检索宣传成高质量语义检索。

## 8. Token、时间和失败预算

`JsonModel.ask` 每次调用前用 UTF-8 字节数进行保守输入预留，加上最大输出额度；响应后使用服务端 usage 记录真正的 input/output tokens。无 usage 或网络中断时标记 estimated，不伪装成精确账单。错误 JSON 也会消耗 tokens，必须先计量再尝试修复。

工具调用预算包含每次 Retry/Fallback。轮数定义为执行决策次数，图节点数单列为 trajectory_length；它们不能混称。活动时间在节点之间检查，当前 in-flight 请求有自己的超时，所以不是严格到毫秒的全局 deadline；暂停等待不计入活动预算。

可替代方案是模型专用 tokenizer、中心预算服务、每次工具调用前预留成本。当前实现方便解释和保守限额；对未知第三方模型不能声称严格等于供应商账单。

## 9. Evaluation / Ablation

`fixtures.py` 给出 10 种多步分析模板和 6 个领域，共 60 个合成任务，包含依赖计算与检索核验。`evaluation.py` 给所有配置使用相同任务、相同模型参数、相同工具层、相同最大预算，逐次保存结果。

Single ReAct 只有一个持续选择下一工具的 Agent，没有 Planner 和 Critic；仍保留相同工具治理、记忆与最终汇总，以尽量隔离组织方式影响。No-Critic 保留 DAG，但成功 transport 直接标记完成。No-Memory 去掉长期记忆读写和 memory.search，不去掉工作状态或 Checkpoint。

本基准采用冷记忆命名空间，防止先运行的任务把答案泄漏给后运行的任务。因此这组数据不能验证跨会话记忆收益。真正研究长期记忆，需要先定义独立的训练/记忆写入集，再评估未见过但有关联的测试集；不能直接用同题先跑一次再当作新任务提分。

## 10. LangSmith 与本地可观测性

LangSmith 提供跨节点 Trace 可视化。应用使用 traceable 装饰任务、模型、工具，并使用 LangGraph 原生 tracing。它有助于看见失败在哪一角色、哪次调用、耗时与 token 使用；本地 SQL audit 是不依赖云服务的事实来源。

替代方案包括 OpenTelemetry、Langfuse、自建事件分析。当前已完成 LangSmith 云端上传并通过 API 读回 47 条运行记录，详见 reports/langsmith-smoke.json；这与仅实现装饰器、仅开启配置是不同的验证层次。

建议面试演示顺序：运行 5 步 demo → 查看 plan 和 tool_call 事件 → 暂停后新进程恢复 → 演示 MCP → 打开故障回归测试 → 对照真实评测原始轨迹解释成本与失败，而不是仅展示框架调用代码。
