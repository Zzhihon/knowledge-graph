# Knowledge Graph 重构蓝图（2026-04-01）

## 背景证据
- CLI 入口过大：`agents/cli.py` 当前约 1790 行，命令面和 orchestration 混在一起。
- API 入口是薄路由聚合：`agents/api.py` 负责注册大量 router，`agents/api_routes/*` 继续直接调用底层模块。
- 现有核心流程已经是 workflow，而不是通用 agent：
  - 提取：`agents/ingest.py`
  - 检索/RAG：`agents/query.py`、`agents/ask.py`
  - 索引同步：`agents/sync_engine.py`
  - 后处理流水线：`agents/integrate.py:394-530`
  - 课程/RSS/面试等专项流水线：`agents/course_knowledge.py`、`agents/interview.py`、`agents/api_routes/rss.py`
- 现有质量信号说明应先做分层再做 agent 化：
  - `pytest`：20 项里 19 通过、1 失败（fallback mock 配置路径）
  - `ruff check agents tests`：185 个问题
  - `mypy agents tests`：180 个错误

## 一版“非 agent framework”的重构蓝图

### 目标
1. 把“可预测的服务能力”从“流程编排”里拆出来。
2. 让 CLI / API / Web 只做 transport，不承载业务细节。
3. 让 LLM 相关逻辑集中到 infrastructure/application 边界，避免散落在各业务模块。
4. 为后续最小 agent 化预留稳定工具层，而不是直接把全系统改成 agent framework。

### 目标架构（推荐分层）

```text
kg/
  interfaces/
    cli/
    api/
    web/
  application/
    services/
    workflows/
    dto/
  domain/
    entries/
    review/
    graph/
    retrieval/
  infrastructure/
    llm/
    storage/
    sources/
    parsing/
  agent/
    tools/
    policies/
    runtime/
```

### 分层职责

#### 1) interfaces 层
只负责输入输出适配：
- Click 命令
- FastAPI route
- Web DTO / SSE 事件格式

不得直接放：
- LLM prompt
- 检索逻辑
- merge / integrate 规则
- graph/vector 访问细节

#### 2) application/services 层
承载稳定、可复用、可测试的用例：
- `SearchService`
- `AskService`
- `IngestService`
- `SyncService`
- `ReviewService`
- `ExportService`
- `ConversationService`

#### 3) application/workflows 层
承载多步骤流程，但仍然是确定性 workflow：
- `EntryIntegrationWorkflow`
- `RssIngestionWorkflow`
- `CourseProcessingWorkflow`
- `InterviewGenerationWorkflow`
- `ProblemGenerationWorkflow`

#### 4) domain 层
承载核心业务概念与规则：
- Entry metadata / relation model
- review score / due rules
- hybrid retrieval score policy
- relation completion policy
- interview value policy

#### 5) infrastructure 层
承载可替换的实现：
- API client manager / model routing
- Qdrant / SurrealDB / BM25 / chat store
- web/rss/file source adapter
- markdown/frontmatter/json/pdf parser

### 当前代码到目标分层的映射

#### interfaces
- `agents/cli.py` -> `kg/interfaces/cli/*`
- `agents/api.py` -> `kg/interfaces/api/app.py`
- `agents/api_routes/*` -> `kg/interfaces/api/routes/*`

#### application/services
- `agents/query.py` -> `kg/application/services/search_service.py`
- `agents/ask.py` -> `kg/application/services/ask_service.py`
- `agents/ingest.py` -> `kg/application/services/ingest_service.py`
- `agents/sync_engine.py` -> `kg/application/services/sync_service.py`
- `agents/review.py` -> `kg/application/services/review_service.py`
- `agents/radar.py` -> `kg/application/services/radar_service.py`
- `agents/export_entries.py` -> `kg/application/services/export_service.py`
- `agents/history.py` -> `kg/application/services/history_service.py`
- `agents/chat_store.py` 中的会话用例可拆出 `conversation_service.py`

#### application/workflows
- `agents/integrate.py` -> `kg/application/workflows/entry_integration.py`
- `agents/course_knowledge.py` -> `kg/application/workflows/course_processing.py`
- `agents/batch_ingest.py` -> `kg/application/workflows/batch_ingest.py`
- `agents/interview.py` -> `kg/application/workflows/interview_generation.py`
- `agents/problem_generator.py` -> `kg/application/workflows/problem_generation.py`
- `agents/distill.py` -> `kg/application/workflows/distillation.py`

#### domain
- 从 `agents/utils.py`、`agents/review.py`、`agents/link.py`、`agents/history.py` 中抽规则到：
  - `kg/domain/entries/`
  - `kg/domain/review/`
  - `kg/domain/retrieval/`
  - `kg/domain/graph/`

#### infrastructure
- `agents/api_client.py` -> `kg/infrastructure/llm/client_manager.py`
- `agents/embeddings.py` -> `kg/infrastructure/llm/embeddings.py`
- `agents/vector_store.py` -> `kg/infrastructure/storage/vector_store.py`
- `agents/graph_store.py` -> `kg/infrastructure/storage/graph_store.py`
- `agents/bm25_store.py` -> `kg/infrastructure/storage/bm25_store.py`
- `agents/diff_store.py` -> `kg/infrastructure/storage/diff_store.py`
- `agents/sources/*` -> `kg/infrastructure/sources/*`
- `agents/file_converter.py` / `json_utils.py` -> `kg/infrastructure/parsing/*`
- `agents/config.py` -> `kg/infrastructure/config/*`

### 重构原则
1. **先拆边界，不改行为。**
2. **先提 service，再提 workflow。**
3. **所有 CLI/API route 只依赖 service/workflow，不直接依赖 store。**
4. **LLM prompt 统一收敛到 infrastructure 或 application/prompt_builder。**
5. **保留 `agents` 兼容层一段时间，避免一次性大迁移。**

### 推荐迁移阶段

#### Phase 1：建立新骨架，不迁移行为
- 创建 `kg/` 包
- 先迁移 config / dto / storage protocol
- 新老并存，`kg` 先不接入口

#### Phase 2：把稳定服务迁出
优先迁：
- search
- sync
- review
- radar
- export
- history

#### Phase 3：把 workflow 迁出
优先迁：
- integrate
- batch ingest
- course process
- interview generation
- rss pull

#### Phase 4：瘦身 transport
- CLI 各命令改为调用 service/workflow
- API route 改为调用 service/workflow
- SSE/job store 统一抽到 `interfaces/api/jobs/*`

#### Phase 5：兼容清理
- `agents/*` 变成 shim 或逐步删除
- `pyproject.toml` script 改为 `kg = "kg.interfaces.cli.main:cli"`

### 验收标准
- CLI 和 API 层不直接 import `vector_store` / `graph_store` / `api_client`
- `integrate` / `rss` / `course` / `interview` 均具备显式 workflow 边界
- LLM 调用入口集中，不再散落在十几个文件里
- mypy/ruff 错误显著下降，测试可按 service/workflow 分层覆盖

## 一版“最小 agent 化改造方案”

### 核心判断
现在不应把系统整体重写为 LangGraph / CrewAI / AutoGen 这类 agent framework 形态。
更合适的是：

- 底层继续保持 `service + workflow`
- 在其上增加一个很薄的 `agent` 层
- 只让开放式任务使用 agent runtime

### 哪些能力保持非 agent
以下模块继续保持确定性：
- search / ask 的基础检索与上下文装配
- sync
- review / radar
- graph/history
- vector/graph/chat/diff store
- config / parser / source adapter

### 哪些能力最适合最小 agent 化

#### 1. 智能知识整合（首选）
把 `agents/integrate.py:394-530` 升级为：
- agent 负责决定是否执行某一步
- 但每一步仍调用已有 service/tool

最小 loop：
1. 读取 entry
2. 判断需要 route / interview / moc / relation 哪些步骤
3. 调用已有工具
4. 记录结果
5. 输出结构化事件

#### 2. 知识缺口补全
面向更开放的任务，如：
- “帮我补齐 ai-agent 域的知识空白”

agent 做：
- gap 分析
- topic 选择
- source 拉取计划
- 调用 ingest/integrate/sync

#### 3. 会话知识沉淀助手
不是默认自动写库，而是：
- session end 提出 capture 候选
- 用户确认后再写入

### 最小 agent 层结构建议

```text
kg/agent/
  runtime/
    orchestrator.py
    events.py
    checkpoints.py
  tools/
    search_tools.py
    entry_tools.py
    ingest_tools.py
    integrate_tools.py
  policies/
    integrate_policy.py
    capture_policy.py
    gap_fill_policy.py
  prompts/
    integrate_agent.md
    gap_fill_agent.md
```

### agent 层设计原则
1. **agent 不直接碰数据库实现，只调 tool/service。**
2. **agent 不负责 transport。**
3. **agent 必须有 checkpoint / event log / human approval gate。**
4. **写知识库默认需要显式策略门控。**
5. **先做单 agent，后面再考虑 multi-agent。**

### 最小 agent MVP 建议
先只做一个：`KnowledgeIntegrationAgent`
- 输入：entry ids
- 输出：结构化处理结果 + 建议动作
- 可选动作：route / relate / create interview draft / update moc / ask human

本质是把现在固定 phase workflow 升级为“policy-driven workflow”，而不是直接上大框架。

### 为什么这叫“最小 agent 化”
因为：
- 只有开放式任务用 agent
- 核心数据面还是确定性服务
- 没有全局改写现有系统
- 后面接 OMX / MCP 也会更顺

## 按当前代码结构输出具体目录重组建议

### 推荐最终目录树

```text
kg/
  __init__.py
  interfaces/
    cli/
      main.py
      commands/
        ingest.py
        query.py
        ask.py
        sync.py
        review.py
        radar.py
        export.py
        graph.py
        interview.py
        rss.py
    api/
      app.py
      routes/
        search.py
        ingest.py
        sync.py
        entries.py
        graph.py
        interview.py
        rss.py
        conversations.py
        integrate.py
      jobs/
        integrate_jobs.py
        interview_jobs.py
        rss_jobs.py
    web/
      contracts/
        dto.py
        events.py
  application/
    services/
      search_service.py
      ask_service.py
      ingest_service.py
      sync_service.py
      review_service.py
      radar_service.py
      export_service.py
      history_service.py
      conversation_service.py
    workflows/
      entry_integration.py
      batch_ingest.py
      rss_ingestion.py
      course_processing.py
      interview_generation.py
      problem_generation.py
      distillation.py
    dto/
      entries.py
      search.py
      review.py
      interview.py
      jobs.py
  domain/
    entries/
      models.py
      repository.py
      rules.py
    retrieval/
      scoring.py
      filters.py
      query_expansion.py
    graph/
      relations.py
      history.py
    review/
      health_rules.py
      spaced_repetition.py
      radar_rules.py
  infrastructure/
    config/
      loader.py
      models.py
    llm/
      client_manager.py
      embeddings.py
      prompt_builders.py
    storage/
      vector_store.py
      graph_store.py
      bm25_store.py
      chat_store.py
      diff_store.py
    parsing/
      file_converter.py
      frontmatter_io.py
      json_utils.py
    sources/
      base.py
      rss.py
      web.py
      state.py
  agent/
    runtime/
      orchestrator.py
      checkpoints.py
      events.py
    tools/
      knowledge_tools.py
      ingest_tools.py
      review_tools.py
    policies/
      integrate_policy.py
      capture_policy.py
```

### 按你当前文件逐项迁移

#### 第一批（低风险，先迁）
- `agents/config.py` -> `kg/infrastructure/config/loader.py`
- `agents/json_utils.py` -> `kg/infrastructure/parsing/json_utils.py`
- `agents/file_converter.py` -> `kg/infrastructure/parsing/file_converter.py`
- `agents/sources/*` -> `kg/infrastructure/sources/*`
- `agents/api_client.py` -> `kg/infrastructure/llm/client_manager.py`
- `agents/embeddings.py` -> `kg/infrastructure/llm/embeddings.py`
- `agents/vector_store.py` -> `kg/infrastructure/storage/vector_store.py`
- `agents/graph_store.py` -> `kg/infrastructure/storage/graph_store.py`
- `agents/bm25_store.py` -> `kg/infrastructure/storage/bm25_store.py`
- `agents/chat_store.py` -> `kg/infrastructure/storage/chat_store.py`
- `agents/diff_store.py` -> `kg/infrastructure/storage/diff_store.py`

#### 第二批（稳定服务）
- `agents/query.py` -> `kg/application/services/search_service.py`
- `agents/ask.py` -> `kg/application/services/ask_service.py`
- `agents/sync_engine.py` -> `kg/application/services/sync_service.py`
- `agents/review.py` -> `kg/application/services/review_service.py`
- `agents/radar.py` -> `kg/application/services/radar_service.py`
- `agents/export_entries.py` -> `kg/application/services/export_service.py`
- `agents/history.py` -> `kg/application/services/history_service.py`

#### 第三批（workflow）
- `agents/ingest.py` -> `kg/application/services/ingest_service.py` + prompt builder
- `agents/batch_ingest.py` -> `kg/application/workflows/batch_ingest.py`
- `agents/integrate.py` -> `kg/application/workflows/entry_integration.py`
- `agents/course_knowledge.py` -> `kg/application/workflows/course_processing.py`
- `agents/interview.py` -> `kg/application/workflows/interview_generation.py`
- `agents/problem_generator.py` -> `kg/application/workflows/problem_generation.py`
- `agents/distill.py` -> `kg/application/workflows/distillation.py`
- `agents/link.py` -> `kg/domain/graph/relations.py` + service facade
- `agents/backlinks.py` -> `kg/application/services/backlink_service.py`

#### 第四批（transport）
- `agents/api.py` -> `kg/interfaces/api/app.py`
- `agents/api_routes/*` -> `kg/interfaces/api/routes/*`
- `agents/cli.py` -> `kg/interfaces/cli/main.py` + `commands/*`

### CLI 具体拆法
把 `agents/cli.py` 按命令族拆：
- `commands/ingest.py`
- `commands/query.py`
- `commands/ask.py`
- `commands/review.py`
- `commands/stats.py`
- `commands/sync.py`
- `commands/graph.py`
- `commands/interview.py`
- `commands/rss.py`
- `commands/export.py`

公共展示逻辑沉到：
- `kg/interfaces/cli/renderers/`

### API 具体拆法
把 route 保持薄层：
- 每个 route 文件只做 request/response schema + 调 service/workflow
- job store 单独抽到 `interfaces/api/jobs/`
- SSE event 序列化统一抽到 `interfaces/api/sse.py`

### 兼容策略
为避免一次性 break：
- 保留 `agents/__init__.py`
- 保留少量 shim，如：
  - `agents/query.py` 只 re-export `kg.application.services.search_service`
  - `agents/cli.py` 只 import 新 CLI 并转发
- 逐步删 shim

## 优先级排序（建议实际执行顺序）
1. **先处理 secrets 外置**（`config.yaml` 明文 key 问题）
2. 建 `kg/` 新骨架
3. 迁 infrastructure
4. 迁 search/sync/review/ask 服务
5. 拆 CLI / API
6. 迁 integrate / course / interview / rss workflows
7. 再做最小 agent 层

## 近期 3 个最值当的第一步
1. 把 `agents/cli.py` 拆成 `interfaces/cli/commands/*`
2. 把 `agents/api_client.py + embeddings.py + store` 收敛到 `infrastructure/`
3. 把 `agents/integrate.py` 从脚本式流程改成 `EntryIntegrationWorkflow`
