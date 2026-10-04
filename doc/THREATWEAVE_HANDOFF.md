# ThreatWeave 历史交接（已归档）

更新时间：2026-10-03

> 归档说明：本文件以下内容是 2026-10-03 的历史快照，描述的旧异步 A/B 编排、A 自动提交 B 和多个 Agent Protocol 图均已删除。它们不代表“当前实现”，不得用于代码修改、运行、测试或排障。当前实现以 `THREATWEAVE_CONFIRMED_DECISIONS.md`、`INTELLIGENCE_WORKFLOW_REFACTOR_PLAN.md` 和 `PROJECT_DOCUMENTATION.md` 为准。

## 项目背景与目的

本仓库原本是一个摩托车零部件采购 Agent 项目。用户要求将它收敛为 **ThreatWeave**：一个面向公开网络威胁情报的本地多 Agent 工作台。

用户要实现的不是自动处置、封禁或规则引擎，而是一条可追溯的情报生产链：从批准的公开来源获得内容，保留完整且可读的格式化正文；从正文中抽取具有精确原文出处的实体和关系；再只读分析库内数据，生成 HTML 关系图和 Markdown 分析报告。最终系统应让分析人员能区分原文事实、外部背景和分析推断。

项目当前处于“核心边界和最小骨架已落地，真实采集和可靠编排仍待完成”的阶段。不要把可启动的 Agent 图误认为完整生产能力。

## 文档与权威性

| 文档 | 用途 | 权威性 |
| --- | --- | --- |
| `AGENTS.md` | 仓库工作方式、代码规范、测试命令 | 开发规则 |
| `doc/THREATWEAVE_CONFIRMED_DECISIONS.md` | 已确认的职责、数据模型、实体/关系类型和业务边界 | 业务与模型的唯一权威来源 |
| 本文 | 当前实现状态、缺口、验证证据和推荐继续顺序 | 实施交接记录 |
| `README.md` | 面向人的项目概览与启动入口 | 导航说明 |

处理 ThreatWeave 业务前，先读取凿定决策。后续工作只能实现其中已有的范围；若要增加 Agent、规则集、自动黑名单、文档版本库、来源数据库或新的实体/关系模型，必须先获得用户确认。

## 已凿定的目标模型

ThreatWeave 只有三个业务子 Agent，全部异步：

| Agent | 目标职责 | 业务数据权限 |
| --- | --- | --- |
| `intel_ingestor` | 从批准来源获取内容，清除页面噪声，将内容变为完整、清晰的规范正文 | 仅文档与来源元数据 |
| `entity_relation_extractor` | 以模型理解为主，从规范正文提取实体、别名、关系、语义角色和精确出处 | 仅实体、别名、关系、provenance |
| `threat_analyst` | 只读查询图谱，完成关联分析，生成 HTML 图与 Markdown 报告 artifact | 只写 artifact，不写业务表 |

关键约束：

- A 只做格式化，不作实体、关系、IOC 恶意性或风险判断；不保存原始页面、广告、乱码和中间清洗结果。
- B 是实体、别名、关系和出处的唯一写入方。每项写入必须来自格式化文档中的精确引文与字符范围；搜索结果只能辅助消歧或背景，不是写库证据。
- C 的图谱和报告不反向修改业务数据；报告必须清楚标记库内事实、外部背景和推断。
- 同一来源文章再次采集时由稳定 `doc_key` 覆盖同一文档，不保存版本链。
- 初始来源为 CNCERT/CC，最低频率每日一次。来源配置为仓库内 YAML；第一期没有前端来源管理台。
- FastAPI 必须从 HttpOnly Cookie 会话取得身份，不能信任业务请求中的 `user_id`。

实体、语义角色、关系类型及 PostgreSQL DDL 不在本文重复维护，直接以凿定决策为准。

## 2026-10-03 历史实现快照（已废弃）

### 运行链路

`start_web.py` 依次启动 Java、Python MCP、LangGraph Agent Protocol、调度器、FastAPI 和 Vue。当前本机地址如下：

| 服务 | 地址 | 责任 |
| --- | --- | --- |
| Vue | `http://127.0.0.1:19000` | 登录和对话工作台 |
| FastAPI | `http://127.0.0.1:18000` | 认证、会话、聊天、异步任务状态 |
| Java | `http://127.0.0.1:18080` | ThreatWeave PostgreSQL CRUD |
| MCP | `http://127.0.0.1:18081/mcp` | 向 Agent 暴露 Java CRUD |
| Agent Protocol | `http://127.0.0.1:18082` | 三个异步图 |

运行命令：

```powershell
.\.venv\Scripts\python.exe .\start_web.py
```

Python 必须使用仓库根目录下的 `.venv` uv 环境（旧的 `myagent` 环境已废弃）。`start_web.py` 中的 Maven 本地缓存和 Java 临时目录设置已在 Windows 上验证；修改前需要重新验证完整启动。

### 数据与 MCP

- Java 源码位于 `java-backend/src/main/java/com/threatweave/`，只保留 ThreatWeave JDBC CRUD、通用响应/异常、CORS 和 OpenAPI 配置。
- `ThreatWeaveSchemaInitializer.java` 创建 `threatweave` schema 的 `documents`、`entities`、`entity_aliases`、`relations`、`provenance` 表。
- `ThreatWeaveServiceImpl.writeEvidence()` 校验证据的字符范围和引文必须与文档正文严格匹配。
- MCP 注册入口是 `src/mcp_server/server_main.py`，仅注册四个工具：文档 upsert、文档读取、抽取写入、图谱查询。
- Agent 的最小权限工具装配位于 `src/agent/subagents/async_registry.py`：A 只有文档 upsert 和 A→B 交接工具；B 只有文档读取与抽取写入；C 只有图谱查询和本地 HTML 生成工具。

### Agent、Skill 与调度

- 三个 YAML 配置位于 `src/agent/subagents/configs/`；异步入口为 `src/agent/subagents/async_entry.py`；Agent Protocol 注册见 `langgraph.json`。
- A、B、C 的 Skill 分别位于 `src/agent/skills/subagents/` 的同名目录。来源配置是 `intel_ingestor/intel-ingestion/sources/cncert_cc.yaml`。
- `src/scheduler/runner.py` 只读取该单一 YAML，并以 `system-scheduler` 上下文提交 `intel_ingestion_orchestrator_system`。
- `intel_ingestion_orchestrator` 通过单一编排工具完成来源校验、逐篇抓取、初步格式化和按上下文预算分批；每批均调用一次全新的 `intel_ingestor` 格式化图。
- `src/agent/tools/threatweave_task_tools.py` 提供 `submit_entity_extraction(document_id)`，用于在 A 经 Java MCP 写入并取得文档 ID 后创建 B 的异步 run。这仍是模型流程约束，**不是** Java/MCP/调度器提供的事务性事件保证。

### 认证与前端

- 认证位于 PostgreSQL `auth` schema；会话令牌只存 HttpOnly Cookie。
- `src/api/identity.py` 为业务端点提供当前身份；聊天、历史和异步任务已使用该边界。
- 前端已替换为 ThreatWeave 深色控制台主题，入口标题为“ThreatWeave 威胁情报工作台”。

## 2026-10-03 历史验证记录（不适用于当前链路）

本次清理后的验证结果：

```text
Python: 227 tests, OK (2 skipped)
Frontend: npm test, 29 tests passed; npm run build, success
Java: mvn.cmd -DskipTests compile, BUILD SUCCESS
Runtime: 五个本地端口均已监听；Vue 与 FastAPI 根路径返回 200
MCP: Agent Protocol 启动时发现 4 个 ThreatWeave MCP 工具
```

此前还完成过一轮 Java REST 数据链路验证：文档重复 upsert 保持同一 ID；写入 CVE、恶意软件和 `EXPLOITS` 关系成功；图谱查询返回对应节点和边；伪造的证据引文被拒绝。该验证留下了本地开发数据 `e2e-cve-2026-0001`、`CVE-2026-0001`、`ExampleRAT`，未删除，因为用户尚未授权删除数据。

当前启动日志还确认：调度器能提交一次 A 任务，且 A 图可以完成运行。但这只证明编排入口与 Skill 装载可用，**不证明** CNCERT/CC 正文已被真实抓取或写入。

**历史验证（本次职责重构前）**：曾以真实 CNCERT 列表页驱动旧版 `ingest_source`，验证过第 1 页 15 篇文章的确定性清洗与直连入库。该旧链路已移除；当前链路由采集工具把文章草稿交给 A 深度格式化，再由 A 调用 Java MCP 入库，需以新的端到端任务重新验证。

## 2026-10-03 历史缺口与风险（已过期）

这些是后续实现应优先处理的真实缺口，不是可忽略的优化项。

1. **受控来源采集已实现。** `collect_source_documents` 对已登记来源做确定性校验、列表发现、逐篇抓取、编码修复、正文容器提取、基础 Markdown 渲染和稳定 `doc_key` 生成；它不连接数据库。
2. **深度格式化改由 A 执行。** A 的 Skill 规定其逐篇去除广告、导航残留、推荐、页脚、无关信息、乱码和重复内容，并恢复可读 Markdown 结构；A 再调用 Java MCP 入库，MCP 依据最终正文生成 `content_sha256`。
3. **A→B 可靠交接未完成。** 交接目前依赖 A 遵守提示词调用工具。调度器只处理 run 提交失败，不读取 A 或 B 的终态，不能可靠重试“ A 成功而 B 未提交”或“A 执行失败”。
4. **来源管理未完成。** 凿定决策要求通过受控主 Agent 工具列出、新增、启用、停用来源，并在新增/启用前人工确认；当前只有一个静态 YAML。前端来源管理明确后置。
5. **C 的完整交付未验证。** `OPEN_SANDBOX_API_KEY` 未配置，沙箱预热失败后按需降级。尚未真实验证登录用户提交 C 任务、打开 HTML artifact、下载 Markdown 报告以及只读边界。
6. **API 级领域校验未完成。** `ThreatWeaveRequests.java` 仅有基础 Bean Validation；实体类型、语义角色和关系类型主要由 PostgreSQL CHECK 约束拒绝。模型传入不支持值时可能得到通用错误，而非清晰业务错误。

## 2026-10-03 历史推荐顺序（已过期）

1. ✅ 为 CNCERT/CC 实现受来源条款和频率约束的、可测试的获取适配器（已完成，见上“本次更新后新增的真实验证”）。
2. ✅ 在该适配器中实现确定性正文清洗、稳定 `doc_key`、内容 SHA 和文档 upsert（已完成）。
3. 引入可持久化的采集/抽取任务状态或终态轮询，将 A 成功、B 已提交、B 成功/失败作为可恢复状态，而非提示词假设。
4. 给 Java 请求层增加与凿定决策一致的枚举/值域校验；同时为 B 实现长文分段策略和真实样本文档测试。
5. 配置 OpenSandbox，真实执行 C，并验证 HTML 图、报告下载、事实/推断边界和 C 的只读属性。
6. 在上述链路稳定后，再实现经确认的主 Agent 来源管理与人工确认；不要先做前端管理台。

## 历史验收命令（当前以 AGENTS.md 为准）

```powershell
$env:PYTHONPATH = 'src'
.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_*.py'

Set-Location .\frontend
npm test
npm run build
Set-Location ..

mvn.cmd '-Dmaven.repo.local=C:\Users\25144\Desktop\ThreatWeave-Agent\.m2' -f .\java-backend\pom.xml -DskipTests compile
```

修改启动链或前端后，使用 `start_web.py` 并确认五个端口均监听。修改 Java 数据层后，除编译外还应验证文档 upsert、带精确出处的抽取写入、错误引文拒绝和图谱查询。
