"""为 ThreatWeave 三个业务 Agent 暴露最小 Java CRUD 工具集。"""

from __future__ import annotations

import hashlib
from typing import Annotated, Any

from fastmcp import Context, FastMCP
from pydantic import BaseModel, Field

from mcp_server.http_base import request_threatweave_api
from mcp_server.schema import (
    ExtractionEntityCandidate,
    ExtractionRelationCandidate,
    VALID_ENTITY_TYPES,
    VALID_RELATION_TYPES,
    VALID_SEMANTIC_ROLES,
)
from intelligence_workflow.repository import WorkflowRepository


MAX_DOCUMENT_CHUNK_CHARACTERS = 8_000

DocumentId = Annotated[int, Field(gt=0, description="要读取、校验或写入的格式化情报文档 ID。")]
DocumentAccessToken = Annotated[str, Field(description="本次格式化文档写入所需的访问令牌。")]
ExtractionAccessToken = Annotated[str, Field(description="本次抽取草稿保存或提交所需的访问令牌。")]
FormattedContent = Annotated[str, Field(description="已清洗并整理为 Markdown 的完整文档正文。")]
DocumentTitle = Annotated[str | None, Field(description="可选的文档标题；省略时保留现有标题或由系统确定。")]
ChunkIndex = Annotated[int, Field(ge=0, description="要读取的正文分块序号，从 0 开始。")]
MaxChunkCharacters = Annotated[
    int,
    Field(
        ge=1,
        le=MAX_DOCUMENT_CHUNK_CHARACTERS,
        description="每个正文分块允许的最大字符数，默认 8000。",
    ),
]
EntityCandidates = Annotated[
    list[ExtractionEntityCandidate],
    Field(description="待校验或写入的实体候选列表；每项必须包含规范值和正文证据。"),
]
RelationCandidates = Annotated[
    list[ExtractionRelationCandidate] | None,
    Field(description="可选的关系候选列表；每项必须使用平面源端点、目标端点和正文证据。"),
]
GraphQuery = Annotated[str, Field(description="可选的实体名称、IOC、CVE 或关键词；为空时查询指定范围内全部数据。")]
GraphDocumentIds = Annotated[
    list[int] | None,
    Field(description="可选的文档 ID 列表；提供时仅查询这些文档关联的实体和关系。"),
]
GraphLimit = Annotated[int, Field(ge=1, le=500, description="最多返回的实体和关系数量，默认 100。")]


def _http_client(ctx: Context):
    """获取 MCP 生命周期内共享的 Java API 客户端。"""
    return ctx.request_context.lifespan_context["http_client"]


def _content_sha256(content: str) -> str:
    """计算最终格式化正文的 UTF-8 指纹，避免要求 Agent 手工生成哈希。"""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _normalize_extraction_records(value: Any, collection_key: str) -> list[dict[str, Any]]:
    """将模型常见的单对象或外层包装规范为 Java 接口需要的对象列表。"""
    if isinstance(value, BaseModel):
        value = value.model_dump(by_alias=True, exclude_none=True)
    if isinstance(value, dict):
        nested = value.get(collection_key)
        value = nested if isinstance(nested, list) else [value]
    if not isinstance(value, list):
        raise ValueError(f"{collection_key} 必须是对象列表")
    records: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, BaseModel):
            item = item.model_dump(by_alias=True, exclude_none=True)
        if not isinstance(item, dict):
            raise ValueError(f"{collection_key} 必须是对象列表")
        records.append(_normalize_extraction_record(item))
    return records


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
                raise ValueError("entityType 必须是已确认实体类型，不能使用 name 或 type 代替")
            if not isinstance(canonical_value, str) or not canonical_value.strip():
                raise ValueError("canonicalValue 不能为空，不能使用 name 代替")
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
                raise ValueError("relationType 必须是已确认关系类型，不能使用 type 代替")
            if not all(isinstance(raw.get(field), str) and raw[field].strip() for field in endpoint_fields):
                raise ValueError(
                    "关系必须提供 srcEntityType、srcCanonicalValue、dstEntityType、"
                    "dstCanonicalValue；不能使用 source、target 或嵌套端点对象"
                )
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
        access_token: DocumentAccessToken,
        content: FormattedContent,
        title: DocumentTitle = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """写入格式化情报文档。

        使用已清洗的 Markdown 正文创建或更新当前目标文档。

        Args:
            access_token: 本次文档写入所需的访问令牌。
            content: 完整的格式化 Markdown 正文。
            title: 可选的文档标题。

        Returns:
            写入后的文档标识和文档元数据。
        """
        grant = await WorkflowRepository().consume_formatting_access_grant(access_token)
        return await request_threatweave_api(_http_client(ctx), "POST", "/threatweave/documents", json={
            "docKey": grant.doc_key,
            "sourceId": grant.source_id,
            "sourceName": grant.source_name,
            "externalId": grant.external_id,
            "title": title,
            "url": grant.url,
            "publishedAt": grant.published_at,
            "content": content,
            "contentSha256": _content_sha256(content),
        }) or {}

    @mcp.tool(name="threat_document_get")
    async def get_document(
        document_id: DocumentId,
        chunk_index: ChunkIndex = 0,
        max_chunk_characters: MaxChunkCharacters = MAX_DOCUMENT_CHUNK_CHARACTERS,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """读取格式化情报文档的一段正文。

        文档较长时按段落切分；返回当前正文片段、分块序号和总分块数。

        Args:
            document_id: 要读取的格式化文档 ID。
            chunk_index: 要读取的正文分块序号，从 0 开始。
            max_chunk_characters: 单个正文分块的最大字符数。

        Returns:
            文档元数据、当前正文片段、chunkIndex 和 chunkCount。
        """
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

    @mcp.tool(name="threat_extraction_get")
    async def get_extraction(
        document_id: DocumentId,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """读取文档已保存的实体、关系和证据。

        用于查看或导出既有抽取结果，不重新抽取内容。

        Args:
            document_id: 要读取抽取结果的格式化文档 ID。

        Returns:
            文档 ID、实体列表、关系列表及各项证据。
        """
        return await request_threatweave_api(
            _http_client(ctx), "GET", f"/threatweave/documents/{document_id}/extraction"
        ) or {"document_id": document_id, "entities": [], "relations": []}

    @mcp.tool(name="validate_extraction_evidence")
    async def validate_extraction_evidence(
        document_id: DocumentId,
        entities: EntityCandidates,
        relations: RelationCandidates = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """校验实体和关系候选是否能由文档正文唯一证据支持。

        在保存草稿或写入抽取结果前调用。关系必须使用平面源端点和目标端点字段。

        Args:
            document_id: 候选来源的格式化文档 ID。
            entities: 待校验的实体候选列表。
            relations: 可选的待校验关系候选列表。

        Returns:
            可接受的 entities、relations，以及每个被拒绝候选的原因。
        """
        document = await request_threatweave_api(
            _http_client(ctx), "GET", f"/threatweave/documents/{document_id}"
        ) or {}
        content = document.get("content")
        if not isinstance(content, str):
            raise ValueError("文档正文格式无效")
        return _validate_extraction_candidates(content, entities, relations)

    @mcp.tool(name="threat_extraction_write")
    async def write_extraction(
        document_id: DocumentId,
        entities: EntityCandidates,
        relations: RelationCandidates = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """写入文档中有正文证据支持的实体和关系。

        工具会校验证据；不符合要求的候选不会写入，并会在结果中说明原因。

        Args:
            document_id: 要写入抽取结果的格式化文档 ID。
            entities: 待写入的实体候选列表。
            relations: 可选的待写入关系候选列表。

        Returns:
            写入状态、写入结果及被拒绝候选的原因。
        """
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

    @mcp.tool(name="threat_extraction_preview")
    async def save_extraction_preview(
        document_id: DocumentId,
        access_token: ExtractionAccessToken,
        entities: EntityCandidates,
        relations: RelationCandidates = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """校验并保存文档的抽取草稿。

        草稿可供后续确认；本操作不会提交最终的实体和关系结果。

        Args:
            document_id: 草稿对应的格式化文档 ID。
            access_token: 本次草稿保存所需的访问令牌。
            entities: 要保存的实体候选列表。
            relations: 可选的要保存关系候选列表。

        Returns:
            草稿 ID、接受的实体和关系数量，以及被拒绝候选的原因。
        """
        repository = WorkflowRepository()
        grant = await repository.consume_draft_access_grant(access_token, "preview")
        if grant.document_id != document_id:
            raise ValueError("草稿访问授权与目标文档不一致")
        document = await request_threatweave_api(
            _http_client(ctx), "GET", f"/threatweave/documents/{document_id}"
        ) or {}
        content = document.get("content")
        content_sha256 = document.get("content_sha256")
        if not isinstance(content, str) or not isinstance(content_sha256, str):
            raise ValueError("文档正文或正文哈希无效")
        if content_sha256 != grant.content_sha256:
            raise ValueError("文档正文已变化，不能保存旧版本抽取草稿")
        validated = _validate_extraction_candidates(content, entities, relations)
        draft = await repository.save_draft(
            user_id=grant.user_id,
            document_id=document_id,
            content_sha256=content_sha256,
            payload={"entities": validated["entities"], "relations": validated["relations"]},
        )
        return {
            "draftId": draft.draft_id,
            "acceptedEntities": len(validated["entities"]),
            "acceptedRelations": len(validated["relations"]),
            "rejected": validated["rejected"],
        }

    @mcp.tool(name="commit_extraction_draft")
    async def commit_extraction_draft(
        access_token: ExtractionAccessToken,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """提交已保存的抽取草稿。

        将当前可用草稿中的实体和关系写入文档抽取结果。

        Args:
            access_token: 本次草稿提交所需的访问令牌。

        Returns:
            写入状态、草稿 ID、写入结果及被拒绝候选的原因。
        """
        repository = WorkflowRepository()
        grant = await repository.consume_draft_access_grant(access_token, "commit")
        # 令牌由工作流绑定草稿、用户和正文，工具仍验证草稿状态和当前正文哈希。
        draft = await repository.get_active_draft_by_id(grant.draft_id or "", grant.user_id)
        if not draft:
            raise ValueError("抽取草稿不存在、已过期或不属于当前用户")
        if draft.document_id != grant.document_id or draft.content_sha256 != grant.content_sha256:
            raise ValueError("草稿访问授权与当前草稿不一致")
        document = await request_threatweave_api(
            _http_client(ctx), "GET", f"/threatweave/documents/{draft.document_id}"
        ) or {}
        if document.get("content_sha256") != draft.content_sha256:
            raise ValueError("文档正文已变化，不能提交旧草稿")
        entities = draft.payload.get("entities", [])
        relations = draft.payload.get("relations", [])
        content = document.get("content")
        if not isinstance(content, str):
            raise ValueError("文档正文格式无效")
        # 草稿保存后可能被维护脚本或未来的迁移改写；提交前始终以当前正文重新校验。
        validated = _validate_extraction_candidates(content, entities, relations)
        if not validated["entities"] and not validated["relations"]:
            # Java 的抽取接口要求至少一个实体；空草稿仍代表 B 已完成审阅，只是没有可写事实。
            await repository.mark_draft_committed(draft.draft_id)
            return {
                "written": False,
                "draftId": draft.draft_id,
                "reason": "没有可写入的实体或关系",
                "rejected": validated["rejected"],
            }
        result = await request_threatweave_api(_http_client(ctx), "POST", "/threatweave/extractions", json={
            "documentId": draft.document_id,
            "entities": validated["entities"],
            "relations": validated["relations"],
        }) or {}
        await repository.mark_draft_committed(draft.draft_id)
        return {**result, "written": True, "draftId": draft.draft_id, "rejected": validated["rejected"]}

    @mcp.tool(name="threat_graph_query")
    async def query_graph(
        query: GraphQuery = "",
        document_ids: GraphDocumentIds = None,
        limit: GraphLimit = 100,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """查询情报库中的实体、关系及其证据。

        可按关键词或指定文档范围查询；用于回答库内关联、列表和统计问题。

        Args:
            query: 可选的实体名称、IOC、CVE 或关键词；为空时查询指定范围内全部数据。
            document_ids: 可选的文档 ID 列表；提供时仅查询这些文档关联的数据。
            limit: 最多返回的实体和关系数量。

        Returns:
            匹配的 entities、relations 及其文档证据。
        """
        scoped_document_ids = sorted({document_id for document_id in document_ids or [] if document_id > 0})
        return await request_threatweave_api(
            _http_client(ctx),
            "GET",
            "/threatweave/graph",
            params={
                "query": query,
                "documentIds": scoped_document_ids or None,
                "limit": limit,
            },
        ) or {"entities": [], "relations": []}
