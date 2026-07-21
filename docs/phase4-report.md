# Phase 4 验收报告：Memory 与增量索引

## 交付边界

Phase 4 将代码知识库与分析记忆严格分离：源码、符号、关系和 Chunk 属于不可变 Index Version；Working Memory 属于 LangGraph Checkpoint；Session、Project、Module 与 Episodic Memory 由 CodeMind 数据模型管理。任何 Run 在创建时固定 `index_version_id`，执行过程中不会跨版本读取。

## 增量索引算法

1. 对发现文件的规范化路径与 SHA-256 内容哈希进行比较；
2. 分类为 added、changed、deleted、unchanged；
3. 只解析并 Embedding added/changed 文件；
4. 将 unchanged 文件、符号、关系、Chunk 和 Qdrant 向量复制到 building version；
5. 使用新版本完整 Chunk 集生成临时 BM25 文件并原子替换；
6. 重基或失效 Memory，生成新 Project/Module 摘要；
7. 在一个数据库事务中将版本标记 ready 并切换仓库 active version；
8. 回收超出保留数且未被 Agent Run 引用的旧版本及外部索引。

building version 不会被 Search 或 Agent 读取。外部索引失败时旧 active version 保持可用；版本回收失败只记录错误，不会反向破坏已经激活的新版本。

## Memory 失效矩阵

| Layer | 版本变化行为 | 召回限制 |
|---|---|---|
| Working | 随 Checkpoint 固定在单次 Run | 上下文超阈值时压缩，保留 Evidence ID |
| Session | 来源均未变化时复制，否则 stale | repository + active version + session_id |
| Project | 旧版本 stale，新版本重新生成 | repository + active version |
| Module | 旧版本 stale，新版本按目录重新生成 | repository + active version + query relevance |
| Episodic | 来源均未变化时复制，否则 stale | repository + active version + query relevance |

没有 Evidence 来源的模型推断不会被持久化为 Session/Episodic 事实。Memory 内容作为不可信上下文注入，不能绕过工具白名单或引用校验。

## 可复现实测

容器 fixture 第一次索引 3 个 Python 文件：

```text
files_added=3, chunks_embedded=5
```

只修改 `src/auth.py` 后再次索引：

```text
files_changed=1, files_reused=2,
chunks_embedded=2, chunks_reused=3
```

新版本 Search 正确返回更新后的 `login` 行号与源码；旧 Project/Module/Session/Episodic 全部标记 stale，当前 Memory API 只返回新生成的 Project/Module。后续 Agent Run 的持久化 SSE 包含：

```text
memory.recalled
memory.compacted
memory.persisted
```

## 质量结果

- Ruff：通过；
- Pyright strict：0 errors / 0 warnings；
- import-linter：2 contracts kept；
- Pytest：44 passed；
- Alembic：从空数据库升级到 0006，模型无漂移；
- Docker：API、Worker、PostgreSQL、Qdrant healthy；
- 安全边界：无新增 shell、代码执行、任意文件读取或写仓库工具。
