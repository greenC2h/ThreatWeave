"""以确定性代码协调情报采集、格式化、抽取和状态迁移。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from intelligence_workflow.document_gateway import CanonicalDocument, DocumentGateway
from intelligence_workflow.repository import WorkflowRepository
from intelligence_workflow.schema import (
    ExtractionStatus,
    IntelligenceWorkflowMode,
    IntelligenceWorkflowRequest,
    IntelligenceWorkflowResult,
    WorkflowDocumentResult,
)
from intel_ingestor.ingestor import collect_source
from intel_ingestor.orchestrator import format_batch_instruction, split_document_batches
from intel_ingestor.schema import CollectedDocument


AgentRunner = Callable[[str], Awaitable[object]]


@dataclass(frozen=True)
class WorkflowAgents:
    """工作流调用 A/B 的窄接口，生产和测试可分别替换实现。"""

    formatter: AgentRunner
    preview_extractor: AgentRunner
    commit_extractor: AgentRunner
    draft_commit_extractor: AgentRunner


class IntelligenceWorkflow:
    """执行一个完整工作流，不让模型决定状态转换或 A/B 调用顺序。"""

    def __init__(
        self,
        *,
        repository: WorkflowRepository,
        document_gateway: DocumentGateway,
        agents: WorkflowAgents,
    ) -> None:
        self._repository = repository
        self._document_gateway = document_gateway
        self._agents = agents

    async def run(self, request: IntelligenceWorkflowRequest) -> IntelligenceWorkflowResult:
        """执行请求并返回不包含正文或模型原始输出的同步处理摘要。"""
        await self._repository.ensure_schema()
        workflow_id = str(uuid4())
        if request.mode is IntelligenceWorkflowMode.LIST_PROCESSING:
            return await self._list_processing(request, workflow_id)
        if request.mode is IntelligenceWorkflowMode.EXTRACT_PENDING:
            documents = await self._pending_documents(request.source_id)
            return await self._extract_documents(request, workflow_id, documents)
        if request.document_ids:
            documents = [
                await self._document_gateway.get_by_id(document_id)
                for document_id in request.document_ids
            ]
            return await self._run_existing_documents(request, workflow_id, documents)
        return await self._run_source(request, workflow_id)

    async def _run_source(
        self,
        request: IntelligenceWorkflowRequest,
        workflow_id: str,
    ) -> IntelligenceWorkflowResult:
        """采集来源文章，按受控批次调用 A，并在需要时交给 B。"""
        assert request.source_id is not None
        collection = await collect_source(
            request.source_id,
            max_articles=request.max_articles,
            article_url=request.article_url,
        )
        result = IntelligenceWorkflowResult(workflow_id=workflow_id, mode=request.mode)
        if collection.listing_error:
            result.failures.append(f"来源列表抓取失败：{request.source_id}")
        collected = [outcome.document for outcome in collection.outcomes if outcome.document]
        result.failures.extend(
            f"采集失败：{outcome.url}" for outcome in collection.outcomes if outcome.status == "failed"
        )
        for outcome in collection.outcomes:
            if outcome.status == "skipped":
                result.documents.append(WorkflowDocumentResult(
                    action="skipped_collection",
                    detail=outcome.reason or outcome.url,
                ))

        formatted: list[CanonicalDocument] = []
        for batch_number, batch in enumerate(split_document_batches(collected), start=1):
            runnable: list[CollectedDocument] = []
            for document in batch:
                acquired, record = await self._repository.begin_formatting(
                    doc_key=document.doc_key,
                    source_id=document.source_id,
                    external_id=document.external_id,
                    url=document.url,
                    source_fingerprint=self._source_fingerprint(document),
                    workflow_id=workflow_id,
                    force_refresh=request.force_refresh,
                )
                if acquired:
                    runnable.append(document)
                else:
                    result.documents.append(WorkflowDocumentResult(
                        doc_key=document.doc_key,
                        document_id=record.document_id,
                        action=(
                            "skipped_in_progress"
                            if record.formatting_status.value == "running"
                            else "skipped_already_formatted"
                        ),
                    ))
                    # 另一工作流正在替换正文时，旧文档不能进入 B；等待它完成后再处理。
                    if record.document_id and record.formatting_status.value != "running":
                        formatted.append(await self._document_gateway.get_by_id(record.document_id))
            if not runnable:
                continue
            try:
                grants = await self._issue_formatting_grants(runnable)
                await self._agents.formatter(
                    format_batch_instruction(request.source_id, batch_number, runnable, grants)
                )
            except Exception as exc:
                for document in runnable:
                    await self._repository.mark_stage_failed(
                        document.doc_key,
                        "formatting",
                        str(exc),
                        workflow_id,
                    )
                    result.failures.append(f"格式化失败：{document.url}")
                continue
            for document in runnable:
                try:
                    canonical = await self._document_gateway.get_by_key(document.doc_key)
                    await self._repository.record_formatted_document(
                        doc_key=document.doc_key,
                        source_id=document.source_id,
                        external_id=document.external_id,
                        url=document.url,
                        document_id=canonical.document_id,
                        content_sha256=canonical.content_sha256,
                        workflow_id=workflow_id,
                        source_fingerprint=self._source_fingerprint(document),
                    )
                except Exception as exc:
                    await self._repository.mark_stage_failed(
                        document.doc_key,
                        "formatting",
                        str(exc),
                        workflow_id,
                    )
                    result.failures.append(f"格式化结果未确认：{document.url}")
                    continue
                formatted.append(canonical)
                result.documents.append(WorkflowDocumentResult(
                    doc_key=document.doc_key,
                    document_id=canonical.document_id,
                    action="formatted",
                ))
        if request.mode is IntelligenceWorkflowMode.FORMAT_ONLY:
            return result
        return await self._extract_documents(request, workflow_id, formatted, prior=result)

    async def _run_existing_documents(
        self,
        request: IntelligenceWorkflowRequest,
        workflow_id: str,
        documents: list[CanonicalDocument],
    ) -> IntelligenceWorkflowResult:
        """在已有规范文档上执行仅抽取或明确入库，不重复运行 A。"""
        result = IntelligenceWorkflowResult(workflow_id=workflow_id, mode=request.mode)
        if request.mode is IntelligenceWorkflowMode.FORMAT_ONLY:
            result.failures.append("已存在规范文档不能再次执行格式化；请指定来源或文章 URL")
            return result
        return await self._extract_documents(request, workflow_id, documents, prior=result)

    async def _extract_documents(
        self,
        request: IntelligenceWorkflowRequest,
        workflow_id: str,
        documents: list[CanonicalDocument],
        *,
        prior: IntelligenceWorkflowResult | None = None,
    ) -> IntelligenceWorkflowResult:
        """顺序调用 B，并仅在 COMMIT 成功返回后记录当前正文已完成抽取。"""
        result = prior or IntelligenceWorkflowResult(workflow_id=workflow_id, mode=request.mode)
        extraction_mode = "PREVIEW" if request.mode is IntelligenceWorkflowMode.EXTRACT_PREVIEW else "COMMIT"
        for document in documents:
            try:
                record = await self._repository.get_processing(document.doc_key)
                if record is None:
                    record = await self._repository.record_formatted_document(
                        doc_key=document.doc_key,
                        source_id=document.source_id or request.source_id or document.source_name,
                        external_id=document.external_id,
                        url=document.url,
                        document_id=document.document_id,
                        content_sha256=document.content_sha256,
                        workflow_id=workflow_id,
                    )
                if (
                    extraction_mode == "COMMIT"
                    and not request.force_refresh
                    and record.extraction_status is ExtractionStatus.COMPLETED
                    and record.extracted_content_sha256 == document.content_sha256
                ):
                    result.documents.append(WorkflowDocumentResult(
                        doc_key=document.doc_key,
                        document_id=document.document_id,
                        action="skipped_already_extracted",
                    ))
                    continue
                if extraction_mode == "COMMIT":
                    acquired, current = await self._repository.begin_extraction(
                        doc_key=document.doc_key,
                        workflow_id=workflow_id,
                        force_refresh=request.force_refresh,
                    )
                    if not acquired:
                        result.documents.append(WorkflowDocumentResult(
                            doc_key=document.doc_key,
                            document_id=document.document_id,
                            action=(
                                "skipped_in_progress"
                                if current.extraction_status is ExtractionStatus.RUNNING
                                else "skipped_already_extracted"
                            ),
                        ))
                        continue
                draft = await self._repository.find_active_draft(
                    user_id=request.actor_id,
                    document_id=document.document_id,
                    content_sha256=document.content_sha256,
                ) if extraction_mode == "COMMIT" else None
                extractor = self._select_extractor(extraction_mode, draft is not None)
                grant = await self._repository.issue_draft_access_grant(
                    user_id=request.actor_id,
                    document_id=document.document_id,
                    content_sha256=document.content_sha256,
                    purpose="commit" if draft else "preview",
                    draft_id=draft.draft_id if draft else None,
                ) if extraction_mode == "PREVIEW" or draft else None
                agent_result = await extractor(self._extraction_instruction(
                    document=document,
                    extraction_mode=extraction_mode,
                    access_token=grant.token if grant else None,
                ))
                expected_tool = (
                    "threat_extraction_preview"
                    if extraction_mode == "PREVIEW"
                    else "commit_extraction_draft" if draft else "threat_extraction_write"
                )
                if not self._has_successful_tool_result(agent_result, expected_tool):
                    raise RuntimeError(f"B 未调用要求的受控出口: {expected_tool}")
                if extraction_mode == "COMMIT":
                    await self._repository.mark_extraction_completed(
                        document.doc_key,
                        document.content_sha256,
                        workflow_id,
                    )
                result.documents.append(WorkflowDocumentResult(
                    doc_key=document.doc_key,
                    document_id=document.document_id,
                    action="extraction_previewed" if extraction_mode == "PREVIEW" else "extracted",
                ))
            except Exception as exc:
                if extraction_mode == "COMMIT":
                    try:
                        await self._repository.mark_stage_failed(
                            document.doc_key,
                            "extraction",
                            str(exc),
                            workflow_id,
                        )
                    except ValueError:
                        pass
                result.failures.append(f"实体关系抽取失败：{document.doc_key}")
        return result

    async def _pending_documents(self, source_id: str | None) -> list[CanonicalDocument]:
        """读取已格式化但尚未为当前正文完成抽取的规范文档。"""
        records = await self._repository.list_processing(source_id)
        candidates = [
            record for record in records
            if record.document_id
            and record.formatting_status.value == "completed"
            and record.extraction_status is not ExtractionStatus.COMPLETED
        ]
        return [
            await self._document_gateway.get_by_id(record.document_id)
            for record in candidates
            if record.document_id is not None
        ]

    async def _list_processing(
        self,
        request: IntelligenceWorkflowRequest,
        workflow_id: str,
    ) -> IntelligenceWorkflowResult:
        """返回处理状态，而不触发采集、模型调用或数据库业务写入。"""
        records = await self._repository.list_processing(request.source_id)
        documents = [
            WorkflowDocumentResult(
                doc_key=record.doc_key,
                document_id=record.document_id,
                action=f"{record.formatting_status.value}/{record.extraction_status.value}",
                detail=record.last_error,
            )
            for record in records
        ]
        return IntelligenceWorkflowResult(
            workflow_id=workflow_id,
            mode=request.mode,
            documents=documents,
        )

    @staticmethod
    def _source_fingerprint(document: CollectedDocument) -> str:
        """为初步抓取正文生成幂等指纹，避免无变化页面重复进入 A。"""
        payload = json.dumps({
            "title": document.title,
            "url": document.url,
            "published_at": document.published_at,
            "content": document.preliminary_content,
        }, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    async def _issue_formatting_grants(self, documents: list[CollectedDocument]) -> dict[str, str]:
        """为当前批次文章分别签发写入授权，禁止 A 用一篇文章的身份写入另一篇。"""
        access_tokens: dict[str, str] = {}
        for document in documents:
            grant = await self._repository.issue_formatting_access_grant(
                doc_key=document.doc_key,
                source_id=document.source_id,
                source_name=document.source_name,
                external_id=document.external_id,
                url=document.url,
                published_at=document.published_at,
            )
            access_tokens[document.doc_key] = grant.token
        return access_tokens

    @staticmethod
    def _extraction_instruction(
        *,
        document: CanonicalDocument,
        extraction_mode: str,
        access_token: str | None,
    ) -> str:
        """把确定的 B 模式和文档标识传给模型，不让模型猜测是否允许写库。"""
        return (
            f"执行 {extraction_mode} 工作流。document_id={document.document_id}，"
            f"content_sha256={document.content_sha256}。"
            "先读取完整格式化文档，按 Skill 提取并验证实体、关系和 evidence。"
            + (
                f"使用 access_token={access_token} 只保存结构化预览草稿，绝不调用图谱写入工具。"
                if extraction_mode == "PREVIEW"
                else (
                    f"使用 access_token={access_token}，只调用 commit_extraction_draft。"
                    if access_token else "完成校验后只调用一次图谱写入工具。"
                )
            )
        )

    def _select_extractor(self, extraction_mode: str, has_draft: bool) -> AgentRunner:
        """按工作流模式选择最小权限 B 图，预览图不加载图谱写入工具。"""
        if extraction_mode == "PREVIEW":
            return self._agents.preview_extractor
        if has_draft:
            return self._agents.draft_commit_extractor
        return self._agents.commit_extractor

    @staticmethod
    def _has_successful_tool_result(result: object, tool_name: str) -> bool:
        """要求 B 留下成功工具结果，避免仅凭模型文本或失败调用标记阶段完成。"""
        messages: Any = result.get("messages", []) if isinstance(result, dict) else []
        for message in messages:
            name = message.get("name") if isinstance(message, dict) else getattr(message, "name", None)
            status = message.get("status") if isinstance(message, dict) else getattr(message, "status", None)
            message_type = message.get("type") if isinstance(message, dict) else getattr(message, "type", None)
            if name == tool_name and message_type == "tool" and status in {None, "success"}:
                return True
        return False
