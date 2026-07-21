# 故障排查

## `/health/ready` 返回 503

```bash
docker compose ps
docker compose logs --tail=100 postgres qdrant migrate api
```

`/health/live` 只检查 API 进程；`/health/ready` 要求 PostgreSQL 和 Qdrant 同时可用。首次启动必须等待 `migrate` 正常退出。

## 8000、5432 或 6333 端口被占用

在 `.env` 中覆盖宿主机端口：

```dotenv
CODEMIND_API_PORT=18000
CODEMIND_POSTGRES_PORT=15432
CODEMIND_QDRANT_HTTP_PORT=16333
CODEMIND_QDRANT_GRPC_PORT=16334
```

容器间地址不变；只需把浏览器和 CLI 的 base URL 改为新 API 端口。

## 索引任务一直 queued

```bash
docker compose ps worker
docker compose logs --tail=200 worker
```

Worker 必须为 healthy。若导入本地仓库，传给 API 的路径必须是 Worker 容器可见且位于 `CODEMIND_ALLOWED_REPOSITORY_ROOTS` 的路径；默认演示路径是 `/fixtures/sample_python`。

## 索引任务长期停留在 running

Worker 使用 `heartbeat_at` 作为任务租约。进程异常退出后，超过
`CODEMIND_INDEX_JOB_STALE_AFTER_SECONDS`（默认 120 秒）的任务会自动重新入队；达到
`CODEMIND_INDEX_JOB_MAX_ATTEMPTS`（默认 3 次）后转为 failed。Tree-sitter 在独立子进程中
运行，超时由 `CODEMIND_PARSER_TIMEOUT_SECONDS`（默认 30 秒）控制；单个文件解析失败会
记录 warning 并使用全文 Chunk，最终任务状态为 partial，不会终止整个 Worker。

查看恢复事件：

```bash
docker compose logs --since=10m worker | grep -E 'stale_index_jobs_recovered|source_file_parse_failed'
```

## 本地启动 API 后 readiness 失败

本地 `uv run codemind-api` 不会自动启动依赖。使用：

```bash
docker compose up -d --wait postgres qdrant
uv run alembic upgrade head
uv run codemind-api
```

API 和 Worker 是两个进程；需要处理索引任务时还要运行 `uv run codemind-worker`，或直接使用完整 Compose。

## 清理与恢复

停止容器并保留数据：

```bash
docker compose down
```

删除所有 CodeMind 命名卷会永久删除 PostgreSQL、Qdrant 和索引数据，只应在明确需要全新环境时执行：

```bash
docker compose down -v
```

生产环境应分别备份 PostgreSQL 和 Qdrant snapshot；仓库工作副本和 BM25 可以从源仓库与元数据重建。
