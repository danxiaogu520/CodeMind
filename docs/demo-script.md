# CodeMind 三分钟演示脚本

## 0:00–0:30：定位与启动

介绍：CodeMind 不是通用文档 RAG，而是面向代码结构、版本、调用关系与长期项目上下文的 RAG Agent。

```bash
docker compose up -d --build --wait
docker compose --profile demo run --rm demo
```

指出 Compose 同时启动 API、索引 Worker、PostgreSQL 和 Qdrant；CLI 不需要本机安装 Python 包。

## 0:30–1:20：代码知识库与 Hybrid Retrieval

CLI 会导入 `/fixtures/sample_python`，展示 Index Job 阶段和增量统计。强调：Tree-sitter 以符号切分；Dense、BM25、Symbol 三路召回经 RRF 和 Reranker 合并；每条 Evidence 固定 commit、路径和行号。

打开 `http://127.0.0.1:8000/demo`，保留自动填充的 fixture，点击 “Build knowledge base”。

## 1:20–2:20：Agent 工作流

问题使用 `Trace calls to create_token`，Workflow 选择 `trace_symbol`。点击 “Run grounded analysis”，展示：

- LangGraph 的 plan/retrieve/inspect/synthesize/validate；
- `search_code`、`find_references`、`read_symbol` 工具事件；
- `AuthService.login --CALLS--> create_token` 静态关系；
- 最终答案中的 `src/tokens.py:4-5` 等可验证引用。

说明 Run/Step/Event 和 PostgreSQL Checkpoint 可恢复，预算与取消阻止无限循环。

## 2:20–3:00：Memory 与增量更新

展示 Memory 卡片：Project、Module、Session、Episodic。说明 Working Memory 在 Graph State 中压缩，其他层绑定 Index Version 和来源哈希；代码变化后只复用来源未变化的分析。

最后展示索引任务的 `files_changed/files_reused/chunks_embedded/chunks_reused`，总结项目差异点：代码结构检索、可恢复 Agent、来源级 Memory 失效和工程化交付。
