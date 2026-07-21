# Phase 5 性能与发布验收报告

## 测试环境与方法

- 日期：2026-07-20；
- Python 3.12，依赖由 `uv.lock` 固定；
- Docker Compose 独立 project，使用全新 PostgreSQL、Qdrant、BM25 和仓库工作卷；
- Embedding、Reranker、LLM 使用项目内确定性离线 Provider；
- API、Worker 采用发布镜像，非 root 用户、只读根文件系统；
- 延迟由公开 HTTP API runner 端到端测量，不含容器首次拉取镜像时间。

## 实测结果

| 场景 | 结果 |
|---|---|
| 全新数据卷启动至 API/Worker/PostgreSQL/Qdrant healthy | 13 秒 |
| CLI 导入 Python fixture、索引、Agent、引用与 Memory | 约 5 秒 |
| Python Retrieval | Recall@5/10 1.00，MRR 0.611，nDCG@10 0.710，P95 67.6ms |
| Agent Workflow | 5/5 completed，引用有效率 1.00，路径召回 1.00，平均 372.0ms |
| 四语言 Retrieval | Recall@5/10、MRR、nDCG@10 1.00，P95 56.0ms |
| 单文件增量索引 | 1 changed、2 reused、2 chunks embedded、3 chunks reused |

## 架构权衡

- SQLite BM25 采用共享卷单写多读，适合单 Worker MVP；多 Worker 部署应换成独立 Lexical 服务。
- feature-hash Embedding 和 template LLM 保证离线复现，但不代表生产模型的语义上限；Provider 端口允许替换。
- PostgreSQL 表队列减少部署依赖；任务吞吐明显增长后可替换 Redis Streams 或专用队列。
- Tree-sitter 调用图是静态启发式结果，输出置信度，不声称具备编译器或 LSP 级精度。
- 当前数据集用于发布回归而非统计显著的公开排行榜，扩容前需增加真实开源仓库与 10 万 Chunk 压测。

## 本地真实模型验收（2026-07-21）

环境为 Ryzen 5 9600X、11 GiB WSL 内存和 8 GiB RTX 5060 Ti，使用
`deploy/compose.local-models.yaml` 启用 `qwen3-embedding:0.6b`（1024 维）与
`qwen3:8b` Q4。Ollama、API、Worker、PostgreSQL 和 Qdrant 均通过健康检查，
readiness 返回 `postgres/qdrant/ollama: ok`。

| 场景 | 结果 |
|---|---|
| Python fixture 首次索引 | 2 files，5 chunks 全部使用真实 Embedding |
| Grounded Agent 演示 | completed，2 条有效源码行号引用 |
| Retrieval（3 cases） | Recall@5/10 1.00，MRR 0.611，nDCG@10 0.710，平均 2157ms，P95 3608ms |
| Agent（5 cases） | 完成率 1.00，引用有效率 1.00，期望路径召回 0.90，平均 8969ms |

小数据集下的质量门槛已通过，但本地模型的延迟显著高于确定性 Provider；这些结果是开发机基线，
不能外推为生产容量结论。模型切换测试还验证了 build identity 变化会强制完整重建，旧模型向量不会被增量复用。

## 复现命令

```bash
docker compose up -d --build --wait
docker compose --profile demo run --rm demo

# 本地真实模型
sudo docker compose -f compose.yaml -f deploy/compose.local-models.yaml up -d --build --wait
sudo docker compose -f compose.yaml -f deploy/compose.local-models.yaml --profile demo run --rm demo

uv run codemind-eval-retrieval \
  --repository-id <repository_id> \
  --dataset eval/datasets/sample_python.jsonl

uv run codemind-eval-agent \
  --repository-id <repository_id> \
  --dataset eval/datasets/sample_agent.jsonl
```
