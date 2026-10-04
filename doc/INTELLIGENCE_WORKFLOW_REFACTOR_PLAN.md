# ThreatWeave 情报处理工作流重构计划

状态：实施中  
创建日期：2026-10-04  
实施原则：本文是本轮重构的实施与验收基线。实现过程中发现与现有代码、框架约束或已验证行为冲突时，先调整本文，再以可运行代码和自动化验证为准。

## 1. 背景与问题

当前实现将来源采集、批次分割、A 深度格式化、A 自动触发 B、B 写入图谱耦合为一条固定链路。这适合定期采集，但不能正确支持以下用户任务：

- 只获取清晰、格式化后的文章。
- 只抽取实体关系并交付草稿，不写入情报库。
- 在已有格式化文档上补做抽取或补做入库。
- 查询已格式化但未完成抽取的文章。

重构目标不是增加更多相互调用的 Agent，而是以一个确定性的工作流模块承接状态、幂等、重试与分支决策。主 Agent 通过本地同步子 Agent 调用该模块并等待结果；A 和 B 只执行各自明确的专业步骤。

## 2. 已确认的目标架构

```text
定期调度器 / 主 Agent
          |
          v
IntelligenceWorkflow（确定性状态机、去重、分批、重试）
          |
          +-- 需要采集时：来源加载与逐篇抓取
          |
          +-- 需要格式化时：A / intel_ingestor
          |       `-- 写入规范文档（Java CRUD）
          |
          +-- 需要抽取时：B / entity_relation_extractor
                  +-- PREVIEW：保存用户草稿，不写图谱
                  `-- COMMIT：写入实体、关系、provenance（Java CRUD）

threat_analyst / C：独立、只读地查询情报库并按请求生成交付件
```

约束：

- A、B 的模型调用保持异步 I/O，但作为同步编排器等待的内部执行步骤；C 保持用户可见的独立异步任务。
- A 与 B 不直接相互提交异步任务；工作流决定是否及何时调用下一阶段。
- `intelligence_workflow_orchestrator` 是主 Agent 的本地同步子 Agent，不注册为 Agent Protocol 图，也不出现在公共异步任务列表。它只调用一次确定性工作流工具并等待其完成。
- 调度器直接调用相同的确定性工作流模块并等待完成；它不创建用户可见的异步任务。
- 工作流工厂必须显式接收 `SandboxBackendProxy`。主 Agent 传入当前用户沙箱；调度器传入 `system-scheduler` 专用沙箱；A/B 不得自行创建内存后端、宿主文件系统映射或临时技能目录。
- 仓库 `src/agent/skills/` 是版本控制下的同步源。每次 Agent 运行前通过 `SandboxSkillsMiddleware` 同步到沙箱 `/skills/`，主 Agent、A、B、C 均只从该沙箱路径读取技能。
- 主 Agent 是意图路由者，不直接写 ThreatWeave 业务表。
- Java 后端继续是 `threatweave.documents` 和图谱数据的 CRUD 边界。
- 工作流运行状态、锁与用户草稿属于运行控制数据，置于独立 `workflow` PostgreSQL schema，由 Python 工作流仓储维护。
- 不保存初步清洗前的杂乱正文。规范格式化正文仍会写入 `threatweave.documents`，以支撑幂等、后续抽取和状态查询；“不入库”仅指不写图谱实体、关系与出处。

## 3. 工作流契约

### 3.1 外部请求

新增统一请求 `IntelligenceWorkflowRequest`：

| 字段 | 说明 |
| --- | --- |
| `mode` | 工作流模式，见下表。 |
| `source_id` | 已批准来源标识，可选。 |
| `article_url` | 来源内的单篇文章 URL，可选。 |
| `document_ids` | 已存在规范文档 ID 列表，可选。 |
| `max_articles` | 本次来源任务最多处理篇数。 |
| `force_refresh` | 是否忽略正常的完成态跳过逻辑。 |
| `actor_id` | 发起用户或 `system-scheduler`。 |
| `requested_deliverables` | 明确要求生成的 Markdown/HTML 交付件；默认空。 |

每个请求只能提供一种目标：来源、来源内 URL，或已有文档 ID。参数校验在工作流入口完成。

### 3.2 工作流模式

| 模式 | 典型入口 | A | B | 写入结果 |
| --- | --- | --- | --- | --- |
| `FORMAT_ONLY` | “清洗这篇/这个来源” | 必要时执行 | 不执行 | 规范文档。 |
| `EXTRACT_PREVIEW` | “提取这篇的实体关系” | 文档不存在或已变更时执行 | `PREVIEW` | 用户草稿，不写图谱。 |
| `INGEST_FULL` | 定期任务；“提取并入库” | 必要时执行 | `COMMIT` | 规范文档和图谱。 |
| `EXTRACT_PENDING` | “提取已格式化未抽取文章” | 不执行 | `COMMIT` | 图谱。 |
| `LIST_PROCESSING` | “有哪些只格式化未抽取文章” | 不执行 | 不执行 | 仅返回状态列表。 |

`threat_analyst` 不属于上述写入链路；它独立处理数据库分析和用户明确请求的 Markdown/HTML 交付件。

### 3.3 状态与幂等规则

每篇文章以来源标识和外部标识构成稳定 `doc_key`。规范正文以 `content_sha256` 标识内容版本。

| 当前状态 | 请求 | 行为 |
| --- | --- | --- |
| 已格式化且已对当前 hash 完成抽取 | 同一目标的 `INGEST_FULL` | 跳过，返回已完成状态。 |
| 已格式化但未抽取、抽取失败或抽取内容过期 | `INGEST_FULL` / `EXTRACT_PENDING` | 仅执行 B。 |
| 抓取到内容 hash 改变 | 任意需要处理的模式 | 重新执行 A，标记旧抽取为过期。 |
| A 失败 | 重试 | 仅重试 A。 |
| B 失败 | 重试 | 复用规范文档，仅重试 B。 |
| 同一文章正在处理 | 并发请求 | 复用进行中的工作项，不重复启动 A/B。 |
| 同一用户、同一 document/hash 有有效预览草稿 | 要求入库 | 复用草稿并执行提交校验；草稿过期或 hash 不同则重新 B。 |

“只被清洗未被格式化”在业务上不成立：A 的输出就是规范格式化文档。界面和查询统一使用“已格式化、未抽取/抽取过期/抽取失败”。

## 4. 数据设计

新增 `workflow` schema，不改变既有 `threatweave` 业务表的所有权：

### 4.1 `workflow.document_processing`

记录规范文档的处理状态，核心字段：

```text
doc_key, source_id, external_id, url, document_id,
source_fingerprint, formatted_content_sha256,
formatting_status, extraction_status, extracted_content_sha256,
current_workflow_id, last_failed_stage, last_error,
formatted_at, extracted_at, created_at, updated_at
```

状态枚举：

- `formatting_status`: `pending`, `running`, `completed`, `failed`
- `extraction_status`: `not_requested`, `pending`, `running`, `completed`, `stale`, `failed`

该表的唯一键是 `doc_key`。工作流以行级锁或条件更新保留处理权，避免并发重复任务。

### 4.2 `workflow.extraction_drafts`

记录仅抽取交付所需的结构化草稿，核心字段：

```text
draft_id, user_id, document_id, content_sha256, payload_json,
status, created_at, expires_at
```

草稿仅属于发起用户；提交图谱前必须重新校验 `content_sha256`。下载型 Markdown/HTML 仍由现有 sandbox artifact 和统一交付入口保管，不从 Markdown 反解析业务数据。

### 4.3 `workflow.draft_access_grants`

仅保存 B 内部工具的一次性短时访问令牌。令牌由工作流绑定 `user_id`、`document_id`、正文哈希，以及预览或草稿提交用途；B 不接收或选择用户、草稿 ID。工具消费令牌后立即删除，防止模型越权复用其他用户的草稿。

## 5. Agent、工具与 Skill 的改造

### 5.1 主 Agent

主 Agent 只依据意图调用：

- 通过同步子 Agent `intelligence_workflow_orchestrator` 提交 `FORMAT_ONLY`、`EXTRACT_PREVIEW`、`INGEST_FULL`、`EXTRACT_PENDING`。
- `list_intelligence_processing`：查询格式化/抽取状态。
- `threat_analyst`：库内分析与交付件生成。

工作流调用直接等待完成并返回结构化摘要。保留现有 `start_async_task`、`check_async_task`、`list_async_tasks`、`cancel_async_task`，供 C 和其他长任务继续使用，不得为本次重构删除或阉割。

同步子 Agent 的实现约束：

- 它只暴露 `run_intelligence_workflow` 和 `list_intelligence_processing` 两个受控工具，并且一次任务只调用其中一个。
- 它不得自行调用 A、B、Java CRUD 或异步任务工具；参数选择完成后由确定性 Python 工作流处理。
- `run_intelligence_workflow` 是 `async` 服务函数，但工具会等待它完成并返回完整结果；这里的异步只用于非阻塞 I/O，不改变用户可见的同步语义。
- 调度器绕过主 Agent 和同步子 Agent，直接调用同一个服务函数并以 `system-scheduler` 身份记录状态。
- 同步子 Agent 调用工作流工具时，工具闭包绑定主 Agent 已取得的用户沙箱代理；这使 A/B 的文件系统、命令和 Skill 访问始终留在同一用户沙箱。

### 5.2 编排与 A

- 已删除旧的 `intel_ingestion_orchestrator` 远程图；来源发现和上下文分批由 `IntelligenceWorkflow` 调用 `intel_ingestor` 模块完成，不再由模型决定业务分支。
- 保留“最多三篇且不超过字符预算”的批次算法；每一批 A 都使用新上下文。
- A/B 内部运行器使用 `CompositeBackend(default=sandbox_backend, routes={})`。它只在当前沙箱同步并发现对应 `/skills/`，不再将仓库目录映射为虚拟文件系统。
- A 工具只包含来源输入验证、格式化后的规范文档写入，以及受控状态回报。删除 A 成功后自动调用 B 的工具和 Skill 指令。
- A Skill 只描述完整内容保留、广告/导航/乱码去除、结构恢复与最终文档写入；禁止实体关系判断。

### 5.3 B

- B 仍以模型判断为主，代码承担分块、字段校验、证据唯一匹配、去重和受控写入。
- B Skill 明确两个工作流：
  - `PREVIEW`: 读取规范文档，抽取、验证并保存结构化用户草稿；绝不写入图谱。
  - `COMMIT`: 使用有效草稿或重新抽取、验证后，原子写入实体、关系和 provenance。
- B 的工具按职责拆分为读取文档、验证草稿、保存草稿、提交抽取；不允许通过自由文本决定是否落库。

### 5.4 C 与交付件

- C 保持只读分析边界。
- C 与 A/B 一样使用沙箱同步后的 Skill；用户任务连接用户沙箱，调度任务不存在 C 调用路径。
- 抽象统一的受控交付工具 `write_deliverable`，供 A/B/C 在用户明确要求报告或下载时使用。
- 第一阶段复用现有 artifact 登记与下载协议，避免改动前后端传输边界；随后可在不改变下载接口的前提下替换内部协议。

## 6. 代码改造清单

| 状态 | 位置 | 改造 |
| --- | --- | --- |
| 已完成 | `src/intelligence_workflow/` | 已实现请求/状态 schema、PostgreSQL repository、Java 文档网关、确定性状态机和 A/B 内部执行接口。 |
| 已完成 | `src/intel_ingestor/orchestrator.py` | 已收敛为纯分批和 A 输入构造；不再包含 A→B 或总编排逻辑。 |
| 已完成 | 旧 `intel_orchestrator_tools.py`、`threatweave_task_tools.py` | 已删除，避免绕过统一工作流。 |
| 已完成 | `src/agent/main_agent.py`、同步子 Agent 配置 | 已注册 `intelligence_workflow_orchestrator` 为主 Agent 的本地同步子 Agent。 |
| 已完成 | `src/agent/subagents/async_registry.py`、`async_entry.py`、`langgraph.json` | 已移除旧编排器、A、B 的远程图；C 保持唯一公共异步业务 Agent。 |
| 已完成 | `src/scheduler/runner.py` | 已直接等待 `INGEST_FULL`，不再通过 Agent Protocol 提交旧总编排器。 |
| 已完成 | A/B 内部运行器、工作流工具与调度器 | 工作流显式绑定调用方沙箱；A/B 从沙箱 `/skills/` 读取技能；调度器持久化使用 `system-scheduler` 专用沙箱。 |
| 已完成 | `src/agent/memory/prompts.py`、`src/agent/memory/AGENTS.md` | 已更新主 Agent 路由规则。 |
| 已完成 | A/B 配置与 Skill | A 已移除自动串联；B 已增加 PREVIEW、草稿提交和 COMMIT 约束。 |
| 已完成 | Java Controller/Service/DTO | 已增加按 `doc_key` 解析规范文档的受控查询接口。 |
| 已完成 | `src/mcp_server/tools/` | 已提供预览草稿保存和草稿提交工具。 |
| 待开始 | 统一 `write_deliverable` 工具及下载登记适配 | C 保持现有下载协议；A/B 的用户明确报告请求尚未统一接入 artifact 交付。 |
| 不适用 | `src/api/async_tasks.py` | 工作流改为同步子 Agent，不进入异步任务查询契约；原有异步任务接口未改动。 |
| 待开始 | `frontend/` | 现有聊天入口已可调用工作流；任务页状态展示属于独立 UI 增强。 |
| 已完成 | `doc/THREATWEAVE_CONFIRMED_DECISIONS.md` | 本轮实现后同步新的工作流和状态存储边界。 |
| 已完成 | `doc/PROJECT_DOCUMENTATION.md` | 本轮实现后同步当前架构说明。 |

## 7. 实施顺序与验收

1. **状态底座**：新增 schema、状态枚举、仓储和目标校验；验证建表、状态迁移、并发占用。
2. **来源与文档解析**：补齐 Java 受控查询接口和 Python 文档网关；验证现有规范文档可被工作流定位。
3. **A/B 解耦**：删除 A 自动提交 B；验证 A 只写规范文档，B 可独立处理指定文档。
4. **确定性工作流**：实现五种模式、去重、内容变更失效、阶段性重试和草稿复用。
5. **Agent 接入**：注册同步编排子 Agent，改造 A/B 内部执行入口、主 Agent 指令与调度器；保留现有异步任务查询接口给 C 和其他长任务。
6. **交付件与前端**：接入统一交付工具和最小状态展示。
7. **回归验证**：更新确认文档和项目文档，运行 Python、前端、Java 检查，并用真实页面分别验证定期 `INGEST_FULL`、用户 `FORMAT_ONLY`、`EXTRACT_PREVIEW`、草稿后 `COMMIT`、`EXTRACT_PENDING`、C 的分析交付。

## 8. 必测场景

- 同一文章已成功格式化并完成图谱抽取，再次定期任务不重复执行。
- 正文 hash 改变后，重新格式化并使旧抽取状态为 `stale`。
- A 失败时只重试 A；B 失败时只重试 B。
- 同一文章的并发请求只保留一个有效执行者。
- `EXTRACT_PREVIEW` 不写实体、关系或 provenance。
- 同一用户同 hash 草稿可提交；草稿过期、换用户或内容变化后不可提交。
- `EXTRACT_PENDING` 只处理已格式化而未完成或已过期抽取的文档。
- `check_async_task`、`list_async_tasks`、`cancel_async_task` 保持原有行为。
- FastAPI 从 Cookie 获取身份，不接受请求体 `user_id` 作为身份边界。

## 9. 本轮调整记录

| 日期 | 调整 | 原因 |
| --- | --- | --- |
| 2026-10-04 | 创建计划，进入第 1 步“状态底座”。 | 将固定 A→B 链路改为支持用户意图分支的统一工作流。 |
| 2026-10-04 | 编排器改为主 Agent 的同步子 Agent。 | 用户请求必须等待处理完成；编排器无需作为独立远程异步图注册。 |
| 2026-10-04 | 完成工作流请求与状态模型的单元测试。 | 已验证来源、单文档、查询与冲突目标的输入边界；持久化接入尚未完成。 |
| 2026-10-04 | 细化同步子 Agent 的工具与调用边界。 | 编排器是薄的意图适配层，确定性状态迁移不交给模型。 |
| 2026-10-04 | 完成同步工作流核心、A/B 解耦和草稿复用提交。 | 用户文章处理等待完整结果；C 和通用异步任务能力保持独立。 |
