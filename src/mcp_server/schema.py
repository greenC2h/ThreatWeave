"""ThreatWeave MCP 工具的结构化候选输入模型。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


VALID_ENTITY_TYPES = frozenset({
    "ipv4", "ipv6", "domain", "url", "file_hash", "cve", "threat_actor",
    "malware", "campaign", "attack_technique", "tool", "organization",
})
VALID_RELATION_TYPES = frozenset({
    "USES", "ATTRIBUTED_TO", "INDICATES", "RESOLVES_TO", "TARGETS", "EXPLOITS", "COMMUNICATES_WITH",
})
VALID_SEMANTIC_ROLES = frozenset({"malicious_infrastructure", "victim", "research", "unknown"})


class ExtractionEvidence(BaseModel):
    """校验工具返回给后续写入工具的内部证据引用。"""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    evidence_quote: str = Field(alias="evidenceQuote")


class ExtractionEntityCandidate(BaseModel):
    """B 提交给校验或写入工具的一条实体候选。"""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    entity_type: str = Field(
        alias="entityType",
        description="实体类型：ipv4、ipv6、domain、url、file_hash、cve、threat_actor、malware、campaign、attack_technique、tool 或 organization。",
    )
    canonical_value: str = Field(alias="canonicalValue", description="实体规范值。")
    semantic_role: str = Field(
        default="unknown",
        alias="semanticRole",
        description="语义角色：malicious_infrastructure、victim、research 或 unknown。",
    )
    evidence: str | list[ExtractionEvidence] = Field(
        description="正文中唯一出现的最小充分引文；也可传入校验工具返回的 evidence 引用。"
    )
    display_name: str | None = Field(default=None, alias="displayName")
    confidence: int | None = None


class ExtractionRelationCandidate(BaseModel):
    """B 提交给校验或写入工具的一条关系候选。"""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    src_entity_type: str = Field(
        alias="srcEntityType",
        description="源实体类型，必须与对应实体的 entityType 一致。",
    )
    src_canonical_value: str = Field(
        alias="srcCanonicalValue",
        description="源实体规范值，必须与对应实体的 canonicalValue 一致。",
    )
    dst_entity_type: str = Field(
        alias="dstEntityType",
        description="目标实体类型，必须与对应实体的 entityType 一致。",
    )
    dst_canonical_value: str = Field(
        alias="dstCanonicalValue",
        description="目标实体规范值，必须与对应实体的 canonicalValue 一致。",
    )
    relation_type: str = Field(
        alias="relationType",
        description="关系类型：USES、ATTRIBUTED_TO、INDICATES、RESOLVES_TO、TARGETS、EXPLOITS 或 COMMUNICATES_WITH。",
    )
    evidence: str | list[ExtractionEvidence] = Field(
        description="正文中唯一出现的最小充分引文；也可传入校验工具返回的 evidence 引用。"
    )
    confidence: int | None = None
