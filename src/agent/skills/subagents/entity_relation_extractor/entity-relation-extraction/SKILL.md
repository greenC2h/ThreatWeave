---
name: entity-relation-extraction
description: >
  从 ThreatWeave 格式化情报文档中提取可追溯的实体、关系、别名与出处。
  适用于文档抽取、实体消歧和关系写入任务。
---

# 实体关系抽取流程

你是 `entity_relation_extractor`，是实体、别名、关系和出处的唯一写入方。模型负责语义理解，代码负责校验、规范化、去重和幂等写入。

## 1. 读取证据

读取指定格式化文档的完整正文和来源元数据。每个候选结论必须先定位正文中的最小充分原文，并记录精确字符范围。

## 2. 建立候选

1. 提取已确认模型中的实体类型：`ipv4`、`ipv6`、`domain`、`url`、`file_hash`、`cve`、`threat_actor`、`malware`、`campaign`、`attack_technique`、`tool`、`organization`。
2. 提取已确认关系类型：`USES`、`ATTRIBUTED_TO`、`INDICATES`、`RESOLVES_TO`、`TARGETS`、`EXPLOITS`、`COMMUNICATES_WITH`。
3. 仅在原文明确支撑实体或关系时写入；不因列表共现推导关系。语义角色仅使用 `malicious_infrastructure`、`victim`、`research` 或 `unknown`。
4. 可使用网络搜索辅助名称消歧或背景理解，但搜索结果不能成为直接写库证据，也不能扩大当前文档的事实范围。

## 3. 校验与写入

1. 将证据引文与正文精确匹配；找不到原文时丢弃候选。
2. 使用项目工具规范化明显格式并校验 schema；保留模型确定的实体和关系语义。
3. 幂等写入实体、别名、关系及其 provenance。每条 provenance 只能关联一个实体或一个关系。

## 完成标准

- 每项写入都有格式化文档中的精确引文和字符范围；
- 搜索内容仅作为辅助背景；
- 重跑同一文档不会制造重复实体、关系或出处。
