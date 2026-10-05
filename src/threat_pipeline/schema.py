"""ThreatPipeline 的公开请求、状态和结果模型。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, model_validator


class ThreatPipelineRequest(BaseModel):
    """描述一次从来源直链或来源配置到图谱事实的完整导入请求。"""

    actor_id: str = Field(min_length=1, max_length=255)
    source_id: str | None = Field(default=None, max_length=255)
    article_url: str | None = Field(default=None, max_length=2048)
    max_articles: int = Field(default=3, ge=1, le=100)
    force_refresh: bool = False

    @model_validator(mode="after")
    def validate_target(self) -> "ThreatPipelineRequest":
        """要求请求定位一个来源或一篇已批准文章。"""
        if not self.source_id and not self.article_url:
            raise ValueError("导入请求必须指定 source_id 或 article_url")
        return self


class ProcessingRecord(BaseModel):
    """一篇文章的持久化处理状态，用于幂等和失败恢复。"""

    doc_key: str
    source_id: str | None = None
    external_id: str | None = None
    url: str | None = None
    document_id: int | None = None
    source_fingerprint: str | None = None
    formatted_content_sha256: str | None = None
    status: str = "pending"
    current_run_id: str | None = None
    last_failed_stage: str | None = None
    last_error: str | None = None
    formatted_at: datetime | None = None
    extracted_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class PipelineDocumentResult(BaseModel):
    """单篇文章在本次流水线中的结果。"""

    action: str
    doc_key: str | None = None
    document_id: int | None = None
    title: str | None = None
    detail: str | None = None


class ThreatPipelineResult(BaseModel):
    """流水线返回给 threat_handle 或调度器的紧凑摘要。"""

    run_id: str
    documents: list[PipelineDocumentResult] = Field(default_factory=list)
    failures: list[str] = Field(default_factory=list)
