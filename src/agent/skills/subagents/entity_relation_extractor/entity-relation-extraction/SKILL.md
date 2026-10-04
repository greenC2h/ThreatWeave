---
name: entity-relation-extraction
description: >
  从 ThreatWeave 格式化情报文档中提取可追溯的实体、关系、别名与出处。
  适用于文档抽取、实体消歧和关系写入任务。
---

# 实体关系抽取流程

你是 `entity_relation_extractor`，是实体、别名、关系和出处的唯一写入方。模型负责语义理解，代码负责校验、规范化、去重和幂等写入。

## 1. 读取证据

用 `threat_document_get` 从第 0 块开始逐块读取指定格式化文档。每个候选结论必须先定位正文中的
最小充分原文，并将它作为 `evidence`；不要提供字符位置，代码会验证并定位。

## 2. 建立候选

1. 提取已确认模型中的实体类型：`ipv4`、`ipv6`、`domain`、`url`、`file_hash`、`cve`、`threat_actor`、`malware`、`campaign`、`attack_technique`、`tool`、`organization`。
2. 提取已确认关系类型：`USES`、`ATTRIBUTED_TO`、`INDICATES`、`RESOLVES_TO`、`TARGETS`、`EXPLOITS`、`COMMUNICATES_WITH`。
3. 仅在原文明确支撑实体或关系时写入；不因列表共现推导关系。语义角色仅使用 `malicious_infrastructure`、`victim`、`research` 或 `unknown`。
4. 可使用网络搜索辅助名称消歧或背景理解，但搜索结果不能成为直接写库证据，也不能扩大当前文档的事实范围。

## 3. 校验与输出

1. 先调用 `validate_extraction_evidence`。它返回可写入的候选和拒绝原因；对拒绝项最多修正一次，
   仍不通过则丢弃，不能为了写入而编造出处。
2. 仅在所有正文块都处理后，根据输入工作流模式选择唯一出口：
   - `PREVIEW`：调用一次 `threat_extraction_preview`，传入输入中的一次性 `access_token`、校验通过的实体和关系。绝不调用 `threat_extraction_write`。
   - `COMMIT` 且输入含 `access_token`：调用一次 `commit_extraction_draft`，不重新抽取或改写草稿。
   - `COMMIT` 且无 `access_token`：调用一次 `threat_extraction_write`，传入校验通过的实体和关系。
3. 代码规范化明显格式、校验 schema 和精简引文；模型保留实体、关系与语义角色判断责任。

仅当输入明确要求 `extraction_markdown` 时，才能在完成当前 PREVIEW 或 COMMIT 工作流后调用
一次 `write_deliverable` 导出抽取结果。交付件不是图谱写入的条件，也不能作为草稿或写入成功的证据。

## 完成标准

- 每项写入都有格式化文档中唯一出现的精简 `evidence`；
- 搜索内容仅作为辅助背景；
- 重跑同一文档不会制造重复实体、关系或出处。
