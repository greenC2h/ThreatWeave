# ThreatWeave 已凿定决策

状态：已凿定。本文是 ThreatWeave 当前业务边界、处理语义和数据读写模型的权威来源。

## 职责边界

| 组件 | 执行方式 | 职责 | 业务写入 |
| --- | --- | --- | --- |
| `threat_handle` | 同步 | 调用 Pipeline 导入文章；查询规范正文和该文档的抽取结果；按需交付 Markdown。 | 无直接写入 |
| `ThreatPipeline` | 同步确定性代码 | 采集、批量格式化、文档写入、分块抽取、图谱写入。 | 仅经类型化 Java HTTP 命令接口 |
| `threat_analyst` | 异步 | 对业务数据作高级只读关联分析，按需生成 HTML 图或 Markdown 报告。 | 仅 artifact |
| 调度器 | 常驻进程 | 按来源最小间隔直接执行 Pipeline。 | 无直接写入 |

不存在 A/B 子 Agent、预览草稿、仅格式化、待补抽取或工作流模式选择。模型在 Pipeline 内只负责 JSON 格式化和抽取转换，不能访问业务工具或数据库。

## Pipeline 语义

固定流程为：`采集 -> 按预算分批清洗格式化 -> 写入规范文档 -> 按正文分块抽取 -> 一次性替换图谱事实`。

- 输入必须指定一个已批准 `source_id`，或一个能唯一匹配已批准来源的 `article_url`。
- 每篇文章用稳定 `doc_key` 标识，用采集阶段正文的 SHA-256 判断来源内容是否变化。
- 相同来源、正文未变且上次已完成时跳过全部后续阶段；失败、强制刷新或正文变化时从头运行并覆盖旧内容。
- Java 在正文变化时先删除该文档的旧出处；抽取写入也以本篇文档的完整最新事实替换旧出处。随后删除没有任何出处的关系和不再被关系或出处引用的实体。
- 规范正文是唯一保留的文档正文。不会保存原始杂乱 HTML、清洗中间版本、抽取预览或草稿授权。
- 实体、关系和证据必须由文档原文支持。代码校验引文唯一性并生成字符偏移；不从实体共现推断关系。

## 查询和交付

Python MCP 只包含两个只读工具：

- `describe_read_model()`：返回可查询业务数据集、字段、主键、字段语义、可用 JOIN、schema 版本和示例 SQL。
- `execute_read_query(sql, parameters)`：执行参数化的单条 SELECT 或非递归 WITH SELECT。

查询可以访问 ThreatWeave 业务数据和 Pipeline 状态，不访问认证、用户、会话或系统数据。Java 强制表白名单、单语句 SELECT、JOIN/嵌套深度上限、禁止锁定与危险函数、查询超时、最大行数和返回字节数。

`threat_handle` 用上述查询读出某篇文章的规范正文或其关联实体与关系；用户明确要求 Markdown 文件时，统一调用 `write_deliverable`。不存在独立的 `export_document_markdown`。

## 数据模型

业务 schema 为 `threatweave`：

| 表 | 用途 |
| --- | --- |
| `documents` | 已清洗的规范文章、来源元数据、正文和正文哈希。 |
| `entities` / `entity_aliases` | 规范化实体及其别名。 |
| `relations` | 有向实体关系。 |
| `provenance` | 支撑实体或关系的原文引文与字符范围。 |

Pipeline 运行状态位于 `workflow.document_processing`，仅用于幂等、租约和失败诊断。旧的抽取草稿与授权表会在 Pipeline 初始化时删除。

实体类型限定为 `ipv4`、`ipv6`、`domain`、`url`、`file_hash`、`cve`、`threat_actor`、`malware`、`campaign`、`attack_technique`、`tool`、`organization`。语义角色限定为 `malicious_infrastructure`、`victim`、`research`、`unknown`。关系类型限定为 `USES`、`ATTRIBUTED_TO`、`INDICATES`、`RESOLVES_TO`、`TARGETS`、`EXPLOITS`、`COMMUNICATES_WITH`。
