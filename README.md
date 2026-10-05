# ThreatWeave

ThreatWeave 是一个面向公开威胁情报的工作台。它通过确定性 Pipeline 将来源文章规范化、抽取具备原文出处的实体和关系，并基于库内数据生成图谱与 Markdown 分析报告。

## 当前架构

- `ThreatPipeline`：固定执行采集、批量清洗、文档写入、分块抽取和图谱写入。
- `threat_handle`：同步调用 Pipeline，或查询并导出规范正文、实体和关系。
- `threat_analyst`：异步只读查询图谱，生成 HTML 图和分析报告。
- FastAPI 与 Vue：认证、对话、异步任务和 artifact 展示。
- Java Spring Boot：ThreatWeave 文档和图谱的类型化命令接口，以及受控只读查询接口。
- MCP：只暴露 `describe_read_model` 和 `execute_read_query` 两个只读业务查询工具。

当前架构、运行方式、接口和排障说明见 [项目技术文档](doc/PROJECT_DOCUMENTATION.md)；已确认的业务边界和数据模型见 [凿定决策](doc/THREATWEAVE_CONFIRMED_DECISIONS.md)。

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
