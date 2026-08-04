# `code-review-graph` 借鉴分析与实施映射

## 1. 文档目的

本文记录 CodeMind 对外部项目 [`tirth8205/code-review-graph`](https://github.com/tirth8205/code-review-graph) 的设计审查，供后续开发影响面分析、图查询、评测体系和开发者工具集成时参考。

本次审查基于 `code-review-graph` commit [`6ce25b4e53f9df397f5136e86a59e17c02a610fe`](https://github.com/tirth8205/code-review-graph/tree/6ce25b4e53f9df397f5136e86a59e17c02a610fe)，审查日期为 2026-07-22。外部项目会继续变化；实现前应重新核对其最新设计、许可证和评测数据，不能把本文中的版本描述当成永久事实。

本文不是采用该项目或迁移其代码的决策。结论是：**CodeMind 不应复制它的整体架构，但应吸收其变更驱动图分析、图工具契约、真实仓库评测和客户端接入经验。**

## 2. 定位对比

| 维度 | CodeMind | `code-review-graph` | 判断 |
|---|---|---|---|
| 核心定位 | 版本化代码知识库、Hybrid RAG、可恢复 Agent 与分层 Memory | 本地优先的代码图、代码审查上下文与 MCP/CLI 工具 | 场景重叠，但产品边界不同 |
| 事实模型 | `repository_id + commit_sha + path + line_range`，索引版本不可变 | 本地工作区中的节点、边和增量图 | CodeMind 的版本化事实模型应保留 |
| 存储 | PostgreSQL、Qdrant、BM25，API/Worker 分进程 | 以本地 SQLite 图为中心 | 不迁移存储架构 |
| 检索 | Dense、BM25、Symbol、RRF、可选 Rerank、Evidence | 图查询、FTS/Embedding、结构化精简上下文 | 图结果应作为 CodeMind 检索的一路证据 |
| 工作流 | 显式状态机、预算、持久化、SSE、取消、恢复 | MCP/CLI 工具和预设提示词 | 保留 CodeMind Workflow Runtime |
| 变更分析 | 已规划 Git history/diff/blame 与影响面工作流 | 已有 diff-to-symbol、影响半径、Flow、风险和测试缺口 | 是最直接的产品借鉴点 |
| 客户端 | REST、Demo CLI/Web；IDE/LSP 在路线图中 | MCP、CLI、hooks、VS Code 和多种 AI 客户端 | 借鉴适配层和安装体验 |
| 评测 | 小型 fixture 的检索与 Agent 基线 | 固定真实仓库、多跳、Agent baseline、影响面和 Token 评测 | 评测方法值得优先吸收 |

## 3. CodeMind 已有基础与当前缺口

CodeMind 已具备继续演进所需的基础，不需要为了引入代码图能力而重写：

- `RelationType` 已覆盖 `CONTAINS`、`IMPORTS`、`CALLS`、`EXTENDS`、`IMPLEMENTS` 和 `REFERENCES`；
- Parser 已提取符号、import 和粗粒度 call，并为关系保留置信度；
- Run 绑定单一 `index_version`，图事实可以稳定追溯到仓库版本；
- `trace_symbol` 和 `find_references` 已能完成一层关系查询；
- Hybrid Retrieval、Evidence、引用校验、Memory 和显式 Agent Workflow 已可承载更复杂的图分析。

当前缺口主要不在“有没有图数据”，而在“有没有把图能力组织成可靠的开发者工作流”：

1. `find_references` 是最多 100 条的一层关系读取，没有方向、边类型、深度、路径、分页和 `truncated` 契约。
2. 当前没有把 Git diff 的变更行稳定映射到变更符号。
3. 当前没有从变更符号执行有界多跳遍历，也没有输出传播路径和每条边的置信度。
4. 当前没有把受影响测试、入口流程、模块边界、历史 churn 等信息组合成风险解释。
5. 当前评测主要依赖小型 fixture；满分适合作为回归门禁，但不足以证明真实仓库效果。
6. MCP、IDE 和图浏览 API 尚未形成稳定的外部契约。

## 4. 值得借鉴的能力

### 4.1 变更驱动的影响面分析

`code-review-graph` 的核心价值不是简单保存节点和边，而是把代码审查组织成以下流水线：

```text
Git diff / working tree
        ↓
变更文件与变更行范围
        ↓
与行范围相交的文件、类、函数和方法
        ↓
沿 CALLS / IMPORTS / REFERENCES / TESTED_BY 等边进行有界遍历
        ↓
受影响入口流程、模块、调用方、依赖项和测试
        ↓
按可解释因子计算风险并提出验证建议
```

这条流水线应作为 CM-701/CM-702 的主要参考，但在 CodeMind 中应满足更严格的事实约束：

- 输入必须明确 `repository_id`、基准版本、目标版本或工作区快照；
- diff、符号和关系必须属于明确的索引版本，不允许混用两个版本的图；
- 每个受影响结论至少返回一条从变更种子到目标的可验证路径；
- 每条路径返回边类型、方向、置信度和对应源码位置；
- 启发式风险和测试建议必须标记为派生分析，不能伪装成静态事实；
- 图不完整、目标未解析或结果截断时必须显式说明。

### 4.2 可组合的有界图查询契约

CodeMind 不宜为每个场景增加一个高度定制的工具。应先建立少量稳定原语，再由 `trace_symbol`、`explain_module` 和 `analyze_change_impact` 工作流组合使用。

建议的核心查询输入：

```json
{
  "seed_symbols": ["auth.login"],
  "direction": "incoming",
  "relation_types": ["CALLS", "REFERENCES"],
  "max_depth": 2,
  "max_nodes": 200,
  "include_paths": true,
  "minimum_confidence": 0.5
}
```

建议的核心结果契约：

```json
{
  "nodes": [],
  "edges": [],
  "paths": [],
  "total_matched": 0,
  "truncated": false,
  "warnings": [],
  "index_version_id": "..."
}
```

实现约束：

- `direction` 至少支持 `incoming`、`outgoing`、`both`；
- `max_depth`、`max_nodes`、执行时间和响应 Token 均有硬上限；
- 排序必须确定，截断必须可观察；
- 循环图只保留节点的最佳或最短可解释路径，避免枚举指数级路径；
- 未解析的 `target_text` 不能假装成已解析节点；
- API、Agent Tool 与未来 MCP Adapter 应复用同一 application port 和 DTO。

### 4.3 Flow：从入口点理解执行路径

外部项目把 HTTP handler、CLI command、测试等入口沿调用边展开为 Flow。该思路对以下场景有价值：

- “认证请求从哪里进入，经过哪些核心函数”；
- “修改这个函数会影响哪些用户可见流程”；
- “哪个测试覆盖了这条关键路径”；
- `explain_module` 中展示模块参与的主流程。

在 CodeMind 中，Flow 应是绑定索引版本的派生投影，而不是新的事实源。建议保存：

- 入口符号及入口类型；
- 有界关键路径和路径置信度；
- 涉及的文件、模块和测试；
- 生成算法版本、参数和时间；
- 来源 symbol/relation ID，用于索引更新后的精确失效。

第一版只覆盖可以确定识别的入口；不要用 LLM 猜测入口后写回事实表。

### 4.4 Community 与架构摘要

基于图聚类形成代码社区，可以辅助模块解释、架构概览、热点和跨模块耦合分析。它与 CodeMind 的 Project/Module Memory 有互补关系：

- 图社区提供结构候选；
- Project/Module Summary 提供带证据的自然语言解释；
- 两者都必须绑定索引版本并记录生成方法；
- 聚类结果变化只能表示结构信号变化，不能直接等价为“架构漂移”。

此能力优先级低于影响面分析。只有在真实仓库评测证明目录边界不足以支持模块理解时，才引入 Leiden 或其他社区发现依赖。应始终保留基于目录/包的确定性降级路径和固定随机种子。

### 4.5 测试关联、风险解释与 Token 预算

风险输出应由透明因子组成，而不是单一不可解释分数。可以参考的因子包括：

- 变更符号数量和修改行范围；
- 入边/出边数量与最大传播深度；
- 是否位于关键入口 Flow；
- 是否涉及安全、认证、数据迁移或公开 API；
- 是否存在可解析的关联测试；
- 文件历史 churn 和近期共同变更；
- 图解析置信度、缺边警告和结果截断。

建议同时返回因子明细、原始值、权重版本和最终等级。没有关联测试只能表述为“图中未找到测试关系”，不能断言“没有测试”。

对于上下文预算，应分别记录：

- 可比较的 baseline 定义；
- 实际返回上下文的字符数和 Token 数；
- 估算值与真实 tokenizer 结果；
- 被截断的节点、路径和证据数量。

不能只用“全仓库 Token / 图响应 Token”宣传节省比例，因为真实 Agent 通常会先 grep、搜索和读取少量文件。

### 4.6 MCP、hooks 与 IDE 接入

`code-review-graph` 的 MCP、CLI、hooks 和 VS Code 集成说明了代码图能力如何进入日常开发流程。CodeMind 可以借鉴：

- 安装时自动发现客户端，但任何配置写入都应支持 dry-run 和明确确认；
- session start 只提示图能力和索引状态，不强迫 Agent 忽略正常文件读取；
- 文件变化触发增量更新，但更新失败不能阻塞 Git commit；
- IDE 展示影响半径、引用跳转、索引陈旧状态和 Run 进度；
- MCP 作为 REST/application service 的薄适配器，不直接访问数据库；
- CLI、REST、MCP 和 IDE 使用同一查询语义、权限和配额。

MCP/IDE 工作应排在稳定的公开 Tree/Symbol/Relation/Traversal API 之后。

### 4.7 自定义语言扩展

外部项目允许通过配置映射扩展 tree-sitter-language-pack 中的语言。CodeMind 后续可以借鉴“Parser Registry + 配置扩展”的方向，但不能只靠通用 node type 列表承诺高质量关系图。

建议把语言支持分级：

- `structure`：文件、类、函数等结构节点；
- `imports`：可提取并部分解析 import；
- `calls`：可提取调用，可能只有未解析目标文本；
- `resolved_graph`：通过 LSP、编译器或框架适配器完成跨文件解析。

API 和评测结果应暴露语言能力等级，避免“能解析语法”被误解为“调用图准确”。

## 5. 最值得借鉴的评测方法

评测改进应早于复杂图功能扩张，否则无法判断功能增加是否真的改善 Agent 表现。

### 5.1 固定真实仓库与可复现环境

- 选择许可证清晰、规模和语言不同的公开仓库；
- 配置固定完整 commit SHA，禁止用浮动 `HEAD`；
- 保存仓库、模型、Tokenizer、Parser、图算法和随机种子版本；
- clone、checkout、build 任一步失败都记录为 error，不能当作零结果参与汇总；
- 输出逐任务原始记录和聚合报告，确保失败可审计。

### 5.2 Agent baseline

至少比较以下三种策略：

1. `grep/read-top-k`：模拟现实中的基础编码 Agent；
2. CodeMind Hybrid Retrieval：Dense + BM25 + Symbol + Rerank；
3. CodeMind Hybrid + Graph Traversal：先找锚点，再遍历关系。

比较 Task Success、Recall、错误路径、读取文件数、输入 Token、延迟和模型费用。全仓库读取只能作为理论上界，不能作为唯一 baseline。

### 5.3 多跳检索任务

任务应拆分记录：

- 锚点符号是否进入 top-k；
- 图遍历是否返回正确邻居；
- 是否找到正确关系方向和路径；
- 证据行号是否支持最终结论。

只有锚点和目标关系都正确时，完整任务才算成功。这样可以区分检索失败与图解析失败。

### 5.4 影响面独立真值

不能只用当前图自身生成 ground truth 再验证同一张图。建议并列报告：

- `graph-derived`：作为算法一致性和理论上界，明确标注循环性；
- `co-change`：给定一个 seed 文件/符号，使用同一真实 commit 中其他变更文件作为独立但有噪声的真值；
- `curated`：人工标注少量关键变更的真实依赖和应运行测试。

分别报告 Recall、Precision、F1 和截断率。影响分析可以偏向 Recall，但必须展示过度预测程度。

### 5.5 Token 效率

Token 节省必须附带 baseline、Tokenizer 和上下文组成。建议同时报告：

- 全仓库理论基线；
- grep/read-top-k Agent 基线；
- 仅变更文件基线；
- CodeMind 检索响应；
- CodeMind 检索加图路径响应。

快速路径可以使用字符估算，但必须标记 `estimated=true`；正式报告使用目标模型对应 tokenizer 复核。不同基线的比例不得混在同一指标中。

## 6. 不应照搬的内容

### 6.1 不迁移为单机 SQLite 中心架构

CodeMind 的不可变索引版本、Run 版本绑定、PostgreSQL/Qdrant/BM25 和未来权限边界是核心产品约束。不能为了图遍历方便，将事实源退化为工作区绝对路径和单个可变 SQLite 数据库。

SQLite 可以继续用于本地 BM25 或客户端缓存，但不能成为服务端图事实的第二套权威来源。

### 6.2 不无节制扩展工具数量

大量细粒度工具会扩大权限、版本兼容、提示选择和测试成本。优先提供：

1. `search_code`；
2. `read_symbol`；
3. `traverse_relations`；
4. `analyze_change_impact`；
5. `get_project_or_module_summary`。

热点、测试缺口、Flow 和架构概览可以先作为上述工具的参数或工作流产物，经过真实使用验证后再决定是否成为独立公开工具。

### 6.3 不自动修改或提交目标仓库

外部项目包含重构和应用修改相关能力，但 CodeMind 当前范围明确只读。影响面和变更方案可以输出建议及证据，不应执行修改、提交或任意仓库脚本。若未来改变该边界，必须单独完成 ADR、权限模型、沙箱和审计设计。

### 6.4 不把启发式结果提升为事实

Community、风险、关键路径、测试覆盖和“死代码”都可能存在误判。返回值必须区分：

- `extracted`：直接来自源码解析；
- `resolved`：通过确定性解析器/LSP/编译器解析；
- `inferred`：图算法或规则推断；
- `generated`：模型生成的解释。

每一层都应保留来源、算法版本和置信度。

### 6.5 不以语言数量替代解析质量

支持更多扩展名不等于拥有可靠的跨文件关系。新增语言必须按能力等级建立 fixture 和真实仓库评测，尤其验证同名符号、别名、动态调用、框架注入和语法错误。

## 7. CodeMind 实施顺序

### Phase 6：不改变当前优先级

继续先完成认证、配额、任务租约、指标告警、数据生命周期和供应链门禁。外部项目的功能面不构成跳过生产可靠性工作的理由。

### Phase 7：先升级评测闭环

1. 建立固定 SHA 的真实仓库集和可复现下载/校验流程；
2. 增加 grep/read-top-k Agent baseline；
3. 增加多跳检索任务，将锚点召回与图邻居正确性分开计分；
4. 建立 graph-derived、co-change 和 curated 三类影响面真值；
5. 校准 Token、延迟、成本与上下文截断指标；
6. 将逐任务错误记录和回归差异纳入发布报告。

### Phase 8：交付变更影响纵向切片

建议按以下依赖顺序实施：

```text
公开版本化 Symbol/Relation API
        ↓
有界 traverse_relations 原语
        ↓
Git diff-to-symbol 映射
        ↓
受影响路径、测试缺口与风险因子
        ↓
analyze_change_impact Workflow + SSE + Evidence
        ↓
MCP Adapter / VS Code 展示
        ↓
有评测需求后再增加 Flow / Community
```

第一条可交付纵向切片只需要支持：指定两个 commit、解析变更行、映射变更符号、沿 `CALLS/IMPORTS/REFERENCES` 两跳遍历、返回受影响路径和候选测试。暂不需要 Community、自动 Wiki 或自动重构。

## 8. 建议验收标准

### 图查询

- 所有查询绑定单一 `index_version_id`；
- 支持方向、关系类型、深度、节点数和最低置信度过滤；
- 响应包含路径、来源行、`total_matched`、`truncated` 和 warnings；
- 循环图、稠密图、同名符号和未解析目标有确定行为；
- 达到预算或超时时返回可解释的部分结果，不伪装为完整结果。

### 影响面分析

- diff 行范围可正确映射新增、修改、删除和重命名；
- 每个受影响结论可回溯到变更 seed 和关系路径；
- 测试缺口使用“未找到关系”措辞；
- 风险等级返回透明因子与算法版本；
- 旧索引、部分解析、关系低置信度和结果截断均产生警告；
- 工作流只读、可取消、可恢复，并记录 Step/Event/usage。

### 评测

- 至少覆盖 6 个固定 SHA 的真实仓库和 CodeMind 自身仓库；
- 至少覆盖 Python、Rust、JavaScript 和 TypeScript；
- baseline 与待测策略读取相同版本的仓库；
- 失败任务保留 `status=error`，不纳入成功聚合；
- 分别报告 anchor recall、neighbor accuracy、impact Recall/Precision/F1、Task Success、Token、延迟和成本；
- 随机过程固定 seed，评测环境和原始结果可复现；
- 不再把小 fixture 的满分描述为真实仓库质量证明。

## 9. 相关外部资料

- [`code-review-graph` README](https://github.com/tirth8205/code-review-graph/blob/6ce25b4e53f9df397f5136e86a59e17c02a610fe/README.md)
- [Architecture](https://github.com/tirth8205/code-review-graph/blob/6ce25b4e53f9df397f5136e86a59e17c02a610fe/docs/architecture.md)
- [Features](https://github.com/tirth8205/code-review-graph/blob/6ce25b4e53f9df397f5136e86a59e17c02a610fe/docs/FEATURES.md)
- [Knowledge Graph Schema](https://github.com/tirth8205/code-review-graph/blob/6ce25b4e53f9df397f5136e86a59e17c02a610fe/docs/schema.md)
- [Reproducing the Benchmarks](https://github.com/tirth8205/code-review-graph/blob/6ce25b4e53f9df397f5136e86a59e17c02a610fe/docs/REPRODUCING.md)

外部资料仅作为设计输入。实施时仍以 CodeMind 的产品范围、ADR、领域模型、Backlog 和冻结评测结果为决策依据。
