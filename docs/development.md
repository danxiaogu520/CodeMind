# 开发指南

## 环境原则

- Python 版本固定为 3.12，由 `.python-version` 声明；
- 所有 Python 命令经 `uv run` 执行；
- 虚拟环境固定在项目根目录 `.venv`，不得向全局环境安装依赖；
- `uv.lock` 必须提交，CI 和容器使用 `--locked`；
- 正式依赖放 `[project.dependencies]`，开发工具放 `[dependency-groups].dev`。

## 初始化

```bash
uv python install 3.12
uv sync --locked --all-groups
cp .env.example .env
```

添加依赖：

```bash
uv add package-name
uv add --dev package-name
```

不要直接运行 `pip install`，也不要手工修改 `uv.lock`。

## 启动 API

```bash
uv run codemind-api
```

也可以使用 ASGI 模块并启用开发热更新：

```bash
uv run uvicorn apps.api.main:app --reload
```

## 基础设施

```bash
docker compose up -d postgres qdrant
uv run alembic upgrade head
uv run codemind-api
```

完整容器栈：

```bash
docker compose up --build
```

零配置产品演示：

```bash
docker compose up -d --build --wait
docker compose --profile demo run --rm demo
```

本地 CLI 也全部通过项目内 uv 环境运行：

```bash
uv run codemind-demo doctor
uv run codemind-demo import --name sample --source /fixtures/sample_python
uv run codemind-demo ask --repository-id <id> --question "Trace calls to create_token"
```

开发默认值连接 `localhost`；Compose 中的 API 通过环境变量覆盖为服务名 `postgres` 和 `qdrant`。

当前开发基线验证版本为 Docker Engine 29.1.3、Buildx 0.30.1、Compose 2.40.3。测试完成后可运行 `docker compose down` 停止并移除容器；命名数据卷默认保留。

## 质量检查

提交前依次执行：

```bash
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run lint-imports
uv run pytest
```

修复格式：

```bash
uv run ruff format .
```

`lint-imports` 当前强制两条边界：领域层不依赖框架/外围模块，应用层不依赖基础设施或交付层。

## Retrieval benchmark

导入示例仓库并取得 `repository_id` 后运行：

```bash
uv run codemind-eval-retrieval \
  --repository-id <repository_id> \
  --dataset eval/datasets/sample_python.jsonl \
  --min-recall-at-10 0.8
```

Runner 调用公开 Search API，输出 Recall@5/10、MRR、nDCG@10、平均延迟和 P95 延迟；低于门槛时返回非零退出码。

Agent 工作流基准：

```bash
uv run codemind-eval-agent \
  --repository-id <repository_id> \
  --dataset eval/datasets/sample_agent.jsonl \
  --min-completion-rate 0.8
```

该 Runner 覆盖三个显式工作流和 auto routing，输出任务完成率、引用结构有效率、期望路径召回率和平均完成时间。

## 健康检查语义

- `/health/live`：只检查 API 进程是否工作，不访问外部依赖；
- `/health/ready`：并行检查 PostgreSQL 和 Qdrant；启用 Ollama Embedding 时还检查 Ollama，任一必需依赖不可用返回 HTTP 503；
- 每个响应携带 `X-Trace-ID`；合法的调用方 trace id 会被保留。

## 数据库迁移

创建迁移：

```bash
uv run alembic revision --autogenerate -m "describe change"
```

执行迁移：

```bash
uv run alembic upgrade head
```

模型元数据统一由 `codemind.infrastructure.database.metadata` 暴露。新增 ORM 模型后必须确保 Alembic `env.py` 能导入其 metadata。

## 目录依赖方向

```text
interfaces ─┐
            ├──> application ──> domain
infrastructure ┘
```

`domain` 不能导入 FastAPI、Pydantic、SQLAlchemy 或 Qdrant SDK。第三方实现细节只能存在于 `infrastructure` 或 `interfaces`。
