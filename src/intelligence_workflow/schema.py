"""定义情报处理工作流的公开请求和内部状态模型。"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class IntelligenceWorkflowMode(StrEnum):
    """工作流支持的业务模式。"""

    FORMAT_ONLY = "format_only"
    EXTRACT_PREVIEW = "extract_preview"
    INGEST_FULL = "ingest_full"
    EXTRACT_PENDING = "extract_pending"
    LIST_PROCESSING = "list_processing"


class RequestedDeliverable(StrEnum):
    """同步工作流允许按用户请求生成的交付件类型。"""

    FORMATTED_MARKDOWN = "formatted_markdown"
    EXTRACTION_MARKDOWN = "extraction_markdown"


class FormattingStatus(StrEnum):
    """规范文档的格式化阶段状态。"""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ExtractionStatus(StrEnum):
    """当前规范正文对应的实体关系抽取状态。"""

    NOT_REQUESTED = "not_requested"
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    STALE = "stale"
    FAILED = "failed"


class IntelligenceWorkflowRequest(BaseModel):
    """主 Agent、调度器和内部任务提交给统一工作流的受控请求。"""

    mode: IntelligenceWorkflowMode
    actor_id: str = Field(min_length=1, max_length=255)
    source_id: str | None = Field(default=None, max_length=255)
    article_url: str | None = Field(default=None, max_length=2048)
    document_ids: list[int] = Field(default_factory=list)
    max_articles: int = Field(default=3, ge=1, le=100)
    force_refresh: bool = False
    requested_deliverables: list[RequestedDeliverable] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_target(self) -> "IntelligenceWorkflowRequest":
        """确保每个执行请求恰好描述一种可定位的文章目标。"""
        if any(document_id < 1 for document_id in self.document_ids):
            raise ValueError("document_ids 必须是正整数")
        if len(set(self.document_ids)) != len(self.document_ids):
            raise ValueError("document_ids 不可重复")
        if len(set(self.requested_deliverables)) != len(self.requested_deliverables):
            raise ValueError("requested_deliverables 不可重复")
        if self.mode is IntelligenceWorkflowMode.LIST_PROCESSING:
            if self.article_url or self.document_ids:
                raise ValueError("list_processing 只能按可选 source_id 过滤，不能指定文章")
            return self

        if self.mode is IntelligenceWorkflowMode.EXTRACT_PENDING:
            if self.article_url or self.document_ids:
                raise ValueError("extract_pending 只能按可选 source_id 过滤")
            return self

        target_count = int(bool(self.source_id or self.article_url)) + int(bool(self.document_ids))
        if target_count != 1:
            raise ValueError("执行请求必须指定 article_url、source_id 或 document_ids 中的一种目标")
        return self


class ProcessingRecord(BaseModel):
    """一篇规范文档在工作流中的可查询处理状态。"""

    doc_key: str
    source_id: str | None = None
    external_id: str | None = None
    url: str | None = None
    document_id: int | None = None
    source_fingerprint: str | None = None
    formatted_content_sha256: str | None = None
    formatting_status: FormattingStatus = FormattingStatus.PENDING
    extraction_status: ExtractionStatus = ExtractionStatus.NOT_REQUESTED
    extracted_content_sha256: str | None = None
    current_workflow_id: str | None = None
    last_failed_stage: str | None = None
    last_error: str | None = None
    formatted_at: datetime | None = None
    extracted_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ExtractionDraft(BaseModel):
    """仅抽取模式的、与用户和正文哈希绑定的结构化草稿。"""

    draft_id: str
    user_id: str
    document_id: int
    content_sha256: str
    payload: dict[str, Any]
    status: str = "active"
    created_at: datetime | None = None
    expires_at: datetime | None = None


class DraftAccessGrant(BaseModel):
    """仅供内部 B 工具使用的一次性草稿访问授权。"""

    token: str
    user_id: str
    document_id: int
    content_sha256: str
    purpose: str
    draft_id: str | None = None


class FormattingAccessGrant(BaseModel):
    """仅供内部 A 写入一篇受控文章的一次性授权。"""

    token: str
    doc_key: str
    source_id: str
    source_name: str
    external_id: str | None = None
    url: str | None = None
    published_at: str | None = None


class WorkflowDocumentResult(BaseModel):
    """一次工作流中单篇文章的最终处理摘要；采集阶段跳过项尚无 doc_key。"""

    doc_key: str | None = None
    document_id: int | None = None
    action: str
    detail: str | None = None


class WorkflowDeliverable(BaseModel):
    """同步工作流为当前用户登记的可下载交付件。"""

    type: Literal["sandbox_deliverable"] = "sandbox_deliverable"
    artifact_id: str
    filename: str
    mime_type: str
    label: str


class IntelligenceWorkflowResult(BaseModel):
    """同步工作流向主 Agent 或调度器返回的紧凑、无正文结果。"""

    workflow_id: str
    mode: IntelligenceWorkflowMode
    documents: list[WorkflowDocumentResult] = Field(default_factory=list)
    deliverables: list[WorkflowDeliverable] = Field(default_factory=list)
    failures: list[str] = Field(default_factory=list)
