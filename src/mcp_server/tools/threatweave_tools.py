"""为 ThreatWeave 三个业务 Agent 暴露最小 Java CRUD 工具集。"""

from __future__ import annotations

import hashlib
from typing import Any

from fastmcp import Context, FastMCP

from mcp_server.http_base import request_threatweave_api


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
    async def get_document(document_id: int, ctx: Context | None = None) -> dict[str, Any]:
        """读取格式化文档及来源元数据，仅供 entity_relation_extractor 使用。"""
        return await request_threatweave_api(_http_client(ctx), "GET", f"/threatweave/documents/{document_id}") or {}

    @mcp.tool(name="threat_extraction_write")
    async def write_extraction(
        document_id: int,
        entities: list[dict[str, Any]] | dict[str, Any],
        relations: list[dict[str, Any]] | dict[str, Any] | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """原子写入有正文证据的实体、别名、关系和 provenance，仅供 B 使用。"""
        normalized_entities = _normalize_extraction_records(entities, "entities")
        normalized_relations = (
            _normalize_extraction_records(relations, "relations") if relations is not None else []
        )
        return await request_threatweave_api(_http_client(ctx), "POST", "/threatweave/extractions", json={
            "documentId": document_id,
            "entities": normalized_entities,
            "relations": normalized_relations,
        }) or {}

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
