# ThreatWeave 项目技术文档

本文描述当前可运行架构。业务约束与数据定义以 [THREATWEAVE_CONFIRMED_DECISIONS.md](THREATWEAVE_CONFIRMED_DECISIONS.md) 为准。

## 系统概览

ThreatWeave 从已批准公开来源导入文章，保留清洗后的规范正文，并抽取带原文证据的实体和关系。FastAPI/Vue 提供用户会话和对话界面；Java Spring Boot 管理 ThreatWeave 业务数据；Python 承担采集、Pipeline、Agent 和 MCP 集成。

```text
已批准来源 / URL
        |
        v
ThreatPipeline
  collect -> format -> document command -> extract -> extraction command
        |                                               |
        +---------------- Java / PostgreSQL ------------+
                              |
                 read-model / read-query MCP
                         |                 |
                threat_handle       threat_analyst (async)
```

## 处理 Pipeline

入口：`src/threat_pipeline/pipeline.py` 的 `ThreatPipeline.run`。

1. `intel_ingestor` 组件读取 `src/intel_ingestor/sources/*.yaml` 中的批准来源，发现和抓取文章，产生基础 Markdown 草稿与稳定 `doc_key`。
2. `threat_pipeline.batching` 按最多三篇和 24,000 输入字符顺序分批，绝不截断单篇文章。
3. `JsonPipelineModel.format_batch` 要求模型返回每篇完整清洗后的 Markdown。模型只能返回 JSON，不能调用工具。
4. `ThreatWeaveCommandGateway` 经 Java `POST /api/threatweave/documents` 写入规范正文。
5. 正文按字符块输入抽取模型。候选实体与关系汇总后校验证据唯一性、类型、关系端点和字符偏移，再经 `POST /api/threatweave/extractions` 一次性写入。

Pipeline 状态在 `workflow.document_processing` 中维护。来源正文哈希不变且状态为 `completed` 时跳过；正文变化、失败或 `force_refresh=true` 时重新完整运行。失败会记录阶段和受限错误文本，供下一轮从头重试。

## Agent 与调度

- `threat_handle` 是同步子 Agent，配置在 `src/agent/subagents/configs/threat_handle.yaml`。它只拥有 Pipeline、受控查询和交付工具。导入文章只能调用一次 Pipeline，不允许自行写库。
- `threat_analyst` 是异步高级分析器。它先发现读模型，再写受限 SQL 查询业务数据；可以按用户明确要求创建报告或关系图，但不修改业务表。
- `src/scheduler/runner.py` 列举全部已登记来源，仅跳过禁用来源，并在来源的 `minimum_interval_seconds` 到期后直接调用 Pipeline。调度器不创建用户沙箱、Agent Protocol 任务或草稿。

## Java 命令和查询接口

Pipeline 使用的类型化写命令：

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `POST` | `/api/threatweave/documents` | 写入一篇清洗后的规范文档。 |
| `POST` | `/api/threatweave/extractions` | 原子替换一篇文档的实体、关系和证据。 |

Python MCP 仅注册如下只读工具：

| 工具 | Java 路径 | 用途 |
| --- | --- | --- |
| `describe_read_model` | `GET /api/threatweave/read-model` | 发现允许数据集、字段、关联与示例查询。 |
| `execute_read_query` | `POST /api/threatweave/read-query` | 执行受限参数化 SQL。 |

读取策略由 `ThreatWeaveReadQueryPolicy` 实施：只接受单条 SELECT/非递归 WITH SELECT，只能引用业务表，最多四个 JOIN、三层 SELECT，禁止锁定、危险函数和非白名单表，结果最多 200 行、1 MiB，语句超时 3 秒。调用方必须用 `?` 占位符传递参数。

Java 仍保留既有 REST 服务方法以避免破坏其他已部署调用方；它们不由 Python MCP 暴露，Pipeline 也不使用通用 CRUD 或自由 SQL 写入。

## 数据和覆盖规则

`threatweave.documents` 保存唯一规范正文。`entities`、`entity_aliases`、`relations` 和 `provenance` 保存跨文章共享的图谱及出处。

文档正文发生变化时，Java 先移除该文档旧出处。抽取写入会先清空该文档出处，再写本轮事实；最后清除没有出处的关系，以及既没有出处也不被关系引用的实体。这样共享实体或关系在仍由其他文档支持时会被保留。

旧工作流的预览草稿和授权表在 Pipeline 的 schema 初始化时删除；它们不再有调用方。

## 本地开发与验证

```powershell
uv venv --python 3.12 .venv
uv pip install --python .\.venv\Scripts\python.exe -r requirements.txt

$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"

Set-Location .\frontend
npm test
npm run build
Set-Location ..

mvn.cmd -f .\java-backend\pom.xml -DskipTests compile
```

运行完整服务：

```powershell
.\.venv\Scripts\python.exe .\start_web.py
```

服务地址为 `http://127.0.0.1:19000/`。需要 JDK 17、Maven、Node.js、PostgreSQL、OpenSandbox 和配置完成的模型访问凭据。
