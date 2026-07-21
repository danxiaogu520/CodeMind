# API 契约

交互式 OpenAPI 位于 `/docs`，同源浏览器 Demo 位于 `/demo`。Demo 只调用本章公开 REST/SSE 契约，不具有额外文件或执行权限。

API 前缀为 `/api/v1`。资源 ID 使用不透明 UUID/ULID；时间为 UTC ISO 8601。错误统一采用 RFC 9457 Problem Details 风格。

## 1. 仓库与索引

### `POST /repositories`

创建仓库并异步启动首个索引任务。

```json
{
  "source": {"type": "git", "url": "https://github.com/org/repo.git"},
  "ref": "main",
  "name": "repo"
}
```

响应 `202 Accepted`：

```json
{
  "repository_id": "repo_01",
  "job_id": "job_01",
  "status": "queued"
}
```

本地来源使用 `{"type":"local","path":"..."}`，但路径必须位于服务端允许根目录内。

### `GET /repositories/{repository_id}`

返回来源与活动索引版本。语言统计、Chunk 数量将在项目浏览 API 中补充。

### `POST /repositories/{repository_id}/index-jobs`

为新 ref/commit 创建索引任务。Worker 解析出 commit 后会复用相同解析器、Embedding 模型下已就绪的版本。

### `GET /index-jobs/{job_id}`

```json
{
  "id": "job_01",
  "status": "running",
  "stage": "embedding",
  "progress": {"processed": 820, "total": 1000},
  "warnings": 3,
  "error_summary": null
}
```

状态枚举：`queued | running | completed | partial | failed | cancelled`。

## 2. 检索

### `POST /repositories/{repository_id}/search`

用于调试和直接消费检索能力。

```json
{
  "query": "where is access token created",
  "filters": {"languages": ["python"], "path_prefix": "src/"},
  "limit": 10,
  "include_content": true
}
```

响应包含 `index_version_id`、Query Analysis、三路检索/重排耗时、降级来源和 `Evidence[]`。每条 Evidence 携带 commit、路径、符号、行号、RRF/Rerank 分数及命中原因；响应头 `X-CodeMind-Index-Version` 与正文版本一致。`include_content=false` 可只返回定位信息。

当前过滤器支持 `languages` 与安全的仓库相对 `path_prefix`。仓库不存在返回 404，尚无活动索引返回 409，三路召回全部不可用返回 503；单路或 Reranker 故障会在 `diagnostics.degraded_sources` 中体现，但请求继续成功。

## 3. Agent Run

### `POST /repositories/{repository_id}/runs`

```json
{
  "question": "分析用户认证流程",
  "workflow": "auto",
  "session_id": null,
  "options": {
    "max_steps": 8,
    "max_tool_calls": 8,
    "max_tokens": 12000,
    "timeout_seconds": 120
  }
}
```

接口统一返回 `202 + run_id`，Run 固定创建时的活动索引版本，并在后台执行。

提供相同 `session_id` 的后续 Run 可以召回当前索引版本内的 Session Memory；跨版本只复用来源未变化的记忆。

### `GET /runs/{run_id}`

```json
{
  "id": "run_01",
  "status": "completed",
  "workflow": "explain_module",
  "answer": {
    "text": "...",
    "citations": [
      {"evidence_id": "ev_01", "path": "src/auth.py", "start_line": 10, "end_line": 31}
    ],
    "incomplete": false
  },
  "usage": {"steps": 4, "tool_calls": 3, "input_tokens": 8300, "output_tokens": 900},
  "error_summary": null,
  "cancel_requested": false
}
```

### `GET /runs/{run_id}/events`

SSE 事件：`run.started`、`step.started`、`tool.completed`、`answer.delta`、`run.completed`、`run.failed`。工具事件默认只暴露摘要，不泄露模型密钥、完整提示词或敏感源码。

客户端可通过 `Last-Event-ID` 或 `?after=<sequence>` 从指定事件继续消费。事件还包括 `run.queued`、`step.completed`、`budget.exhausted`、`run.partial`、`run.cancelled` 和 `run.cancel.requested`。

### `POST /runs/{run_id}/cancel`

请求协作式取消；正在执行的外部调用完成后停止后续步骤。

## 4. Memory

### `GET /repositories/{repository_id}/memories`

参数：`query`、`session_id`、`layer=session|project|module|episodic`、`limit`。接口只返回仓库当前活动索引版本的非 stale Memory；Session Memory 仅对匹配的 `session_id` 可见。每条记录包含来源路径、Evidence ID、Run 来源和置信度，便于检查 Agent 是否错误复用旧分析。

索引任务响应的 `delta` 字段报告 `files_added/files_changed/files_deleted/files_reused` 与 `chunks_embedded/chunks_reused`，可用于验证增量处理是否生效。

## 5. 项目浏览（规划中）

- `GET /repositories/{id}/tree?path=&depth=2`
- `GET /repositories/{id}/symbols?query=login&kind=function`
- `GET /repositories/{id}/symbols/{symbol_id}`
- `GET /repositories/{id}/symbols/{symbol_id}/relations?direction=in&type=CALLS`

## 6. 错误示例

```json
{
  "type": "https://codemind.dev/problems/repository-not-ready",
  "title": "Repository index is not ready",
  "status": 409,
  "detail": "No active index version exists for this repository.",
  "instance": "/api/v1/repositories/repo_01/runs",
  "trace_id": "trace_01"
}
```

## 6. 版本与幂等（目标契约）

- 创建类接口接受 `Idempotency-Key`；
- 响应通过 `X-CodeMind-Index-Version` 暴露使用的索引版本；
- 不兼容变更进入 `/api/v2`，字段新增保持向后兼容；
- OpenAPI schema 由 FastAPI 自动生成，并已纳入契约测试。
