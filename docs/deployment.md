# 部署、配置与运维

## 1. Docker Compose 拓扑

```text
api       FastAPI HTTP/SSE，默认无状态
worker    仓库导入、解析、Embedding 和索引任务
postgres  元数据、任务、Memory
qdrant    Dense Vector Index
```

BM25 索引在 MVP 可使用持久卷；若 API 和 Worker 需要并行访问，必须使用单写多读策略或将词法检索封装成独立进程。开发模式可以使用 SQLite + 内存向量/词法索引运行最小栈。

## 2. 快速部署

```bash
cp .env.example .env
docker compose up -d --build --wait
docker compose --profile demo run --rm demo
```

Compose 使用四个命名卷：`postgres-data`、`qdrant-data`、`index-data` 和 `repository-work`。`docker compose down` 保留数据；`docker compose down -v` 会永久删除全部项目数据。

生产资源覆盖：

```bash
docker compose -f compose.yaml -f deploy/compose.production.yaml up -d --wait
```

生产覆盖文件隐藏 PostgreSQL/Qdrant 宿主机端口并设置资源上限。真实部署还应替换数据库密码、接入 TLS/反向代理、Secret Manager、认证授权、限流和资源配额；当前 MVP 不包含这些公网服务必需能力。

## 3. 配置分组

所有配置来自环境变量或 `.env`，仓库只提交 `.env.example`：

- `CODEMIND_DATABASE_*`
- `CODEMIND_QDRANT_*`
- `CODEMIND_REPOSITORY_*`
- `CODEMIND_EMBEDDING_*`
- `CODEMIND_RERANKER_*`
- `CODEMIND_LLM_*`
- `CODEMIND_AGENT_*`
- `CODEMIND_OBSERVABILITY_*`

宿主机端口可通过 `CODEMIND_API_PORT`、`CODEMIND_POSTGRES_PORT`、`CODEMIND_QDRANT_HTTP_PORT` 和 `CODEMIND_QDRANT_GRPC_PORT` 覆盖，容器内部服务地址保持不变。

向量写入时会校验维度，生产化阶段还应在启动时主动核对模型维度与已有 collection schema。生产密钥只从 secret manager 或容器 secret 注入。

## 4. 安全基线

- API 已限制单字段长度和 Run 步骤、工具调用、Token、超时预算；请求体上限、仓库总大小、全局并发和租户配额尚待实现；
- Git clone 禁止交互式凭证提示并设置超时；
- 不执行 hooks、构建脚本、测试或任何仓库二进制；
- 仓库快照放入独立工作目录，规范化并校验所有路径；
- 对远程 URL 进行协议白名单和 SSRF 防护；
- 访问日志不记录请求体、Prompt 或源码，Git URL 拒绝内嵌凭证；通用日志字段脱敏和更完整的 secret scanner 尚待实现；
- 容器使用非 root 用户，只挂载必要目录。

发布 Compose 进一步启用只读根文件系统、`no-new-privileges`、最小 tmpfs、PID 1 init 和日志轮转。API 对 BM25 卷只读；只有 Worker 可以写词法索引和仓库工作卷。

## 5. 健康与可观察性

每个 API request、index job、agent run 关联统一 trace id。

建议采集的关键指标如下。当前实现已在响应/日志中记录部分阶段耗时与 usage，但尚未提供 Prometheus 等可抓取指标端点和告警规则：

- 导入：文件数、跳过数、解析失败率、Chunk 数、Embedding 吞吐；
- 检索：各路延迟、交集率、Reranker 延迟、最终结果数；
- Agent：任务完成率、步骤数、工具错误率、Token 与费用；
- 系统：队列深度、数据库/Qdrant 错误率、P50/P95/P99 延迟。

日志记录 ID、阶段、耗时和错误分类，默认不记录完整源码、完整 Prompt 或回答。

```bash
curl --fail http://127.0.0.1:8000/health/live
curl --fail http://127.0.0.1:8000/health/ready
docker compose ps
docker compose logs --tail=100 api worker
```

## 6. 数据保留与恢复

- PostgreSQL 定期备份；Qdrant collection 使用 snapshot；
- 仓库工作副本是可再生缓存，不作为唯一事实存储；
- 旧索引版本保留最近 N 个或 N 天后回收；
- 生产化阶段需要增加仓库后台删除任务，依次删除 Memory、关系/Chunk、向量、BM25 和工作副本，并生成审计记录；当前尚无该管理 API。

PostgreSQL 逻辑备份示例：

```bash
docker compose exec -T postgres pg_dump -U codemind -d codemind -Fc > codemind.dump
```

备份文件包含仓库元数据、Run/Event、Memory 和索引版本指针；Qdrant 数据需要单独创建 snapshot。恢复演练应在独立 Compose project 和端口上进行，不要直接覆盖生产卷。当前仓库提供命令示例，但未提供自动备份、Qdrant snapshot 编排或恢复脚本。

## 7. 零 API 费用的本地模型

仓库提供 `deploy/compose.local-models.yaml`，面向约 8 GiB NVIDIA 显存的开发机：

- Ollama `0.32.0`；
- `qwen3-embedding:0.6b`，1024 维，约 639 MB；
- `qwen3:8b` 4-bit，约 5.2 GB，上下文限制为 8192；
- `HeuristicCodeReranker`，避免额外模型与 LLM 争抢显存；
- 模型卷 `ollama-data` 和独立 Qdrant collection。

Linux Docker 需要 NVIDIA 驱动和 NVIDIA Container Toolkit。安装后确认 `docker info` 包含 `nvidia` runtime，且容器能够看到 GPU。启动：

```bash
sudo docker compose \
  -f compose.yaml \
  -f deploy/compose.local-models.yaml \
  up -d --build --wait
```

首次启动由 `model-loader` 下载两个模型。确认状态：

```bash
curl http://127.0.0.1:11434/api/tags
curl http://127.0.0.1:8000/health/ready
sudo docker compose \
  -f compose.yaml \
  -f deploy/compose.local-models.yaml \
  exec ollama ollama ps
```

切换到真实 Embedding 后，已有仓库必须重新提交 Index Job。系统会因为 `embedding_model` 变化创建新版本，不会复用 Hash Embedding 版本；新向量写入独立 collection。回到默认离线模式只需使用基础 `compose.yaml` 启动。

Ollama 只绑定宿主机 `127.0.0.1:11434`。本地模型仍会消耗磁盘、电力和 GPU 资源，但不会把代码发送到付费模型 API。
