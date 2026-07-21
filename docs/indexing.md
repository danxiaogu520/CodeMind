# 代码摄取与索引

## 1. 导入流水线

1. 校验来源并创建 `Repository`、`IndexJob`；
2. 克隆/读取仓库，解析准确的 commit SHA；
3. 遵循 `.gitignore` 与系统 ignore 规则发现文件；
4. 检测语言、二进制、大文件、生成文件和疑似密钥；
5. 对文件内容计算 SHA-256，与活动版本比较；
6. Tree-sitter 解析变化文件，提取符号与关系；
7. 结构化切分，批量 Embedding，写入 BM25/Qdrant；
8. 构建项目树与初始模块摘要；
9. 原子激活新索引版本，记录统计与错误。

## 2. 文件过滤

默认排除：`.git`、依赖目录、构建产物、压缩包、媒体、锁文件、minified 文件、超过配置上限的文件。任何本地路径必须位于配置的 `ALLOWED_REPOSITORY_ROOTS` 内，使用 `resolve()` 后再次校验，防止路径穿越。

敏感内容策略：在进入 Embedding 或远程模型前运行 secret scanner；命中内容默认跳过并记录原因，不在日志中输出原文。

## 3. 语言与符号

统一符号类型：

- `module` / `namespace`
- `class` / `trait` / `interface`
- `function` / `method`
- `constant` / `type_alias`
- `impl`（Rust 特有，但映射到统一关系模型）

`symbol_path` 使用语言友好的完全限定名；无法可靠解析时仍生成文件级 Chunk，并设置 `parse_status=partial`。

## 4. Chunk 设计

Chunk 优先按完整语义单元切分，不按固定 Token 生切：

```text
[repository metadata]
path: src/auth/service.py
language: python
symbol: AuthService.login
kind: method
signature: async def login(self, email: str, password: str) -> Token
imports: ...
doc: ...

<source code>
```

规则：

- 小符号保留完整实现，并附父级类/模块上下文；
- 超长符号按语句块或 AST 子节点切为多个 part，保留相同 `symbol_id`；
- 文件顶部文档、imports、常量形成独立 header Chunk；
- 相邻 Chunk 只保留少量结构上下文，不机械重复大段代码；
- Embedding 文本包含 path、symbol、signature、docstring 和源码；引用展示使用未经包装的源码行。

建议默认上限 800 tokens，重叠 80 tokens；实际值由评测集校准，而不是固定为产品事实。

## 5. 关系提取

MVP 提取以下边：

- `CONTAINS`：文件/类包含符号；
- `IMPORTS`：文件或模块导入另一个模块；
- `CALLS`：调用表达式到可解析符号；
- `EXTENDS` / `IMPLEMENTS`：继承或实现；
- `REFERENCES`：标识符引用。

每条边包含 `confidence` 和 `resolution`（`exact`、`local`、`heuristic`、`unresolved`）。回答调用关系时必须向用户暴露静态分析的不确定性，不能把启发式边表述为确定事实。

## 6. 增量索引

- 文件级 `content_hash` 判断是否重解析；
- Chunk 级 `embedding_hash = hash(normalized_content + model_id + prompt_version)` 判断是否重算向量；
- 模型或切分版本变化会创建新索引版本；
- 同一仓库同一目标 commit 的任务需幂等；
- 重试写入使用 upsert，激活版本使用数据库事务。

## 7. 数据实体

| 实体 | 关键字段 |
|---|---|
| Repository | id, source_type, source_uri, default_branch, active_index_version_id |
| IndexVersion | id, repository_id, commit_sha, parser_version, embedding_model, status |
| SourceFile | id, version_id, path, language, content_hash, parse_status |
| Symbol | id, file_id, qualified_name, kind, signature, start_line, end_line |
| CodeChunk | id, symbol_id?, path, part, text, token_count, content_hash, line range |
| Relation | version_id, source_symbol_id, target_symbol_id?, target_text, type, confidence |
| IndexJob | id, repository_id, status, stage, progress, error_summary, timestamps |

