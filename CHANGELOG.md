# Changelog

## Unreleased

- 接入 Ollama `qwen3-embedding:0.6b` 与 `qwen3:8b` 本地 Provider、严格响应校验、引用白名单和模板降级；
- 增加面向 8 GiB NVIDIA GPU 的零 API 费用 Compose 覆盖和模型独立 Qdrant collection；
- Embedding 模型或解析器版本变化时强制完整重建，阻止增量索引复用不兼容向量；
- 完成真实模型容器 E2E 与 Retrieval/Agent 基准，并记录本机质量和延迟基线；
- 增加项目全景审查、生产化 Backlog 和 Phase 6–9 迭代路线图；
- 修正文档中的现状/目标漂移，并修复 Ruff 格式门禁；
- 修复大型 Rust AST 的非法 Tree-sitter 节点范围导致 Worker `SIGSEGV`；
- 将原生解析器隔离到可重启子进程，单文件失败降级为全文 Chunk；
- 增加索引任务租约回收、最大重试和失败构建版本复用。

## 0.1.0 — 2026-07-20

- 多语言 Tree-sitter 解析与 AST-aware Chunk；
- Qdrant、BM25、Symbol Hybrid Retrieval 与 Reranker；
- LangGraph 可恢复 Agent、SSE、预算、取消和引用验证；
- Working/Session/Project/Module/Episodic Memory；
- 文件级增量索引、来源失效与旧版本回收；
- FastAPI、Demo CLI、浏览器 UI 和 Docker Compose 发布栈。
