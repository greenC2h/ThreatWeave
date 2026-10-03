"""为 ThreatWeave 三个业务 Agent 暴露最小 Java CRUD 工具集。"""

from __future__ import annotations

import hashlib
from typing import Any

from fastmcp import Context, FastMCP

from mcp_server.http_base import request_threatweave_api


MAX_DOCUMENT_CHUNK_CHARACTERS = 8_000
VALID_ENTITY_TYPES = frozenset({
    "ipv4", "ipv6", "domain", "url", "file_hash", "cve", "threat_actor",
    "malware", "campaign", "attack_technique", "tool", "organization",
})
VALID_RELATION_TYPES = frozenset({
    "USES", "ATTRIBUTED_TO", "INDICATES", "RESOLVES_TO", "TARGETS", "EXPLOITS", "COMMUNICATES_WITH",
})
VALID_SEMANTIC_ROLES = frozenset({"malicious_infrastructure", "victim", "research", "unknown"})


def _http_client(ctx: Context):
    """获取 MCP 生命周期内共享的 Java API 客户端。"""
    return ctx.request_context.lifespan_context["http_client"]


def _content_sha256(content: str) -> str:
    """计算最终格式化正文的 UTF-8 指纹，避免要求 Agent 手工生成哈希。"""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _normalize_extraction_records(value: Any, collection_key: str) -> list[dict[str, Any]]:
    """将模型常见的单对象或外层包装规范为 Java 接口需要的对象列表。"""
    if isinstance(value, dict):
        nested = value.get(collection_key)
        value = nested if isinstance(nested, list) else [value]
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ValueError(f"{collection_key} 必须是对象列表")
    return [_normalize_extraction_record(item) for item in value]


def _normalize_extraction_record(record: dict[str, Any]) -> dict[str, Any]:
    """将 Python 风格字段映射到 Java REST 契约，不推断缺失的业务字段。"""
    field_names = {
        "entity_type": "entityType",
        "canonical_value": "canonicalValue",
        "display_name": "displayName",
        "semantic_role": "semanticRole",
        "first_seen_at": "firstSeenAt",
        "last_seen_at": "lastSeenAt",
        "src_entity_type": "srcEntityType",
        "src_canonical_value": "srcCanonicalValue",
        "dst_entity_type": "dstEntityType",
        "dst_canonical_value": "dstCanonicalValue",
        "relation_type": "relationType",
        "evidence_quote": "evidenceQuote",
        "char_start": "charStart",
        "char_end": "charEnd",
    }
    normalized = {field_names.get(key, key): value for key, value in record.items()}
    evidence = normalized.get("evidence")
    if isinstance(evidence, dict):
        normalized["evidence"] = [_normalize_extraction_record(evidence)]
    elif isinstance(evidence, list):
        normalized["evidence"] = [
            _normalize_extraction_record(item) if isinstance(item, dict) else item
            for item in evidence
        ]
    return normalized


def _split_document_content(content: str, max_characters: int) -> list[str]:
    """按段落优先切分正文，不丢失字符，以控制 B 的单次模型上下文。"""
    if max_characters < 1:
        raise ValueError("max_chunk_characters 必须大于零")
    if not content:
        return [""]

    chunks: list[str] = []
    current = ""
    for paragraph in content.splitlines(keepends=True):
        if current and len(current) + len(paragraph) > max_characters:
            chunks.append(current)
            current = ""
        while len(paragraph) > max_characters:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(paragraph[:max_characters])
            paragraph = paragraph[max_characters:]
        current += paragraph
    if current:
        chunks.append(current)
    return chunks


def _find_unique_evidence(content: str, evidence: Any) -> tuple[int, int] | None:
    """定位模型提供的精简引文；重复或不存在时要求模型提供更精确的证据。"""
    if not isinstance(evidence, str) or not evidence.strip():
        return None
    start = content.find(evidence)
    if start < 0 or content.find(evidence, start + 1) >= 0:
        return None
    return start, start + len(evidence)


def _evidence_text(value: Any) -> str | None:
    """兼容校验工具回传的内部证据对象，始终回到模型层的 ``evidence`` 文本。"""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        quote = value.get("evidenceQuote", value.get("evidence_quote"))
        return quote if isinstance(quote, str) else None
    if isinstance(value, list) and len(value) == 1:
        return _evidence_text(value[0])
    return None


def _validate_confidence(value: Any) -> int | None:
    """校验可选置信度，缺失时保持为空而不由代码推断语义置信度。"""
    if value is None:
        return None
    if not isinstance(value, int) or not 0 <= value <= 100:
        raise ValueError("confidence 必须是 0 到 100 的整数")
    return value


def _evidence_payload(content: str, candidate: dict[str, Any]) -> dict[str, Any] | None:
    """将唯一匹配的 ``evidence`` 转换为 Java 内部保存的精确出处字段。"""
    evidence = _evidence_text(candidate.get("evidence"))
    location = _find_unique_evidence(content, evidence)
    if location is None:
        return None
    confidence = _validate_confidence(candidate.get("confidence"))
    return {
        "evidenceQuote": evidence,
        "charStart": location[0],
        "charEnd": location[1],
        "extractor": "entity_relation_extractor",
        "confidence": confidence,
    }


def _validate_extraction_candidates(
    content: str,
    entities: Any,
    relations: Any,
) -> dict[str, Any]:
    """校验并规范化模型候选，返回可直接原子写入的记录与拒绝原因。"""
    accepted_entities: list[dict[str, Any]] = []
    accepted_relations: list[dict[str, Any]] = []
    rejected: list[dict[str, str]] = []

    for index, raw in enumerate(_normalize_extraction_records(entities, "entities")):
        try:
            entity_type = raw.get("entityType")
            canonical_value = raw.get("canonicalValue")
            semantic_role = raw.get("semanticRole", "unknown")
            if entity_type not in VALID_ENTITY_TYPES:
                raise ValueError("entityType 不在已确认集合中")
            if not isinstance(canonical_value, str) or not canonical_value.strip():
                raise ValueError("canonicalValue 不能为空")
            if semantic_role not in VALID_SEMANTIC_ROLES:
                raise ValueError("semanticRole 不在已确认集合中")
            evidence = _evidence_payload(content, raw)
            if evidence is None:
                raise ValueError("evidence 必须是正文中唯一出现的非空精简引文")
            raw["canonicalValue"] = canonical_value.strip()
            raw["semanticRole"] = semantic_role
            raw["confidence"] = _validate_confidence(raw.get("confidence"))
            raw["evidence"] = [evidence]
            accepted_entities.append(raw)
        except ValueError as exc:
            rejected.append({"kind": "entity", "index": str(index), "reason": str(exc)})

    relation_input = relations if relations is not None else []
    for index, raw in enumerate(_normalize_extraction_records(relation_input, "relations")):
        try:
            relation_type = raw.get("relationType")
            endpoint_fields = (
                "srcEntityType", "srcCanonicalValue", "dstEntityType", "dstCanonicalValue",
            )
            if relation_type not in VALID_RELATION_TYPES:
                raise ValueError("relationType 不在已确认集合中")
            if not all(isinstance(raw.get(field), str) and raw[field].strip() for field in endpoint_fields):
                raise ValueError("关系两端的实体类型和规范值不能为空")
            evidence = _evidence_payload(content, raw)
            if evidence is None:
                raise ValueError("evidence 必须是正文中唯一出现的非空精简引文")
            raw["confidence"] = _validate_confidence(raw.get("confidence"))
            raw["evidence"] = [evidence]
            accepted_relations.append(raw)
        except ValueError as exc:
            rejected.append({"kind": "relation", "index": str(index), "reason": str(exc)})

    return {
        "entities": accepted_entities,
        "relations": accepted_relations,
        "rejected": rejected,
    }


def register_threatweave_tools(mcp: FastMCP) -> None:
    """注册由各子 Agent 配置显式筛选的 ThreatWeave 工具。"""

    @mcp.tool(name="threat_document_upsert")
    async def upsert_document(
        doc_key: str,
        source_name: str,
        content: str,
        content_sha256: str | None = None,
        external_id: str | None = None,
        title: str | None = None,
        url: str | None = None,
        published_at: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """写入或覆盖一份格式化情报文档，仅供 intel_ingestor 使用。

        当调用方未传 ``content_sha256`` 时，MCP 根据最终正文计算它，避免将机械计算
        交给模型。``doc_key`` 必须来自受控采集工具，以保证同一来源文章可幂等覆盖。
        """
        final_content_sha256 = content_sha256 or _content_sha256(content)
        return await request_threatweave_api(_http_client(ctx), "POST", "/threatweave/documents", json={
            "docKey": doc_key,
            "sourceName": source_name,
            "externalId": external_id,
            "title": title,
            "url": url,
            "publishedAt": published_at,
            "content": content,
            "contentSha256": final_content_sha256,
        }) or {}

    @mcp.tool(name="threat_document_get")
    async def get_document(
        document_id: int,
        chunk_index: int = 0,
        max_chunk_characters: int = MAX_DOCUMENT_CHUNK_CHARACTERS,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """按受控字符预算读取格式化文档的一块，供 B 逐块抽取候选。"""
        if not 1 <= max_chunk_characters <= MAX_DOCUMENT_CHUNK_CHARACTERS:
            raise ValueError(f"max_chunk_characters 必须在 1 到 {MAX_DOCUMENT_CHUNK_CHARACTERS} 之间")
        document = await request_threatweave_api(
            _http_client(ctx), "GET", f"/threatweave/documents/{document_id}"
        ) or {}
        content = document.pop("content", "")
        if not isinstance(content, str):
            raise ValueError("文档正文格式无效")
        chunks = _split_document_content(content, max_chunk_characters)
        if not 0 <= chunk_index < len(chunks):
            raise ValueError(f"chunk_index 超出范围，当前文档共有 {len(chunks)} 块")
        return {
            **document,
            "content": chunks[chunk_index],
            "chunkIndex": chunk_index,
            "chunkCount": len(chunks),
        }

    @mcp.tool(name="validate_extraction_evidence")
    async def validate_extraction_evidence(
        document_id: int,
        entities: list[dict[str, Any]] | dict[str, Any],
        relations: list[dict[str, Any]] | dict[str, Any] | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """校验候选类型、角色和唯一精简出处，并返回可写入与拒绝记录。"""
        document = await request_threatweave_api(
            _http_client(ctx), "GET", f"/threatweave/documents/{document_id}"
        ) or {}
        content = document.get("content")
        if not isinstance(content, str):
            raise ValueError("文档正文格式无效")
        return _validate_extraction_candidates(content, entities, relations)

    @mcp.tool(name="threat_extraction_write")
    async def write_extraction(
        document_id: int,
        entities: list[dict[str, Any]] | dict[str, Any],
        relations: list[dict[str, Any]] | dict[str, Any] | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """原子写入通过证据校验的候选，防止模型绕过校验而破坏 Java 契约。"""
        document = await request_threatweave_api(
            _http_client(ctx), "GET", f"/threatweave/documents/{document_id}"
        ) or {}
        content = document.get("content")
        if not isinstance(content, str):
            raise ValueError("文档正文格式无效")
        validated = _validate_extraction_candidates(content, entities, relations)
        if not validated["entities"] and not validated["relations"]:
            return {"written": False, "rejected": validated["rejected"]}
        result = await request_threatweave_api(_http_client(ctx), "POST", "/threatweave/extractions", json={
            "documentId": document_id,
            "entities": validated["entities"],
            "relations": validated["relations"],
        }) or {}
        return {**result, "written": True, "rejected": validated["rejected"]}

    @mcp.tool(name="threat_graph_query")
    async def query_graph(
        query: str = "",
        limit: int = 100,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """查询只读实体与关系子图，仅供 threat_analyst 使用。"""
        return await request_threatweave_api(
            _http_client(ctx), "GET", "/threatweave/graph", params={"query": query, "limit": limit}
        ) or {"entities": [], "relations": []}
