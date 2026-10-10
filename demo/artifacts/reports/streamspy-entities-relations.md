# StreamSpy 木马分析文章 — 实体与关系导出

## 一、文章标识

| 项目 | 值 |
|---|---|
| 标题（库内 `title`） | 团伙背景（注：实为文内首个小节名，存在标题定位偏差） |
| URL | https://ti.qianxin.com/blog/articles/analysis-of-streamspy-a-new-trojan-using-websocket-by-patchwork-cn/ |
| 文档 ID | 6 |
| doc_key | 5c3be84d8e825ac0fc47f1769462a2c503b2d9fe4569a6260edf1d61e247aba2 |
| 来源 | direct_url（用户直链文章） |
| external_id | analysis-of-streamspy-a-new-trojan-using-websocket-by-patchwork-cn |
| 发布时间 | 2025-12-01T16:00:00Z |
| 正文长度 | 8192 字符 |

> 说明：上述内容仅覆盖该单篇文章。库内该文档 `title` 字段为「团伙背景」，与文章实际主题不符（推测为清洗时误取首个小节名），但正文内容完整；本交付件据实标注，不做修正。

## 二、实体清单（共 23 个）

| # | 实体 ID | 实体类型 | 规范值 (canonical_value) | 展示名称 | 语义角色 | 置信度 | 别名 |
|---|---|---|---|---|---|---|---|
| 1 | 79 | malware | StreamSpy | 缺失 | unknown | 缺失 | 无 |
| 2 | 80 | malware | Spyder | 缺失 | unknown | 缺失 | 无 |
| 3 | 77 | threat_actor | 摩诃草 | 缺失 | unknown | 缺失 | 无 |
| 4 | 78 | threat_actor | 肚脑虫 | 缺失 | unknown | 缺失 | 无 |
| 5 | 99 | threat_actor | APT 南亚地区 PATCHWORK DONOT | 缺失 | unknown | 缺失 | 无 |
| 6 | 82 | file_hash | f78fd7e4d92743ef6026de98291e8dee | 缺失 | unknown | 缺失 | 无 |
| 7 | 84 | file_hash | c3c277cca23f3753721435da80cad1ea | 缺失 | unknown | 缺失 | 无 |
| 8 | 85 | file_hash | e4a7a85feff6364772cf1d12d8153a69 | 缺失 | unknown | 缺失 | 无 |
| 9 | 86 | file_hash | 0fe90212062957a529cba3938613c4da | 缺失 | unknown | 缺失 | 无 |
| 10 | 87 | file_hash | df626ce2ad3d3dea415984a9d3839373 | 缺失 | unknown | 缺失 | 无 |
| 11 | 88 | file_hash | 838e4d85346001dd04e11359b04c7c24 | 缺失 | unknown | 缺失 | 无 |
| 12 | 81 | file_hash | 1c335be51fc637b50d41533f3bef2251 | 缺失 | unknown | 缺失 | 无 |
| 13 | 83 | file_hash | e0ac399cff3069104623cc38395bd946 | 缺失 | unknown | 缺失 | 无 |
| 14 | 89 | url | wss://www.mydropboxbackup[.]com/analytics/stream | 缺失 | malicious_infrastructure | 缺失 | 无 |
| 15 | 90 | url | wss://www.virtualworldsapinner[.]com/metrics/stream | 缺失 | malicious_infrastructure | 缺失 | 无 |
| 16 | 91 | url | wss://www.virtualworldsapinner[.]com/insights/stream | 缺失 | malicious_infrastructure | 缺失 | 无 |
| 17 | 92 | url | hxxps://www.mydropboxbackup.com/analytics/ | 缺失 | malicious_infrastructure | 缺失 | 无 |
| 18 | 93 | url | hxxps://www.virtualworldsapinner.com/metrics/ | 缺失 | malicious_infrastructure | 缺失 | 无 |
| 19 | 94 | url | hxxps://www.virtualworldsapinner.com/insights/ | 缺失 | malicious_infrastructure | 缺失 | 无 |
| 20 | 95 | url | hxxp://azureinternalupdates.com/getData | 缺失 | malicious_infrastructure | 缺失 | 无 |
| 21 | 96 | url | hxxp://azureinternalupdates.com/getfilename | 缺失 | malicious_infrastructure | 缺失 | 无 |
| 22 | 97 | url | hxxp://azureinternalupdates.com/download | 缺失 | malicious_infrastructure | 缺失 | 无 |
| 23 | 98 | url | hxxps://scrollzshare.info/eeCetyUo8Tr | 缺失 | malicious_infrastructure | 缺失 | 无 |

### 实体类型分布

| 实体类型 | 数量 |
|---|---|
| file_hash | 8 |
| url | 10 |
| malware | 2 |
| threat_actor | 3 |
| **合计** | **23** |

> 说明：库内实体表的 `display_name`、`confidence`、`first_seen_at`/`last_seen_at` 对该文档抽取实体均为缺失；`semantic_role` 除 URL 类为 `malicious_infrastructure` 外均为 `unknown`。`entity_aliases` 表中未记录本文章任一实体的别名（0 条），故别名列统一标注为「无」；文章正文中提及的摩诃草别名（Patchwork、白象、Hangover、Dropping Elephant、APT-Q-36）仅出现在证据引文中，未被写入别名表。

## 三、关系清单（共 8 条）

| # | 关系 ID | 源实体 | 源类型 | 关系类型 | 目标实体 | 目标类型 | 置信度 |
|---|---|---|---|---|---|---|---|
| 1 | 29 | StreamSpy | malware | ATTRIBUTED_TO | 摩诃草 | threat_actor | 缺失 |
| 2 | 30 | StreamSpy | malware | INDICATES | f78fd7e4d92743ef6026de98291e8dee | file_hash | 缺失 |
| 3 | 31 | StreamSpy | malware | INDICATES | c3c277cca23f3753721435da80cad1ea | file_hash | 缺失 |
| 4 | 32 | StreamSpy | malware | INDICATES | e4a7a85feff6364772cf1d12d8153a69 | file_hash | 缺失 |
| 5 | 33 | Spyder | malware | ATTRIBUTED_TO | 摩诃草 | threat_actor | 缺失 |
| 6 | 34 | Spyder | malware | INDICATES | 0fe90212062957a529cba3938613c4da | file_hash | 缺失 |
| 7 | 35 | Spyder | malware | INDICATES | df626ce2ad3d3dea415984a9d3839373 | file_hash | 缺失 |
| 8 | 36 | Spyder | malware | INDICATES | 838e4d85346001dd04e11359b04c7c24 | file_hash | 缺失 |

### 关系类型分布

| 关系类型 | 数量 |
|---|---|
| INDICATES | 6 |
| ATTRIBUTED_TO | 2 |
| **合计** | **8** |

> 说明：`relations` 表为有向关系（src → dst）。库内 `confidence` 字段对上述 8 条关系均为缺失；关系本身未存储独立描述字段，方向由源/目标实体体现。

## 四、统计与数据完整性

| 指标 | 数值 |
|---|---|
| 实体总数 | 23 |
| 关系总数 | 8 |
| 实体别名记录数 | 0 |
| 证据记录数（provenance） | 31（实体证据 23 + 关系证据 8） |

数据完整性说明：

- 实体与关系数量与导入时抽取结果一致（23 实体 / 8 关系），均已完整读取。
- 库内缺失字段：实体的 `display_name`、`confidence`、`first_seen_at`、`last_seen_at`，关系的 `confidence`，以及实体别名（`entity_aliases` 为空）。以上均如实标注为「缺失/未知」，未做任何推测填充。
- 实体 78（肚脑虫）与 99（APT 南亚地区 PATCHWORK DONOT）虽被抽取，但在本次导出的关系列表中未作为源或目标出现（孤立实体）。
- `title` 字段存在标题定位偏差，本交付件不据此编造文章标题，仅原样记录并加注说明。

---

*数据来源：ThreatWeave 业务数据集（documents / entities / entity_aliases / relations / provenance），文档 id=6。导出范围仅限该单篇文章，不含跨文章扩展与关联分析。*
