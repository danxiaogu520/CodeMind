# ADR-0004：采用 LangGraph 作为默认 Workflow Runtime

- 状态：accepted
- 日期：2026-07-20

## 背景

CodeMind 的 Agent 需要多步状态迁移、流式进度、持久化 checkpoint、失败恢复和未来的人工审批。自研这些运行时能力会产生大量与代码理解无关的基础设施工作，但直接让整个系统依赖 LangGraph 又会污染领域模型并削弱可替换性。

## 决策

保留 ADR-0003 的显式状态机设计，并使用 LangGraph `StateGraph` 作为 `WorkflowRuntime` 端口的默认实现。

- `domain` 和 `application` 不导入 LangGraph/LangChain；
- Graph Builder、Checkpointer 和事件转换位于 `infrastructure`；
- 生产使用 PostgreSQL Checkpointer，测试默认使用内存实现；
- `run_id` 映射为 LangGraph `thread_id`；
- LangGraph checkpoint 负责执行恢复，CodeMind Run/Step 负责产品状态和审计；
- Project/Episodic Memory 继续由 CodeMind 管理；
- LLMProvider、AgentTool 和 RetrievalPort 保持框架无关；
- LangSmith 和 Agent Server 不作为必需依赖。

## 后果

CodeMind 可以直接获得 durable execution、streaming 和 interrupt 基础能力，同时保留业务契约与框架解耦。代价是维护一次状态/事件映射，并处理 checkpoint 与产品 Run 数据的双写一致性。所有有副作用节点必须幂等，恢复行为需要真实 PostgreSQL 集成测试。

## 依赖引入策略

在 CM-409 开始实现时，通过 uv 同时加入 LangGraph 和 PostgreSQL Checkpointer，并将准确版本写入 `uv.lock`。架构阶段不提前加入未使用依赖。升级版本必须通过状态快照、crash-resume、stream 顺序和取消语义回归测试。

## 替代方案

- 完全自研 Runtime：控制力强，但 checkpoint、恢复和 streaming 成本过高；
- 全面采用 LangChain/LangGraph 类型：开发快，但领域层严重耦合；
- 自由 ReAct Agent：缺少可预测流程和预算边界；
- 固定单链 RAG：无法覆盖调用关系和复杂分析任务。

