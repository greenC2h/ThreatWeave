# ThreatWeave

ThreatWeave 是一个面向公开威胁情报的多 Agent 工作台。它将来源情报规范化、抽取具备原文出处的实体和关系，并基于库内数据生成可交互图谱与 Markdown 分析报告。

## 当前架构

- `intel_ingestor`：异步采集并保存格式化后的情报正文。
- `entity_relation_extractor`：异步抽取实体、别名、关系及其原文出处。
- `threat_analyst`：异步只读查询图谱，生成 HTML 图和分析报告。
- FastAPI 与 Vue：认证、对话、异步任务和 artifact 展示。
- Java Spring Boot：ThreatWeave 文档、实体、关系和 provenance 的 PostgreSQL CRUD。
- MCP：仅暴露 ThreatWeave 业务 Agent 所需的四个 Java 工具。

已确认的业务边界和数据模型见 [凿定决策](doc/THREATWEAVE_CONFIRMED_DECISIONS.md)。当前实现状态、验证记录和后续任务见 [交接文档](doc/THREATWEAVE_HANDOFF.md)。

## 本地运行

```powershell
.\.venv\Scripts\python.exe .\start_web.py
```

启动后访问 `http://127.0.0.1:19000/`。运行依赖包括 JDK 17、Maven、Node.js、PostgreSQL，以及用于执行 Agent 的 OpenSandbox。

首次运行前用 uv 创建并安装 Python 虚拟环境：

```powershell
uv venv --python 3.12 .venv
uv pip install --python .\.venv\Scripts\python.exe -r requirements.txt
```

## 验证命令

```powershell
$env:PYTHONPATH="src"
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"

Set-Location .\frontend
npm test
npm run build
Set-Location ..

mvn.cmd -f .\java-backend\pom.xml -DskipTests compile
```

认证账号、会话与 ThreatWeave 业务表均在 PostgreSQL 的同 schema 中由代码创建：认证 schema 与 `auth.users` / `auth.sessions` 在 `src/api/auth.py` 首次登录时初始化，`threatweave` 业务表由 Java 后端启动时自动创建。密钥和数据库连接参数只应放在 `.env` 中。
