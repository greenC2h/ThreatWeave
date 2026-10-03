# ThreatWeave 已凿定决策

状态：已凿定。此文件记录 ThreatWeave 已确认的架构决策，是当前业务边界与核心数据模型的唯一权威来源；后续确认的决策应追加到本文件。修改已确认的内容或扩展明确排除的范围，必须获得用户明确确认。

## Agent 职责与写入边界

ThreatWeave 当前确认三个业务 Agent。主 Agent 只负责理解用户请求、选择合适的业务 Agent 并汇总面向用户
的结果；它不直接写入威胁情报业务表。

| 名称 | 执行方式 | 职责 | 可写数据 | 明确禁止 |
| --- | --- | --- | --- | --- |
| `intel_ingestor` | 异步 | 定期拉取已配置的公开情报源，将 HTML、PDF、RSS 或 API 响应格式化为一份完整、清晰、可读的正文。 | 格式化后的文档及其来源元数据 | 不保存广告、导航、乱码等杂乱版本；不提取实体或关系；不判断 IOC 的恶意语义；不维护规则。 |
| `entity_relation_extractor` | 异步 | 以模型的上下文理解为主，从格式化文档提取实体、关系、语义角色和精确出处。 | 实体、别名、关系、出处 | 不采集新情报；不生成分析报告；不维护规则。 |
| `threat_analyst` | 异步 | 查询情报库，进行关联分析，生成交互式 HTML 关系图和 Markdown 分析报告。 | 仅 artifact，例如 HTML 图和 Markdown 报告 | 不写入或修改文档、实体、关系、出处和规则等业务数据。 |

### 已确认的协作规则

- 三个业务 Agent 均通过异步任务执行。
- `intel_ingestion_orchestrator` 是不写业务数据的批处理编排器：它完成来源校验、逐篇抓取、初步格式化，并按“最多三篇且不超过上下文字符预算”把文章顺序分批；每批启动全新的 `intel_ingestor` 上下文。`intel_ingestor` 负责去除广告、导航残留、页脚、推荐、乱码、无关信息和重复内容，并恢复混乱段落、标题、列表和表格。每个来源只保留一份最终格式化正文，不保存清洗前的杂乱内容或中间版本。
- 情报源清单由 `intel_ingestor` 的 Skill 维护。清单必须是代码可校验的结构化配置，至少包含来源标识、入口、解析器类型、最低抓取频率、许可证和所需环境变量名；密钥不得写入 Skill。网络搜索仅用于发现或核验来源，搜索摘要不得直接作为情报正文入库。
- `entity_relation_extractor` 是实体、别名、关系和 provenance 的唯一写入方。模型负责上下文理解、实体和关系判断、语义角色及证据选择；代码只负责引文精确匹配、明显格式规范化、字段校验、去重和幂等写入，不以复杂硬编码规则替代模型判断。
- `entity_relation_extractor` 可使用网络搜索辅助名称消歧或补充背景，但搜索结果不是直接写库证据。每一条写入的实体或关系必须引用格式化文档中的原文。
- `threat_analyst` 的数据库访问为只读。它使用 Skill 约束查询顺序、证据分级、图谱裁剪和报告结构；网络搜索仅作外部背景补充，并在报告中与库内证据和分析推断明确区分。报告和图谱是可清理的交付 artifact，不反向改变情报库。
- `intel_ingestor`、`entity_relation_extractor` 和 `threat_analyst` 均需要各自独立的 Skill。
- `start_web.py` 托管独立的调度器进程。调度器不监听对外端口，入口为 `src/scheduler/runner.py`，负责按照来源配置触发 `intel_ingestion_orchestrator`；主 Agent 对用户指定的已批准来源文章也调用同一编排器。A 成功写入文档后自动提交 `entity_relation_extractor` 异步任务，失败任务必须能够重试。调度任务使用固定的 `system-scheduler` 系统执行上下文，只读挂载 Agent Skill，不借用任何用户的沙箱、会话或记忆。
- 情报源的持久化配置保存在仓库内、由代码校验的结构化文件中。第一期使用 `src/agent/skills/subagents/intel_ingestor/intel-ingestion/sources/cncert_cc.yaml` 中的 CNCERT/CC 公开威胁预警栏目作为唯一来源，最低抓取间隔为每天一次。主 Agent 通过受控工具管理来源的列出、新增、启用和停用；新增或启用来源必须经过人工确认。前端来源管理台后置，并复用同一管理模块。
- 认证迁移到 PostgreSQL 的 `auth` schema，与 `threatweave` 业务 schema 及 LangGraph 持久化 schema 共用同一个 PostgreSQL 服务但保持隔离。所有 FastAPI 业务接口必须从 HttpOnly Cookie 会话获取用户身份，不得信任客户端提交的 `user_id`。

## 核心数据模型

此节是 ThreatWeave 实体、关系、证据和业务 PostgreSQL 表的唯一权威定义。

## JSON 契约

```json
{
  "title": "ThreatWeave Confirmed Decisions",
  "status": "confirmed",
  "version": 1,
  "confirmed_scopes": [
    "agent_responsibilities",
    "core_data_model"
  ],
  "scope": {
    "database_schema": "threatweave",
    "included_tables": [
      "documents",
      "entities",
      "entity_aliases",
      "relations",
      "provenance"
    ],
    "excluded_tables": [
      "sources",
      "subscriptions",
      "ingest_jobs",
      "extract_jobs",
      "doc_versions",
      "doc_blocks",
      "rule_sets",
      "rule_entries",
      "rule_change_log"
    ]
  },
  "storage_boundaries": {
    "documents": "The formatted canonical document body is retained in PostgreSQL. Unformatted source bodies, intermediate cleaning results, versions, and blocks are not retained.",
    "evidence": "Provenance keeps an evidence quote, source reference, and character offsets into the formatted document body.",
    "agent_state": "LangGraph state and memories are outside the threatweave schema. Authentication is stored separately in the PostgreSQL auth schema."
  },
  "entity_types": {
    "observable": [
      "ipv4",
      "ipv6",
      "domain",
      "url",
      "file_hash",
      "cve"
    ],
    "knowledge": [
      "threat_actor",
      "malware",
      "campaign",
      "attack_technique",
      "tool",
      "organization"
    ],
    "canonicalization": {
      "ipv4": "Canonical dotted-decimal address, optionally with CIDR suffix.",
      "ipv6": "RFC 5952 lowercase representation, optionally with CIDR suffix.",
      "domain": "Lowercase hostname without a trailing dot.",
      "url": "Normalized URL while preserving semantically meaningful path and query parameters.",
      "file_hash": "<algorithm>:<lowercase-digest>, for example sha256:... .",
      "cve": "Uppercase CVE-YEAR-SEQUENCE format.",
      "threat_actor": "MITRE ATT&CK group ID when available; otherwise a stable project key.",
      "attack_technique": "MITRE ATT&CK technique or sub-technique ID.",
      "organization": "Normalized organization name."
    }
  },
  "semantic_roles": [
    "malicious_infrastructure",
    "victim",
    "research",
    "unknown"
  ],
  "relation_types": {
    "USES": "An actor, campaign, malware, or tool uses malware, a tool, or an ATT&CK technique.",
    "ATTRIBUTED_TO": "A campaign or malware is attributed to a threat actor.",
    "INDICATES": "An observable supports an association with malware, a campaign, or a threat actor.",
    "RESOLVES_TO": "A domain resolves to an IPv4 or IPv6 address.",
    "TARGETS": "An actor, campaign, or malware targets an organization.",
    "EXPLOITS": "An actor, campaign, or malware exploits a CVE.",
    "COMMUNICATES_WITH": "Malware communicates with a domain or IP address."
  },
  "tables": {
    "documents": {
      "purpose": "The canonical formatted source document used by extraction and provenance; it does not hold unformatted source content or intermediate versions.",
      "key": "doc_key",
      "fields": [
        "doc_key",
        "source_name",
        "external_id",
        "title",
        "url",
        "published_at",
        "content",
        "content_sha256",
        "formatted_at",
        "ingested_at"
      ]
    },
    "entities": {
      "purpose": "Canonical threat-intelligence observables and knowledge entities.",
      "unique_key": ["entity_type", "canonical_value"],
      "fields": [
        "entity_type",
        "canonical_value",
        "display_name",
        "semantic_role",
        "confidence",
        "first_seen_at",
        "last_seen_at",
        "revoked"
      ]
    },
    "entity_aliases": {
      "purpose": "Alternative names for a canonical entity; aliases are not relations.",
      "unique_key": ["entity_id", "alias"],
      "fields": ["entity_id", "alias", "source_url"]
    },
    "relations": {
      "purpose": "Directed canonical relationships between two entities.",
      "unique_key": ["src_entity_id", "dst_entity_id", "relation_type"],
      "fields": [
        "src_entity_id",
        "dst_entity_id",
        "relation_type",
        "confidence",
        "first_seen_at",
        "last_seen_at"
      ]
    },
    "provenance": {
      "purpose": "Evidence that supports one entity or one relation.",
      "constraint": "Exactly one of entity_id or relation_id must be set.",
      "fields": [
        "document_id",
        "entity_id",
        "relation_id",
        "evidence_quote",
        "char_start",
        "char_end",
        "extractor",
        "confidence"
      ]
    }
  }
}
```

## PostgreSQL DDL

以下是已凿定的目标 DDL。落地时必须通过迁移使数据库与此定义一致；本文件不是数据库迁移的执行记录。
它只定义 ThreatWeave 业务表，不修改现有的 LangGraph、认证或其他业务 schema。

```sql
CREATE SCHEMA IF NOT EXISTS threatweave;

CREATE TABLE IF NOT EXISTS threatweave.documents (
    id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    doc_key TEXT NOT NULL UNIQUE,
    source_name TEXT NOT NULL,
    external_id TEXT,
    title TEXT,
    url TEXT,
    published_at TIMESTAMPTZ,
    content TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    formatted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS documents_source_published_idx
    ON threatweave.documents (source_name, published_at DESC);

CREATE TABLE IF NOT EXISTS threatweave.entities (
    id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    entity_type TEXT NOT NULL CHECK (entity_type IN (
        'ipv4', 'ipv6', 'domain', 'url', 'file_hash', 'cve',
        'threat_actor', 'malware', 'campaign', 'attack_technique', 'tool', 'organization'
    )),
    canonical_value TEXT NOT NULL,
    display_name TEXT,
    semantic_role TEXT NOT NULL DEFAULT 'unknown' CHECK (semantic_role IN (
        'malicious_infrastructure', 'victim', 'research', 'unknown'
    )),
    confidence SMALLINT CHECK (confidence BETWEEN 0 AND 100),
    first_seen_at TIMESTAMPTZ,
    last_seen_at TIMESTAMPTZ,
    revoked BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (entity_type, canonical_value)
);
CREATE INDEX IF NOT EXISTS entities_type_value_idx
    ON threatweave.entities (entity_type, canonical_value);

CREATE TABLE IF NOT EXISTS threatweave.entity_aliases (
    id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    entity_id BIGINT NOT NULL REFERENCES threatweave.entities(id) ON DELETE CASCADE,
    alias TEXT NOT NULL,
    source_url TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (entity_id, alias)
);
CREATE INDEX IF NOT EXISTS entity_aliases_alias_idx
    ON threatweave.entity_aliases (alias);

CREATE TABLE IF NOT EXISTS threatweave.relations (
    id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    src_entity_id BIGINT NOT NULL REFERENCES threatweave.entities(id) ON DELETE CASCADE,
    dst_entity_id BIGINT NOT NULL REFERENCES threatweave.entities(id) ON DELETE CASCADE,
    relation_type TEXT NOT NULL CHECK (relation_type IN (
        'USES', 'ATTRIBUTED_TO', 'INDICATES', 'RESOLVES_TO',
        'TARGETS', 'EXPLOITS', 'COMMUNICATES_WITH'
    )),
    confidence SMALLINT CHECK (confidence BETWEEN 0 AND 100),
    first_seen_at TIMESTAMPTZ,
    last_seen_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (src_entity_id <> dst_entity_id),
    UNIQUE (src_entity_id, dst_entity_id, relation_type)
);
CREATE INDEX IF NOT EXISTS relations_src_idx ON threatweave.relations (src_entity_id);
CREATE INDEX IF NOT EXISTS relations_dst_idx ON threatweave.relations (dst_entity_id);
CREATE INDEX IF NOT EXISTS relations_type_idx ON threatweave.relations (relation_type);

CREATE TABLE IF NOT EXISTS threatweave.provenance (
    id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    document_id BIGINT NOT NULL REFERENCES threatweave.documents(id) ON DELETE CASCADE,
    entity_id BIGINT REFERENCES threatweave.entities(id) ON DELETE CASCADE,
    relation_id BIGINT REFERENCES threatweave.relations(id) ON DELETE CASCADE,
    evidence_quote TEXT NOT NULL,
    char_start INTEGER NOT NULL CHECK (char_start >= 0),
    char_end INTEGER NOT NULL CHECK (char_end > char_start),
    extractor TEXT NOT NULL,
    confidence SMALLINT CHECK (confidence BETWEEN 0 AND 100),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK ((entity_id IS NOT NULL) <> (relation_id IS NOT NULL)),
    UNIQUE NULLS NOT DISTINCT (
        document_id, entity_id, relation_id, evidence_quote, char_start, char_end, extractor
    )
);
CREATE INDEX IF NOT EXISTS provenance_document_idx ON threatweave.provenance (document_id);
CREATE INDEX IF NOT EXISTS provenance_entity_idx ON threatweave.provenance (entity_id);
CREATE INDEX IF NOT EXISTS provenance_relation_idx ON threatweave.provenance (relation_id);
```
