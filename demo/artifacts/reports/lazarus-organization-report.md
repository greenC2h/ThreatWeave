# Lazarus 组织情报分析报告（ThreatWeave 库内）

## 分析范围

- **分析对象**：威胁组织 `Lazarus`（库内实体 ID 61，`entity_type = threat_actor`）。
- **关联对象**：`APT-Q-1`（库内实体 ID 62，`entity_type = threat_actor`），按任务要求一并复核。
- **数据范围**：ThreatWeave 情报库全库，未施加时间、来源或风险等级过滤。查询覆盖 `documents`、`entities`、`relations`、`provenance`、`entity_aliases` 全部业务数据集。
- **要回答的问题**：库内与 Lazarus 相关的文章、实体、关系与攻击手法证据分别是什么；Lazarus 与 APT-Q-1 的同一性在库内能否判定。
- **交付形式**：Markdown 报告（按要求不生成 HTML、图表或可视化）。

## 结论概览

1. 库内与 Lazarus 直接相关的**文章仅 1 篇**（文档 ID 5），即《故障修复之下的陷阱：Lazarus（APT-Q-1）近期利用 ClickFix 手法的攻击分析》，来源为"用户直链文章"，发布于 2025-08-27（UTC）。
2. 库内 Lazarus 作为独立实体被抽取出 **3 条有向关系**，全部为 `USES` 类型，指向 `ClickFix`（tool）、`BeaverTail`（malware）、`InvisibleFerret`（malware），且 3 条关系均有同文档 evidence_quote 支持。
3. 库内另有 3 条关系连接 Lazarus 的下游实体与其 C2 基础设施（`BeaverTail`→2 个 IPv4、`drvUpdate.exe`→1 个 IPv4），构成"组织—工具/恶意软件—基础设施"两跳链。
4. `APT-Q-1`（实体 62）在库内**没有任何关系边**（`src_entity_id`/`dst_entity_id` 命中数为 0），其唯一证据是与 Lazarus 出现在同一句话中的实体引文。
5. **Lazarus 与 APT-Q-1 的同一性在库内无法唯一判定**：`entity_aliases` 表为空（0 行），无别名数据桥接两个实体；两个实体仅因同段原文被分别抽取，"同指一个组织"属于原文表述，而非库内可结构化验算的结论。详见"关联分析与推断"。
6. 库内**未发现** Lazarus 的 TARGETS/EXPLOITS/ATTRIBUTED_TO 关系、受害者组织、CVE 编号，也未发现"归属 Lazarus"以外的第三方归因证据。

## 库内事实与证据

### 1. 相关文章清单

以下为库内与 Lazarus 直接相关的全部文章（按 `published_at` 升序）：

| 文档 ID | 标题 | 来源（source_name） | 发布时间（published_at, UTC） | 与 Lazarus 的关联证据 |
| --- | --- | --- | --- | --- |
| 5 | 故障修复之下的陷阱：Lazarus（APT-Q-1）近期利用 ClickFix 手法的攻击分析 | 用户直链文章 | 2025-08-27T16:00:00Z | 标题及正文含 Lazarus、APT-Q-1；prov id 83/84 等实体证据即出自本文 |

**检索依据**：`documents.content` 全文匹配 `lazarus` 得到 1 篇（ID 5）；全文匹配 `apt-q-1` 同样得到 1 篇（ID 5）。库内共存 4 篇文档（ID 1、2、5、6），其余 3 篇（NightHeron 测试情报、山石云瞻 Chrome/Windows 零日、摩诃草 StreamSpy）正文中不含 Lazarus 或 APT-Q-1。

> 备注：文档 5 的 URL 为 `https://ti.qianxin.com/blog/articles/analysis-of-apt-q-1-recent-attacks-using-clickfix-cn/`，正文标注作者为"红雨滴团队"，`source_name` 字段登记为"用户直链文章"。

### 2. 相关实体

**2.1 核心威胁组织实体**

| 实体 ID | entity_type | canonical_value | semantic_role | confidence | first_seen_at | last_seen_at |
| --- | --- | --- | --- | --- | --- | --- |
| 61 | threat_actor | Lazarus | unknown | 缺失(NULL) | 缺失(NULL) | 缺失(NULL) |
| 62 | threat_actor | APT-Q-1 | unknown | 缺失(NULL) | 缺失(NULL) | 缺失(NULL) |

两实体的 `updated_at` 均为 2026-10-09T05:56:06Z，`display_name` 均为 NULL。

**2.2 与 Lazarus 直接关联的实体（依据 relations 与 provenance）**

| 实体 ID | entity_type | canonical_value | 与 Lazarus 的关系 | 支持证据（文档 5 引文） |
| --- | --- | --- | --- | --- |
| 66 | tool | ClickFix | Lazarus —USES→ ClickFix（关系 23） | "Lazarus 在以虚假招聘为诱饵的钓鱼攻击中融入 ClickFix 手法" |
| 63 | malware | BeaverTail | Lazarus —USES→ BeaverTail（关系 24） | "执行 Lazarus 组织常用的 BeaverTail 恶意软件" |
| 64 | malware | InvisibleFerret | Lazarus —USES→ InvisibleFerret（关系 25） | "Lazarus 后续下载 Python 木马 InvisibleFerret 的常用保存路径" |
| 65 | malware | drvUpdate.exe | 库内未与 Lazarus 直接连边；仅作为文档 5 实体出现 | "攻击者还会运行一个具有命令执行、读写指定文件功能的后门 drvUpdate.exe" |

**2.3 与 Lazarus 实体同在文档 5 中抽取出的其他实体（IOC 类）**

| 实体 ID | entity_type | canonical_value |
| --- | --- | --- |
| 67 | file_hash | 17eb90ac00007154a6418a91bf8da9c7 |
| 68 | file_hash | 5e698d6f14e10616b0dbb1496e574a91 |
| 69 | file_hash | d9fb02481d1df9f93b7d8e84dc7e097f |
| 70 | ipv4 | 45.159.248.110 |
| 71 | ipv4 | 45.89.53.54 |
| 72 | ipv4 | 103.231.75.101 |
| 73 | url | hxxp://45.159.248.110/client/xyz2 |
| 74 | url | hxxp://45.159.248.110/payload/xyz2 |
| 75 | url | hxxp://45.159.248.110/brow/xyz2 |
| 76 | organization | 奇安信威胁情报中心（semantic_role = research） |

> 说明：上表实体均通过 `provenance.document_id = 5` 与本文关联，但**仅实体 66/63/64 与 Lazarus 之间存在库内显式关系边**；其余实体与 Lazarus 之间没有直接关系边，属于"同文档共现"，不等同于库内已断言的关系。

### 3. 关系证据（Lazarus 子图）

库内 `relations` 表中与 Lazarus（61）/APT-Q-1（62）相关的全部关系：

| 关系 ID | src_entity_id → dst_entity_id | relation_type | confidence | 证据（prov id，文档 5 引文） |
| --- | --- | --- | --- | --- |
| 23 | 61 Lazarus → 66 ClickFix | USES | 缺失(NULL) | prov 99："Lazarus 在以虚假招聘为诱饵的钓鱼攻击中融入 ClickFix 手法" |
| 24 | 61 Lazarus → 63 BeaverTail | USES | 缺失(NULL) | prov 100："执行 Lazarus 组织常用的 BeaverTail 恶意软件" |
| 25 | 61 Lazarus → 64 InvisibleFerret | USES | 缺失(NULL) | prov 101："Lazarus 后续下载 Python 木马 InvisibleFerret 的常用保存路径" |
| 26 | 63 BeaverTail → 70 (45.159.248.110) | COMMUNICATES_WITH | 缺失(NULL) | prov 102："C2 服务器为 hxxp://45.159.248.110" |
| 27 | 63 BeaverTail → 71 (45.89.53.54) | COMMUNICATES_WITH | 缺失(NULL) | prov 103："其中加载的 BeaverTail 恶意软件连接的 C2 服务器为 hxxp://45.89.53.54" |
| 28 | 65 drvUpdate.exe → 72 (103.231.75.101) | COMMUNICATES_WITH | 缺失(NULL) | prov 104："后门连接的 C2 服务器为 103.231.75.101:8888" |

**关于 APT-Q-1（实体 62）**：`relations` 表中 `src_entity_id = 62` 或 `dst_entity_id = 62` 的记录数为 **0**。该实体在库内无任何关系边。

### 4. 攻击手法与归因的库内证据

以下均为文档 5 原文的库内事实（引文来自 `provenance`，字符偏移为文档 5 正文内位置）：

- **组织背景**（prov 83，偏移 77–103）："Lazarus 是疑似具有东北亚背景的 APT 组织"。
- **内部编号出处**（prov 84，偏移 104–121）："奇安信内部跟踪编号 APT-Q-1"。
- **手法融合**（prov 88 / 关系 23 证据，偏移 400–437）："Lazarus 在以虚假招聘为诱饵的钓鱼攻击中融入 ClickFix 手法"。
- **投放线索**（prov 98，偏移 595–641）："奇安信威胁情报中心发现一个与 Lazarus ClickFix 攻击活动有关的 bat 脚本"。
- **恶意软件归属表述**（prov 85 / 关系 24 证据，偏移 687–719）："执行 Lazarus 组织常用的 BeaverTail 恶意软件"。
- **后门实体**（prov 87，偏移 737–778）："攻击者还会运行一个具有命令执行、读写指定文件功能的后门 drvUpdate.exe"。
- **InvisibleFerret 线索**（prov 86 / 关系 25 证据，偏移 1773–1819）："Lazarus 后续下载 Python 木马 InvisibleFerret 的常用保存路径"。
- **基础设施**（prov 92/93/94）：C2 分别为 `hxxp://45.159.248.110`、`hxxp://45.89.53.54`、`103.231.75.101:8888`。
- **原文归因逻辑**（文档 5"溯源关联"章节）："最初阶段 ClickFix-1.bat 脚本中的命令与 Lazarus 相关报告[1, 2]中提到的命令高度相似，并且最后部署 BeaverTail 和 InvisibleFerret 恶意软件，因此我们将相关样本归属为 Lazarus 组织。"

> 归因性质提示：文档 5 的归属是**原文作者基于命令相似度 + 恶意软件家族一致性的判断**，属原文陈述；库内 `relations.confidence` 字段全部为 NULL，未对该归因给出量化置信度。

## 关联分析与推断

以下内容为**分析推断**，不属于库内直接断言，已标注依据与不确定性。

**推断 1：Lazarus 与 APT-Q-1 的同一性在库内无法唯一判定。**

- 库内事实：两实体均为 `threat_actor`，`display_name` 均缺失，`entity_aliases` 表 **0 行**（无任何别名记录），因此不存在"Lazarus 别名 = APT-Q-1"这类可查询桥接证据。两者仅同时出现于文档 5 同一段原文：prov 83 抽取 Lazarus，prov 84 抽取 APT-Q-1，其中 prov 84 引文为"奇安信内部跟踪编号 APT-Q-1"。
- 推断：结合 prov 83 与 prov 84 的语境，原文语义为"Lazarus ……（奇安信内部跟踪编号）APT-Q-1"，即原文把两者表述为同一组织的不同称谓。但该结论来自**原文自然语言表述**，库内未将其结构化为 alias 或 `SAME_AS` 关系，故库内数据本身**不能唯一判定**同一性。
- 不确定性：`entity_aliases` 为空意味着无法用别名表直接确认；两实体无共享关系边（Lazarus 有 3 条 USES，APT-Q-1 有 0 条），也无共享 provenance 之外的结构关联。

**两种口径下的结果（按要求分别给出）：**

- **口径 A——按库内结构口径（不合并）**：Lazarus 为一个威胁组织，拥有 3 条 USES 关系与 2 跳基础设施链；APT-Q-1 为另一个独立威胁组织，**无任何关系边、无任何独立攻击手法证据**，其库内唯一信息是出现在文档 5 中的实体引文。此口径下二者关系为"未知/无法判定"。
- **口径 B——按原文语义口径（视为同一组织）**：若采纳文档 5 原文表述，则 APT-Q-1 是 Lazarus 的同一组织的内部跟踪编号，Lazarus 的全部 3 条关系与全部基础设施证据可一并归属于"Lazarus（APT-Q-1）"这一实体。此口径下库内 Lazarus 子图的实体与关系数量与口径 A 相同，差别仅在于命名与实体计数（威胁组织由 2 个变为 1 个）。
- **结论**：库内数据无法在两种口径间做唯一裁决；本报告不擅自合并两实体，亦不编造别名或 `SAME_AS` 关系。

**推断 2：库内 Lazarus 子图呈"单文档、单来源"形态。**

- 依据：全部 21 条与 Lazarus 子图相关的 provenance 记录（prov 83–104）`document_id` 均为 5；`documents` 中无第二篇提及 Lazarus 的文章。因此当前 Lazarus 画像完全依赖单一文档，缺乏跨来源交叉验证。

**推断 3：实体类型与 `semantic_role` 存在库内质量问题（如实标注）。**

- `ClickFix` 被抽为 `entity_type = tool`，而原文语境是"社会工程学攻击手法"；`drvUpdate.exe` 被抽为 `malware` 而原文称为"后门"。这些为库内既有标注，报告照录，不做改写。

## 外部背景

本任务仅要求库内分析（全库范围、未要求公开检索），且库内结果已足以回答"库内有哪些相关证据"这一问题，故**未执行网络搜索**，本报告不包含外部背景内容。文档 5 原文引用的 3 条外部参考链接（gendigital、anyrun/medium、RedDrip7 推文）属原文内容转录，非本次检索所得，亦未在库内单独建实体。

## 局限与信息缺口

1. **别名数据缺失**：`entity_aliases` 表 0 行，无法验证 Lazarus 或 APT-Q-1 的公开别名，也无法结构化确认二者同一性。
2. **可信度字段缺失**：两实体及全部 6 条相关关系的 `confidence` 均为 NULL；`provenance.confidence` 亦为 NULL，无法给出量化置信度。
3. **时间字段缺失**：两实体的 `first_seen_at` / `last_seen_at` 均为 NULL；关系表的时间字段同样为 NULL，无法划定攻击活动时间窗口（文档 5 发布时间 2025-08-27 仅代表文章发布，不代表活动发生时间）。
4. **单一来源**：Lazarus 相关证据 100% 来自文档 5 一篇；换来源或换时间无补充样本。
5. **关系类型单一**：库内 Lazarus 仅有 USES 关系，**未发现** TARGETS（受害者）、EXPLOITS（CVE）、ATTRIBUTED_TO（第三方归因）关系；库内 Lazarus 无关联 CVE 实体。
6. **APT-Q-1 近乎无画像**：实体 62 无关系边、无独立证据，除文档 5 的实体引文外无其他库内信息。
7. **共现 ≠ 关系**：文档 5 中抽取的 MD5、IPv4、URL 等 IOC 实体（67–75）与 Lazarus 之间没有直接关系边；将其直接等同于 Lazarus 的 IOC 属于超出库内证据的推断（其上游实体为 BeaverTail / drvUpdate.exe，见关系 26–28）。
8. **原文归因未经库内独立验证**：文档 5 对 Lazarus 的归属是原文基于命令相似度的判断，库内未提供独立第二证据。

## 后续建议

1. 若要判定 Lazarus 与 APT-Q-1 的同一性，需补充 `entity_aliases` 数据（录入权威别名及 source_url），或在库内建立显式 `SAME_AS` 关系；在此之前按口径 A 处理，避免自动合并。
2. 补充 Lazarus 相关的第二、第三来源文档，以实现跨来源交叉验证并覆盖 TARGETS/EXPLOITS/ATTRIBUTED_TO 等关系类型。
3. 回填 `confidence` 与 `first_seen_at` / `last_seen_at`，以支持置信度排序和活动时间线分析。
4. 复核文档 5 的实体类型标注（如 ClickFix 的 tool/手法归类、drvUpdate.exe 的 malware/后门归类），提升图谱语义一致性。
5. 如需外部背景（公开别名、厂商归因时间线），可在明确要求后执行公开检索，并将结果标记为外部背景，不并入库内事实。
