# 测试与评测

## 1. 测试金字塔

### 单元测试

- ignore/路径安全和语言检测；
- AST 到统一符号模型的映射；
- Chunk 边界、行号、稳定 ID 和超长拆分；
- 代码 tokenizer、RRF、去重和上下文预算；
- Agent 状态迁移、预算和错误降级；
- LangGraph Adapter 的状态映射、stream 事件投影和节点幂等；
- Memory 失效规则。

### 集成测试

- PostgreSQL/Qdrant 索引写入和版本切换；
- 从 fixture 仓库导入到 search 的完整流水线；
- Embedding/Reranker/LLM 使用可重复的 fake provider；
- 任务重试、幂等和部分解析失败；
- PostgreSQL Checkpointer 的进程中断恢复，且已完成节点的副作用不重复提交。

### 端到端测试

- Docker Compose 启动；
- 导入小型多语言仓库；
- 等待索引完成；
- 执行问答并验证引用文件、行号和索引版本。

## 2. 检索评测集

在 `tests/fixtures/repos` 保存许可清晰的小型仓库或自建仓库；在 `eval/datasets` 保存 JSONL：

```json
{"question":"Where is the token signed?","relevant":[{"path":"src/auth/token.py","symbol":"create_token"}],"tags":["semantic","python"]}
```

至少覆盖：精确符号、自然语言职责、路径/错误文本、跨文件调用、同名符号、无答案。指标包括 Recall@5/10、MRR、nDCG@10、延迟。

## 3. 回答评测

将“检索”和“生成”分开评估：

- Citation Validity：引用是否存在且行号有效；
- Citation Correctness：证据是否支持相邻结论；
- Completeness：问题要求的关键点是否覆盖；
- Groundedness：是否出现证据外的代码事实；
- Task Success：工作流是否完成预期产物。

优先使用确定性检查和人工标注；LLM-as-judge 只作为辅助，并固定 judge prompt/model/version。

## 4. 发布门槛

- 单元和集成测试全部通过；
- 数据库迁移可向前执行；
- 基准集 Recall@10 不低于上一版本超过允许波动；
- 无效引用率为 0；
- 关键安全用例（路径穿越、恶意 URL、Prompt Injection 文本）通过；
- Docker 镜像可在干净环境完成健康检查和一次端到端任务。

## 5. Phase 5 发布基线

2026-07-20 在独立 Compose project、全新命名卷和离线确定性 Provider 上实测：

| Benchmark | Cases | 核心指标 | 延迟 |
|---|---:|---|---|
| Python Retrieval | 3 | Recall@5/10 1.00，MRR 0.611，nDCG@10 0.710 | avg 60.9ms，P95 67.6ms |
| Agent Workflow | 5 | 完成率 1.00，引用有效率 1.00，路径召回 1.00 | avg 372.0ms |
| Polyglot Retrieval | 5 | Recall@5/10、MRR、nDCG@10 均 1.00 | avg 39.5ms，P95 56.0ms |

Polyglot 数据集覆盖 Python、Rust、TypeScript 和 JavaScript。以上是小型 fixture 的回归基线，不应外推为 10 万 Chunk 的容量结论；大仓库性能目标仍需独立压测。

## 6. Phase 7 真实仓库与图分析评测扩展

后续评测参考 [`code-review-graph` 借鉴分析](reference-code-review-graph.md)，但使用 CodeMind 自身的版本化 Evidence 和工作流契约。至少增加：

- 固定完整 commit SHA 的真实公开仓库集，保存许可证、模型、Parser、Tokenizer、算法版本和随机种子；
- `grep/read-top-k` Agent、Hybrid Retrieval、Hybrid + Graph Traversal 三组可比策略；
- 多跳任务分别记录 anchor recall、neighbor accuracy、关系方向、路径和引用正确性；
- 影响面同时使用 graph-derived 理论上界、真实 commit co-change 和人工 curated 真值；
- Token 指标区分全仓库、Agent 搜索和变更文件 baseline，估算值标记 `estimated`，正式报告使用目标 tokenizer；
- clone、checkout、索引或工具调用失败均保留 `status=error`，不得把失败或空响应计为成功；
- 逐任务原始结果与聚合报告一同保存，报告 Recall、Precision、F1、Task Success、截断率、Token、延迟和成本。

小型 fixture 继续承担快速、确定性的 CI 回归职责；真实仓库集用于发布质量判断，二者不能互相替代。
