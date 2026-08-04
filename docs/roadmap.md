# 实施路线图

采用纵向切片交付，每个阶段都产生可运行、可演示的能力。

## Phase 0：工程基线（已完成）

- Python 包、FastAPI、配置、日志、数据库迁移和测试框架；
- Docker Compose 启动 PostgreSQL/Qdrant；
- CI 执行 lint、type check、unit test；
- fake model provider 支持离线测试。

验收：`docker compose up` 后 `/health/live` 和 `/health/ready` 正常，测试可重复运行。

完成记录（2026-07-20）：代码、锁文件、全部本地质量门禁和 Docker Compose 均已验证。镜像构建成功，API、PostgreSQL、Qdrant 全部达到 healthy；Alembic 基线迁移、liveness 和 readiness 端到端检查通过。

## Phase 1：仓库知识库（已完成）

- 本地/Git 导入、文件发现和安全过滤；
- Python/Rust/JS/TS Tree-sitter 解析；
- 统一符号模型与结构化 Chunk；
- PostgreSQL 元数据、Qdrant 向量、BM25 索引；
- 异步索引任务和进度 API。

验收：导入 fixture 仓库后，可按语义、符号、路径找到目标代码；引用行号与源码一致。

完成记录（2026-07-20）：本地与公开 Git 导入、四语言 Tree-sitter 路由、符号/关系提取、AST-aware Chunk、PostgreSQL/Qdrant/BM25 双索引、独立 Worker、进度 API、版本原子激活及同 commit 幂等复用均已实现。24 项自动化测试通过；容器内分别完成本地 fixture 和 GitHub `pypa/sampleproject` 端到端导入，且空数据库完整升级与 Alembic 模型漂移检查通过。真正的 Hybrid Fusion 与面向用户的 Search API 按边界进入 Phase 2。

## Phase 2：Hybrid RAG（已完成）

- Query Analyzer；
- Dense/BM25/Symbol 三路召回与 RRF；
- 可插拔 Reranker、去重、上下文打包；
- 带引用问答与引用校验；
- 首版 retrieval benchmark。

验收：标注集 Recall@10 ≥ 0.80，Reranker 故障时自动降级，回答无无效引用。

完成记录（2026-07-20）：Query Analyzer、Dense/BM25/Symbol 三路并行召回、RRF、Chunk 去重、单路径多样性、可插拔启发式 Reranker 与超时/异常降级、Context Packer、Evidence 契约、Search API 和 JSONL benchmark runner 已实现。fixture 容器基准 Recall@5/10 均为 1.00、MRR 0.611、nDCG@10 0.710、平均延迟约 54 ms；33 项自动化测试通过，并覆盖 Phase 1 BM25 索引兼容。回答生成与引用校验在 Phase 3 的 grounded answer workflow 中完成。

## Phase 3：Agent Workflow（已完成）

- Run/Step 持久化和预算控制；
- `WorkflowRuntime` 端口与 LangGraph `StateGraph` 默认适配器；
- PostgreSQL Checkpointer、`run_id/thread_id` 映射和 crash-resume；
- 六个只读代码工具；
- `answer_question`、`explain_module`、`trace_symbol`；
- 将 LangGraph stream 投影为稳定的 SSE 事件，支持取消和错误恢复；
- Prompt Injection 防护测试。

验收：基准复杂任务完成率 ≥ 0.80；每一步可追踪，循环受预算约束；Worker 在中间节点退出后可从 PostgreSQL checkpoint 恢复且不重复已提交副作用。

完成记录（2026-07-20）：引入 LangGraph 1.2.9 与 PostgreSQL Checkpointer 3.1.0，并保持 domain/application 无 LangGraph import。Run/Step/Event 审计、六个只读工具、三类预置工作流、Grounded Answer、引用白名单校验、预算、取消、异步 Run API 和可续传 SSE 已实现。5 项容器 Agent 基准任务完成率、引用有效率、期望路径召回均为 1.00，平均完成时间约 218 ms。真实 crash-resume 测试在 retrieve 副作用完成但 checkpoint 尚未提交时强杀 API；重启后同一 Run 自动完成，16 个事件键无重复、每个 Step 仅一条、thread 下保留 8 个 checkpoints。

## Phase 4：Memory 与增量索引（已完成）

- Working/Session/Project/Episodic Memory；
- 项目与模块摘要；
- 文件/Chunk 增量更新和 Memory 失效；
- 索引版本原子切换与旧版本回收。

验收：修改一个文件后只处理受影响内容，旧分析不被错误复用，Run 始终绑定单一版本。

完成记录（2026-07-20）：实现 Working/Session/Project/Module/Episodic 分层 Memory，Working Memory 压缩保留全部 Evidence ID，Session 按 `session_id` 隔离，Project/Module 摘要在版本激活前生成，成功 Run 幂等沉淀 Episodic 与 Session Memory。跨版本时按照 `source_paths/content_hashes` 重基：未受影响分析复制到新版本，受影响分析标记 stale 且不会召回。索引 Worker 基于文件哈希只解析与 Embedding 新增/修改文件，未变 PostgreSQL 图与 Qdrant 向量跨版本复制，BM25 原子生成；active version 最后切换，并按高精度激活时间保留最近 3 个及所有 Run 引用版本。容器实测单文件修改结果为 1 changed、2 reused、2 chunks embedded、3 chunks reused；新版本 Hybrid Search、Memory API 和 Agent recall/persist 事件均通过。43 项测试、严格类型、架构契约、迁移漂移检查通过。

## Phase 5：作品化与发布（已完成）

- 完善 Compose、健康检查、数据卷和示例配置；
- Demo CLI 或极简 Web UI；
- 架构图、演示仓库、性能/评测报告；
- README 快速开始、故障排查与 3 分钟演示脚本。

验收：新环境 10 分钟内启动并完成“导入 → 提问 → 查看引用”的完整演示。

完成记录（2026-07-20）：交付零依赖 `codemind-demo` CLI、同源 `/demo` Web UI、Compose `demo` profile、Python 与四语言 showcase 仓库、发布评测集、生产 Compose override、容器安全加固、架构复盘、性能报告、故障排查和 3 分钟演示脚本。独立 Compose project 使用全新 PostgreSQL/Qdrant/BM25/工作区数据卷冷启动至全服务 healthy 用时 13 秒，CLI 约 5 秒完成导入到引用回答。Python 检索 Recall@5/10=1.00、P95≈67.6ms；5 项 Agent 完成率、引用有效率、期望路径召回均为 1.00，平均≈372ms；Python/Rust/TypeScript/JavaScript 五项检索 Recall@5/10、MRR、nDCG 均为 1.00，P95≈56.0ms。50 项自动化测试、严格类型、架构契约、空库迁移、镜像非 root/只读根文件系统和完整 Docker E2E 均通过。

## 后续迭代路线

以下阶段基于 2026-07-21 的[项目审查](project-review.md)。版本号是建议，不是发布日期承诺；每个阶段应以评测和容量证据决定是否进入下一阶段。

### Phase 6：可靠性与安全基线（建议 0.1.x）

- API 认证/授权、可信代理配置、请求体与速率限制；
- 仓库大小/文件数、并发 Job/Run 和租户资源配额；
- Index Job 独立租约心跳、取消与长步骤幂等提交；
- metrics、队列深度、Provider 指标、告警和运行手册；
- 仓库级联删除、备份编排与隔离环境恢复演练；
- CI 加入覆盖率、迁移、容器 smoke、依赖漏洞和 SBOM 门禁。

验收：在可信预生产环境连续运行，Worker/API 重启和 Provider 超时不产生重复激活或孤儿任务；未授权访问与超额请求被拒绝；关键故障能被指标和告警定位；备份恢复与仓库删除均可演练。

### Phase 7：生产模型与质量闭环（建议 0.2）

- 至少一个真实 Embedding、Reranker 和 LLM Provider；
- 统一超时、重试、并发、费用、模型版本和回退配置；
- 扩充真实仓库评测集，覆盖同名符号、大文件、语法错误和多语言关系；
- 使用固定 commit SHA 建立可复现真实仓库集，引入 grep/read-top-k Agent baseline、多跳检索和逐任务错误记录；
- 为影响面分析准备 graph-derived、co-change 与人工标注三类真值，避免使用同一张图循环验证自身；
- 区分全仓库、Agent、变更文件等 Token baseline，并使用目标 tokenizer 校准正式报告；
- 建立质量/延迟/费用基线和发布回归对比；
- 缓存与批处理优化，校准 Chunk、召回和上下文预算。

验收：在冻结评测集上达到既定 Recall、引用有效率和任务完成率，Provider 故障可降级，单次 Run 成本和 P95 延迟可观测且受预算约束。

阶段进展（2026-07-21）：已完成零 API 费用的 Ollama Embedding/LLM Provider、超时与模板回退、独立向量 collection、模型切换完整重建保护和首轮真实模型回归。Python fixture 上 Retrieval Recall@10=1.00，Agent 完成率/引用有效率=1.00、期望路径召回=0.90。模型 Reranker、真实开源仓库扩容、并发与长上下文基准、统一重试/指标仍属于本阶段后续工作。

### Phase 8：开发者工作流（建议 0.3）

- Git history、diff 与 blame 检索；
- diff 行范围到符号的映射，以及支持方向、边类型、深度、路径、置信度和截断状态的有界图遍历；
- 影响面分析、候选测试、透明风险因子、变更方案和架构漂移检测；
- 按真实需求逐步增加入口 Flow 与代码 Community 派生视图，不将其作为代码事实源；
- 公开 Tree/Symbol/Relation 分页 API；
- 按语言接入 LSP/编译器符号适配器，Tree-sitter 保持降级路径；
- LSP 或 VS Code 客户端，支持引用跳转和 Run 观察。

验收：对真实变更任务给出可验证的依赖路径、历史依据、风险和测试建议；编辑器端能够完成导入、查询、跳转和流式进度查看。

设计和评测细节见 [`code-review-graph` 借鉴分析与实施映射](reference-code-review-graph.md)。该参考不改变 Phase 6 优先级，也不授权自动修改代码。

### Phase 9：平台化与规模扩展（建议 1.0）

- 多租户隔离、RBAC、私有 Git 凭证和审计；
- 分布式 Worker、弹性队列和托管向量/词法索引；
- 配额计费、数据保留策略和租户级 SLO；
- 高可用部署、灾难恢复和滚动升级。

验收：只有当 Phase 6/7 的容量指标证明单机模块化单体达到瓶颈时才拆分服务；租户隔离、故障转移、恢复点/恢复时间目标和滚动升级均通过演练。

## Definition of Done

一个任务只有同时满足以下条件才算完成：

1. 实现和类型契约齐全；
2. 正常、边界和失败路径有测试；
3. 日志/指标可定位失败阶段；
4. API 或行为变化同步文档；
5. 不引入未说明的安全权限；
6. 在干净容器环境验证通过。
