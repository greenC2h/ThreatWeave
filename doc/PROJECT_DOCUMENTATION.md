# ThreatWeave 项目技术文档

本文描述当前代码实现的可运行架构、业务边界、数据处理语义和本地启动方式。业务决策与领域约束以 [THREATWEAVE_CONFIRMED_DECISIONS.md](THREATWEAVE_CONFIRMED_DECISIONS.md) 为准；本文补充代码层面的接口和运行细节。

## 系统概览

ThreatWeave 从已登记的公开来源或用户提供的文章直链采集文章，生成清洗后的规范正文，并抽取带原文证据的实体和关系。FastAPI/Vue 提供认证、会话和对话界面；Java Spring Boot 管理 ThreatWeave 业务数据和类型化 HTTP 接口；Python 负责采集、确定性 Pipeline、Agent、MCP 适配和调度。

```text
已登记来源 / 用户文章 URL
          |
          v
    ThreatPipeline
  采集 -> 分批清洗格式化 -> 文档写入 -> 分块抽取 -> 图谱写入
          |                         |
          +------ Java HTTP --------+
                    |
              PostgreSQL 业务数据
                    |
          Java 受限只读查询接口
                    |
        ThreatWeave 查询 MCP
             |              |
       threat_handle   threat_analyst
          同步             异步
```

系统中不存在 A/B 子 Agent、预览草稿流程、只格式化流程、待补抽取流程或可选工作流模式。文章导入始终由同一个 `ThreatPipeline` 完成固定步骤。

## 运行组件

| 组件 | 默认地址 | 作用 |
| --- | --- | --- |
| FastAPI | `127.0.0.1:18000` | 认证、会话、对话流、交付件下载和异步任务状态接口。 |
| Java Spring Boot | `127.0.0.1:18080` | ThreatWeave 文档、图谱、出处和 Pipeline 状态的写入与查询。 |
| ThreatWeave MCP | `127.0.0.1:18081/mcp` | 向 Agent 暴露两个受限只读业务查询工具。 |
| Agent Protocol | `127.0.0.1:18082` | 托管异步 `threat_analyst`。 |
| Vue/Vite | `127.0.0.1:19000` | 用户工作台。 |
| 调度器 | 不监听端口 | 按来源间隔直接运行 Pipeline。 |

`start_web.py` 依次启动 Java、ThreatWeave MCP、Agent Protocol、调度器（按配置）、FastAPI 和 Vue，并在启动前检查端口是否可用。所有子进程继承项目根目录 `.env` 中的配置。

## ThreatPipeline

入口为 `src/threat_pipeline/pipeline.py` 的 `ThreatPipeline.run`，唯一对外导入工具为 `run_threat_pipeline`。

固定流程如下：

1. 采集：`intel_ingestor` 根据来源配置读取文章列表，或抓取用户提供的单篇文章 URL。来源配置位于 `src/intel_ingestor/sources/*.yaml`。
2. 分批：按最多 3 篇和最多 24,000 个输入字符顺序分批，不截断任何文章。
3. 清洗格式化：`JsonPipelineModel.format_batch` 要求模型为当前批次的每篇文章返回完整 Markdown 和标题。模型只能进行 JSON 格式化转换，不能访问业务数据库或写入工具。
4. 文档写入：`ThreatWeaveCommandGateway` 调用 Java `POST /api/threatweave/documents`，写入或覆盖一篇规范文档。
5. 分块抽取：规范正文按字符块送入抽取模型，结果经过实体类型、关系类型、证据引文和字符范围校验。
6. 图谱写入：汇总所有分块结果后，调用 Java `POST /api/threatweave/extractions`，一次性替换该文档的实体关系出处。

采集阶段可能在内存中生成初步正文，用于后续格式化和来源指纹计算；它不会作为用户可见交付件、预览草稿或独立业务版本保存。最终只保留 Java 中的规范正文。

### 导入目标

`ThreatPipelineRequest` 要求至少提供 `source_id` 或 `article_url`：

- 提供已登记的 `source_id` 时，使用对应来源的专用列表、详情接口或 HTML 解析器，并校验来源已启用且声明许可。
- 只提供 `article_url` 时，使用 `direct_url` 通用 HTML 采集配置，直接抓取该 URL，不要求它匹配来源白名单。
- `source_id` 如果本身是 HTTP(S) URL，工具会将其识别为文章直链；不能再同时提供不同的 `article_url`。

当前仓库登记了 CNCERT/CC 和山石云瞻热点威胁来源。山石来源的 `entry_url` 是详情入口，采集器会直接把它作为待处理文章，并通过配置的详情接口获取 JSON。

### 幂等和重跑

每篇文章使用稳定 `doc_key`：优先由 `source_id + external_id` 生成；没有外部文章标识时使用 `source_id + URL`。采集阶段正文的 SHA-256 保存为 `source_fingerprint`。

- 相同来源、采集正文未变化、上次状态为 `completed`：跳过整条 Pipeline，结果为 `skipped_unchanged`。
- 正文变化、上次失败、上次未完成或请求 `force_refresh=true`：重新执行全部步骤，覆盖规范正文和该文档的抽取事实。
- 同一文章已有活动租约时：返回 `skipped_in_progress`，避免并发重复处理。默认活动租约为 7200 秒，可由 `THREATWEAVE_PIPELINE_RUNNING_LEASE_SECONDS` 调整，最小值为 60 秒。
- 格式化或抽取失败：在 `workflow.document_processing` 中记录失败阶段和受限错误信息，下一次运行从头开始。

正文变化时，Java 先删除该文档旧的 provenance。新的抽取结果写入完成后，删除不再有任何出处的关系，以及既没有出处也不再被关系引用的实体。仍被其他文档支持的共享实体和关系会保留。

## Agent 职责和路由

主 Agent 负责对话路由、公共网络搜索、异步任务提交和交付件登记。业务文章导入和单篇文章中间产物查询委派给同步 `threat_handle`；跨文章查询、图谱关联和高级分析提交给异步 `threat_analyst`。

### threat_handle

`threat_handle` 是本地同步子 Agent，配置在 `src/agent/subagents/configs/threat_handle.yaml`，只有以下工具：

- `run_threat_pipeline`：完整导入文章，一次调用执行固定 Pipeline。
- `describe_read_model`：发现可查询的 ThreatWeave 业务数据集和字段。
- `execute_read_query`：执行受限只读 SQL。
- `write_deliverable`：按用户要求写入 Markdown 交付件。

它只处理用户明确指定的单篇文章中间产物查询。这里的中间产物只有规范正文，以及该文档关联的实体、关系和证据；数据均来自查询接口。它不直接写业务数据库，也不负责全库列表、跨文章统计或高级图谱分析。

### threat_analyst

`threat_analyst` 是独立 Agent Protocol 服务中的异步子 Agent，注册在 `src/agent/subagents/async_registry.py`，图 ID 为 `threat_analyst_async`。它可以：

- 通过只读查询工具分析文档、实体、关系和 provenance；
- 按用户明确要求生成 HTML 网络图；
- 按用户明确要求生成 Markdown 分析报告；
- 使用公共 `web_search` 补充外部背景，并把外部背景和库内事实分开说明。

普通查询、列举和统计默认只返回聊天文本，不自动生成文件或图。HTML 图必须经过 `generate_network_graph_html`，随后使用 `write_deliverable` 登记为交付件。

### 交付件

`write_deliverable` 是统一的文件写入工具，支持 `text/markdown`、`text/html` 和 `application/json`，文件只能写入沙箱的 `/deliverables/` 根目录，内容上限为 4 MiB。工具返回结构化声明，FastAPI 将本轮生成的声明登记到用户会话并生成受用户身份保护的下载入口。

不存在独立的 `export_document_markdown`。单篇文章 Markdown 使用 `threat_handle` 的 `write_deliverable`；异步分析报告和 HTML 图也使用同一个工具。HTML 交付件的受限预览是下载后的安全展示能力，不属于旧的预览草稿流程。

## Java HTTP 接口和查询 MCP

Java Controller 位于 `java-backend/src/main/java/com/threatweave/threatweave/ThreatWeaveController.java`。

### Pipeline 写命令

Pipeline 只使用以下类型化 HTTP 命令：

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `POST` | `/api/threatweave/documents` | 按 `doc_key` 写入或覆盖清洗后的规范文档。 |
| `POST` | `/api/threatweave/extractions` | 原子替换一篇文档的实体、关系和 provenance。 |

Java Controller 还保留文档读取、按 key 读取和旧图谱查询等 REST 方法，以兼容已有调用方。Python Agent 不通过这些通用 REST 方法查询业务数据，而是统一使用下面的查询 MCP。

### 查询 MCP

Python MCP 服务只注册两个工具：

| 工具 | Java 路径 | 用途 |
| --- | --- | --- |
| `describe_read_model` | `GET /api/threatweave/read-model` | 返回 schema 版本、业务数据集、字段语义、主键、JOIN 关系和示例 SQL。 |
| `execute_read_query` | `POST /api/threatweave/read-query` | 执行模型提交的受限参数化 SQL。 |

当前实现的“受控读模型”由 Java 的表白名单和 SQL 策略实现，不是 PostgreSQL 独立视图。允许查询的数据集为：

- `threatweave.documents`
- `threatweave.entities`
- `threatweave.entity_aliases`
- `threatweave.relations`
- `threatweave.provenance`
- `workflow.document_processing`

认证、用户、会话、系统表和其他业务表不在白名单内。

`ThreatWeaveReadQueryPolicy` 只接受单条 `SELECT` 或非递归 `WITH SELECT`，并执行以下限制：

- 只能引用白名单数据集；
- 最多 4 个 JOIN，最多 3 层 SELECT；
- 禁止 `FOR UPDATE`、`FOR SHARE`、递归语句和受阻止函数；
- 使用 `?` 占位符传参，禁止把用户值拼接进 SQL；
- 服务端强制最多 200 行、1 MiB 返回数据和 3 秒 JDBC 查询超时。

查询结果通过 Java 统一包装，MCP 不提供写 SQL、通用 CRUD 或业务表写入能力。

## 数据模型

Java 在启动时确保 `threatweave` schema 和以下业务表存在：

| 表 | 用途 |
| --- | --- |
| `threatweave.documents` | 清洗后的规范文章、来源元数据、正文和正文哈希。 |
| `threatweave.entities` | 按实体类型和规范值唯一化的实体，以及展示名、语义角色和置信度。 |
| `threatweave.entity_aliases` | 实体别名。 |
| `threatweave.relations` | 有向实体关系。 |
| `threatweave.provenance` | 实体或关系对应的证据引文、字符范围和抽取器。 |

Pipeline 的运行状态位于 `workflow.document_processing`，保存 doc key、来源指纹、规范正文哈希、状态、活动租约、失败阶段和时间信息；它不保存模型的中间文本。

Pipeline 初始化会删除已废弃的 `workflow.extraction_drafts`、`workflow.draft_access_grants`、`workflow.formatting_access_grants`，并移除处理表中旧工作流的双阶段状态字段。数据库中不再使用预览草稿或格式化授权状态。

实体类型限定为 `ipv4`、`ipv6`、`domain`、`url`、`file_hash`、`cve`、`threat_actor`、`malware`、`campaign`、`attack_technique`、`tool`、`organization`；语义角色限定为 `malicious_infrastructure`、`victim`、`research`、`unknown`；关系类型限定为 `USES`、`ATTRIBUTED_TO`、`INDICATES`、`RESOLVES_TO`、`TARGETS`、`EXPLOITS`、`COMMUNICATES_WITH`。

## 调度器

`src/scheduler/runner.py` 读取来源目录中的全部登记来源，跳过 `enabled=false` 的来源，对每个启用来源直接构造 `ThreatPipelineRequest` 并同步等待完整 Pipeline。默认每次最多采集 3 篇文章。

成功后按来源的 `minimum_interval_seconds` 再次调度；失败后按 `THREATWEAVE_SCHEDULER_RETRY_SECONDS` 重试，轮询间隔由 `THREATWEAVE_SCHEDULER_POLL_SECONDS` 控制。调度器不创建用户沙箱、异步任务或交付件。

`THREATWEAVE_SCHEDULER_ENABLED=false` 时，`start_web.py` 不启动调度器；启用后需要保证来源配置和网络访问可用。

## 外部 MCP 和配置

主 Agent 和 `threat_analyst` 都可以加载公共网络搜索 MCP。项目从 `MODELSCOPE_BING_SEARCH_MCP_TOKEN` 读取 ModelScope MCP 令牌，并连接 `https://mcp.api-inference.modelscope.net/<token>/mcp`。外部工具的 `bing_search` 在项目边界统一命名为 `web_search`。

缺少令牌或 MCP 连接、工具发现失败时，搜索工具会返回受限降级结果，并提示只能依据已提供的 ThreatWeave 数据回答。该降级状态不会影响 ThreatWeave 查询 MCP。

网络图使用 `MODELSCOPE_CHARTS_MCP_URL` 配置 Charts MCP。Charts MCP 不可用或没有返回可渲染 HTML 时，图谱任务返回明确失败结果，不自行伪造外部图表内容。

## 本地开发和验证

创建环境并安装依赖：

```powershell
uv venv --python 3.12 .venv
uv pip install --python .\.venv\Scripts\python.exe -r requirements.txt
```

项目根目录 `.env` 至少需要配置模型访问、PostgreSQL 和 OpenSandbox 参数。公共网络搜索需要额外配置 `MODELSCOPE_BING_SEARCH_MCP_TOKEN`；调度器是否启动由 `THREATWEAVE_SCHEDULER_ENABLED` 控制。

运行完整服务：

```powershell
.\.venv\Scripts\python.exe .\start_web.py
```

工作台地址为 `http://127.0.0.1:19000/`。启动完整服务需要 Java 17 或更高版本、Maven、Node.js、PostgreSQL、可用的 OpenSandbox 服务和模型访问凭据。

常用验证命令：

```powershell
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"

Set-Location .\frontend
npm test
npm run build
Set-Location ..

mvn.cmd -f .\java-backend\pom.xml -DskipTests compile
```
