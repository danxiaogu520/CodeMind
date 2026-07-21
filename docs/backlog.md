# 工程 Backlog

本 Backlog 是可领取工程任务的事实源。优先级为 P0（阻断发布）、P1（近期必需）、P2（增强）；`✅` 为完成，`◐` 为部分完成，无标记为未完成。任务完成仍需满足[路线图中的 Definition of Done](roadmap.md#definition-of-done)。项目现状与风险依据见[项目审查](project-review.md)。

## Epic A：工程基线

| ID | 优先级 | 任务 | 依赖 | 验收摘要 |
|---|---|---|---|---|
| CM-001 | P0 ✅ | 初始化 Python 3.12、uv、Ruff、Pyright、Pytest | - | 本地单命令完成 lint/type/test |
| CM-002 | P0 ✅ | 建立 domain/application/infrastructure 分层和依赖检查 | CM-001 | domain 不导入 FastAPI/DB/模型 SDK |
| CM-003 | P0 ✅ | Pydantic Settings 与 `.env.example` | CM-001 | 缺失关键配置时启动快速失败且不泄密 |
| CM-004 | P0 ✅ | structlog、trace id、中间件和 Problem Details | CM-001 | 错误响应与日志可按 trace id 对齐 |
| CM-005 | P0 ✅ | PostgreSQL/Alembic/Qdrant Compose | CM-001 | 镜像构建、三服务 healthy、迁移和 API readiness 已验证 |
| CM-006 | P1 ✅ | CI 质量门禁 | CM-001 | PR 自动执行格式、类型、测试 |

## Epic B：仓库摄取与解析

| ID | 优先级 | 任务 | 依赖 | 验收摘要 |
|---|---|---|---|---|
| CM-101 | P0 ✅ | Repository、IndexVersion、IndexJob 模型和迁移 | CM-002, CM-005 | 状态约束、索引和时间字段齐全 |
| CM-102 | P0 ✅ | LocalSource/GitSource 端口与安全适配器 | CM-003 | 根路径约束、超时、禁止 hook/凭证提示 |
| CM-103 | P0 ✅ | 文件发现、ignore、二进制/大文件过滤 | CM-102 | fixture 中包含/排除清单完全匹配 |
| CM-104 | P0 ✅ | 语言检测与 Tree-sitter Parser Registry | CM-103 | 四类目标语言可路由，未知语言安全跳过 |
| CM-105 | P0 ✅ | Python 符号/关系提取 | CM-104 | 定义、import、局部 call 与行号测试通过 |
| CM-106 | P1 ✅ | Rust 符号/关系提取 | CM-104 | trait/impl/function/call fixture 通过 |
| CM-107 | P1 ✅ | JS/TS 符号/关系提取 | CM-104 | function/class/import/call fixture 通过 |
| CM-108 | P0 ✅ | AST-aware Chunker 与稳定 ID | CM-105 | 符号完整、超长可拆、重复运行 ID 稳定 |
| CM-109 | P1 ✅ | 幂等任务、解析错误隔离与文件级增量 diff | CM-108 | 跨 commit 仅解析/Embedding 变化文件，删除与复用统计可审计 |

## Epic C：索引与检索

| ID | 优先级 | 任务 | 依赖 | 验收摘要 |
|---|---|---|---|---|
| CM-201 | P0 ✅ | EmbeddingProvider + deterministic fake | CM-002 | 批量、维度检查和离线测试 |
| CM-202 | P0 ✅ | Qdrant VectorIndex | CM-005, CM-201 | upsert/search/version filter 容器验证 |
| CM-203 | P0 ✅ | 代码 tokenizer 与 BM25 LexicalIndex | CM-108 | camel/snake/qualified name 可精确召回 |
| CM-204 | P0 ✅ | Symbol/Path Search | CM-101, CM-108 | 精确、前缀和仓库版本过滤正确 |
| CM-205 | P0 ✅ | QueryAnalyzer 规则基线 | CM-204 | 符号、路径、关键词提取 fixture 通过 |
| CM-206 | P0 ✅ | 并行召回、RRF、去重和多样性 | CM-202, CM-203, CM-205 | 排序确定、单路失败可降级 |
| CM-207 | P1 ✅ | RerankerProvider 与超时降级 | CM-206 | 重排有效，Provider 失败不影响响应 |
| CM-208 | P0 ✅ | ContextPacker 与 Evidence 契约 | CM-206 | Token 不超预算、引用行号完整 |
| CM-209 | P0 ✅ | 版本化索引原子激活 | CM-109, CM-202, CM-203 | 查询不读取半完成版本 |

## Epic D：API 与任务执行

| ID | 优先级 | 任务 | 依赖 | 验收摘要 |
|---|---|---|---|---|
| CM-301 | P0 ✅ | 健康检查与应用生命周期 | CM-003, CM-005 | live/ready 区分进程与依赖状态，生命周期释放连接 |
| CM-302 | P0 ✅ | Repository/IndexJob API | CM-101, CM-102 | 202、轮询和错误契约一致 |
| CM-303 | P0 ◐ | 数据库任务队列与 Worker | CM-101 | 原子领取、心跳已完成；重试和取消随 Agent Run 任务治理补齐 |
| CM-304 | P0 ✅ | Search API | CM-208, CM-209 | 返回 Evidence、索引版本和耗时分解 |
| CM-305 | P2 | Tree/Symbol/Relation API | CM-105, CM-106, CM-107 | Agent 内部只读工具已完成；补公开分页、过滤和错误契约 |

## Epic E：RAG 与 Agent

| ID | 优先级 | 任务 | 依赖 | 验收摘要 |
|---|---|---|---|---|
| CM-401 | P0 ✅ | LLMProvider + 离线 grounded provider | CM-002 | Provider 隔离且无证据时明确拒答 |
| CM-402 | P0 ✅ | Grounded answer prompt 与引用验证器 | CM-208, CM-401 | 无效 evidence id 无法进入最终响应 |
| CM-403 | P0 ✅ | Run/Step/Event 数据模型 | CM-101 | 每次状态转换和 usage 可追踪 |
| CM-404 | P0 ✅ | 只读 Agent Tool Registry | CM-304, CM-305 | 白名单、参数、版本、预算统一校验 |
| CM-405 | P0 ✅ | `answer_question` LangGraph 工作流 | CM-402, CM-403, CM-404, CM-409 | 带引用问答 E2E 通过 |
| CM-406 | P1 ✅ | `explain_module` 工作流 | CM-405 | 报告包含职责、入口与项目统计 |
| CM-407 | P1 ✅ | `trace_symbol` 工作流 | CM-405 | 调用边带方向、证据和置信度 |
| CM-408 | P1 ✅ | Run API、SSE、预算和取消 | CM-403, CM-405, CM-409 | 事件有序、断线续传、取消和预算生效 |
| CM-409 | P0 ✅ | WorkflowRuntime 端口、LangGraph Adapter 与 PostgreSQL Checkpointer | CM-403 | application 无 LangGraph import；真实 crash-resume 幂等测试通过 |

## Epic F：Memory、评测与发布

| ID | 优先级 | 任务 | 依赖 | 验收摘要 |
|---|---|---|---|---|
| CM-501 | P1 ✅ | Working/Session Memory | CM-403 | 压缩保留 evidence id，Session 按 session_id 隔离 |
| CM-502 | P1 ✅ | Project/Module Summary | CM-405 | 绑定索引版本并支持分层相关性检索 |
| CM-503 | P1 ✅ | Episodic Memory 与来源失效 | CM-501, CM-502, CM-209 | 未变化来源重基复用，变化来源精确标记 stale |
| CM-504 | P0 ✅ | Retrieval JSONL 数据集与 runner | CM-206 | 输出 Recall/MRR/nDCG/延迟并可对比基线 |
| CM-505 | P0 ✅ | 多语言 E2E fixture 与 Compose 测试 | CM-405 | 四语言 fixture 在干净 Compose 环境 Recall@10=1.00 |
| CM-506 | P1 ✅ | 安全测试套件 | CM-302, CM-404 | 路径穿越、SSRF、凭证 URL、符号链接和注入文本受控 |
| CM-507 | P1 ✅ | Demo CLI、示例项目和演示脚本 | CM-408 | CLI 与 Web UI 可在 3 分钟内展示核心差异点 |
| CM-508 | P1 ✅ | 性能、评测与架构复盘报告 | CM-504, CM-505 | 冷启动、检索、Agent 与多语言指标可复现 |

## Epic G：生产化基线

| ID | 优先级 | 任务 | 依赖 | 验收摘要 |
|---|---|---|---|---|
| CM-601 | P0 | API 认证、授权与可信代理边界 | CM-004 | 未认证请求受控，仓库/Run 访问经过统一授权 |
| CM-602 | P0 | 请求、仓库、任务与 Run 资源配额 | CM-301, CM-303, CM-408 | 请求体、仓库规模、并发和速率限制可配置并可观测 |
| CM-603 | P0 | Index Job 租约心跳与取消 | CM-303 | 长步骤持续续租；取消和 Worker 失联不会双重提交 |
| CM-604 | P0 | Metrics、告警与跨进程关联 | CM-004, CM-301 | 暴露 RED/USE、队列与 Provider 指标，告警可演练 |
| CM-605 | P1 | 仓库删除、备份与恢复演练 | CM-209, CM-503 | 元数据/向量/BM25/Memory 可审计清理，备份可恢复 |
| CM-606 | P1 ◐ | 生产 Embedding/Reranker/LLM Provider | CM-201, CM-207, CM-401 | Ollama Qwen3 Embedding/LLM、超时、版本隔离与模板回退已完成；待评测模型 Reranker 及补重试/指标 |
| CM-607 | P1 | CI 发布供应链门禁 | CM-006 | 覆盖率、迁移、容器 smoke、漏洞、SBOM 与镜像检查自动化 |
| CM-608 | P1 | Secret scanner 与数据出站策略 | CM-103, CM-506 | 可配置检测、误报处置、统计与远程 Provider 出站边界齐全 |

## Epic H：开发者工作流与平台化

| ID | 优先级 | 任务 | 依赖 | 验收摘要 |
|---|---|---|---|---|
| CM-701 | P1 | Git 历史、diff 与 blame 检索 | CM-109, CM-208 | 答案可引用变更 commit、作者和对应代码版本 |
| CM-702 | P1 | 影响面分析与变更方案工作流 | CM-407, CM-701 | 给出可验证依赖路径、风险与测试建议，不自动写代码 |
| CM-703 | P2 | LSP/编译器级符号适配器 | CM-305 | 可按语言渐进启用并保留 Tree-sitter 降级路径 |
| CM-704 | P2 | LSP / VS Code 客户端 | CM-305, CM-408 | 编辑器内完成导入、查询、引用跳转和 Run 观察 |
| CM-705 | P2 | 多租户、私有 Git 凭证与审计 | CM-601, CM-602, CM-605 | 租户隔离、密钥轮换、最小权限和审计验证通过 |
| CM-706 | P2 | 分布式 Worker 与托管索引 | CM-603, CM-604 | 由容量测试触发，水平扩缩和故障转移可验证 |

## 第一条纵向切片

为尽早获得真实反馈，第一轮只做：

`CM-001 → 002 → 003 → 004 → 005 → 101 → 103 → 104 → 105 → 108 → 201 → 202 → 203 → 205 → 206 → 208 → 301 → 304`

完成后已经能够对一个本地 Python fixture 执行结构化解析和 Hybrid Search。随后补 `CM-302/303/209` 形成可靠导入，再补 `CM-401/402/405` 形成首个带引用问答 Demo。
