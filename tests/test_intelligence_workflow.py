"""确定性情报工作流的主要分支测试。"""

from __future__ import annotations

import json
import unittest
from unittest.mock import AsyncMock, patch

from intelligence_workflow.document_gateway import CanonicalDocument
from intelligence_workflow.schema import (
    DraftAccessGrant,
    FormattingAccessGrant,
    ExtractionStatus,
    FormattingStatus,
    IntelligenceWorkflowMode,
    IntelligenceWorkflowRequest,
    ProcessingRecord,
)
from intelligence_workflow.workflow import IntelligenceWorkflow, WorkflowAgents
from intel_ingestor.schema import CollectedDocument, CollectionOutcome, CollectionReport


class FakeRepository:
    """以最小状态模拟工作流仓储，隔离业务分支测试。"""

    def __init__(self) -> None:
        self.records: dict[str, ProcessingRecord] = {}
        self.failed: list[tuple[str, str]] = []

    async def ensure_schema(self) -> None:
        return None

    async def begin_formatting(self, **kwargs):
        record = self.records.get(kwargs["doc_key"])
        if record and record.formatting_status is FormattingStatus.COMPLETED:
            return False, record
        record = ProcessingRecord(
            doc_key=kwargs["doc_key"], source_id=kwargs["source_id"], url=kwargs["url"],
            formatting_status=FormattingStatus.RUNNING,
        )
        self.records[record.doc_key] = record
        return True, record

    async def record_formatted_document(self, **kwargs):
        record = ProcessingRecord(
            doc_key=kwargs["doc_key"], source_id=kwargs["source_id"],
            document_id=kwargs["document_id"], formatted_content_sha256=kwargs["content_sha256"],
            formatting_status=FormattingStatus.COMPLETED,
            extraction_status=ExtractionStatus.NOT_REQUESTED,
        )
        self.records[record.doc_key] = record
        return record

    async def get_processing(self, doc_key: str):
        return self.records.get(doc_key)

    async def begin_extraction(self, *, doc_key: str, workflow_id: str, force_refresh: bool):
        record = self.records[doc_key].model_copy(update={"extraction_status": ExtractionStatus.RUNNING})
        self.records[doc_key] = record
        return True, record

    async def mark_extraction_completed(self, doc_key: str, content_sha256: str, workflow_id: str):
        record = self.records[doc_key].model_copy(update={
            "extraction_status": ExtractionStatus.COMPLETED,
            "extracted_content_sha256": content_sha256,
        })
        self.records[doc_key] = record
        return record

    async def mark_stage_failed(self, doc_key: str, stage: str, error: str, workflow_id: str):
        self.failed.append((doc_key, stage))
        return self.records[doc_key]

    async def find_active_draft(self, **kwargs):
        return None

    async def issue_draft_access_grant(self, **kwargs):
        return DraftAccessGrant(token="test-token", **kwargs)

    async def issue_formatting_access_grant(self, **kwargs):
        return FormattingAccessGrant(token="format-token", **kwargs)

    async def list_processing(self, source_id: str | None = None):
        return list(self.records.values())


class FakeDocumentGateway:
    """按稳定文档键返回 A 已写入的规范文档。"""

    def __init__(self, document: CanonicalDocument) -> None:
        self.document = document

    async def get_by_key(self, doc_key: str) -> CanonicalDocument:
        assert doc_key == self.document.doc_key
        return self.document

    async def get_by_id(self, document_id: int) -> CanonicalDocument:
        assert document_id == self.document.document_id
        return self.document


class FakeDeliverableRegistry:
    """记录工作流登记参数，不依赖沙箱或持久化存储。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def register(self, **kwargs):
        self.calls.append(kwargs)
        return [{
            "type": "sandbox_deliverable",
            "artifact_id": "a" * 32,
            "path": "/deliverables/formatted.md",
            "filename": "formatted.md",
            "mime_type": "text/markdown",
            "label": "格式化文章",
        }]


def collected_document() -> CollectedDocument:
    """提供一篇可由来源采集器返回的最小文章。"""
    return CollectedDocument(
        doc_key="cncert:1", source_id="cncert", source_name="CNCERT", external_id="1",
        title="标题", url="https://example.test/1", published_at=None,
        preliminary_content="正文",
    )


class IntelligenceWorkflowTests(unittest.IsolatedAsyncioTestCase):
    """验证格式化、入库抽取、预览和待处理筛选的阶段边界。"""

    def setUp(self) -> None:
        self.document = CanonicalDocument(
            document_id=1, doc_key="cncert:1", source_id="cncert", source_name="CNCERT", content="整理后的正文",
            content_sha256="hash", external_id="1", url="https://example.test/1",
        )
        self.repository = FakeRepository()
        self.formatter = AsyncMock()
        self.commit_extractor = AsyncMock(return_value={"messages": [{
            "type": "tool", "name": "threat_extraction_write", "status": "success",
        }]})
        self.preview_extractor = AsyncMock(return_value={"messages": [{
            "type": "tool", "name": "threat_extraction_preview", "status": "success",
        }]})
        self.draft_commit_extractor = AsyncMock()
        self.workflow = IntelligenceWorkflow(
            repository=self.repository,
            document_gateway=FakeDocumentGateway(self.document),
            agents=WorkflowAgents(
                formatter=self.formatter,
                preview_extractor=self.preview_extractor,
                commit_extractor=self.commit_extractor,
                draft_commit_extractor=self.draft_commit_extractor,
            ),
        )

    async def test_format_only_runs_a_and_not_b(self) -> None:
        collection = CollectionReport("cncert", (CollectionOutcome(
            url="https://example.test/1", status="ok", document=collected_document(),
        ),))
        with patch("intelligence_workflow.workflow.collect_source", AsyncMock(return_value=collection)):
            result = await self.workflow.run(IntelligenceWorkflowRequest(
                mode=IntelligenceWorkflowMode.FORMAT_ONLY,
                actor_id="user-1",
                source_id="cncert",
        ))
        self.formatter.assert_awaited_once()
        self.preview_extractor.assert_not_awaited()
        self.commit_extractor.assert_not_awaited()
        self.draft_commit_extractor.assert_not_awaited()
        self.assertEqual(result.documents[0].action, "formatted")

    async def test_existing_document_full_ingest_runs_b_and_marks_completion(self) -> None:
        result = await self.workflow.run(IntelligenceWorkflowRequest(
            mode=IntelligenceWorkflowMode.INGEST_FULL,
            actor_id="user-1",
            document_ids=[1],
        ))
        self.formatter.assert_not_awaited()
        self.commit_extractor.assert_awaited_once()
        self.assertIn("COMMIT", self.commit_extractor.await_args.args[0])
        self.assertEqual(result.documents[0].action, "extracted")
        self.assertEqual(self.repository.records["cncert:1"].extraction_status, ExtractionStatus.COMPLETED)

    async def test_preview_does_not_mark_document_as_extracted(self) -> None:
        result = await self.workflow.run(IntelligenceWorkflowRequest(
            mode=IntelligenceWorkflowMode.EXTRACT_PREVIEW,
            actor_id="user-1",
            document_ids=[1],
        ))
        self.assertIn("PREVIEW", self.preview_extractor.await_args.args[0])
        self.assertEqual(result.documents[0].action, "extraction_previewed")
        self.assertEqual(self.repository.records["cncert:1"].extraction_status, ExtractionStatus.NOT_REQUESTED)

    async def test_source_document_in_another_formatting_run_does_not_enter_b(self) -> None:
        collection = CollectionReport("cncert", (CollectionOutcome(
            url="https://example.test/1", status="ok", document=collected_document(),
        ),))
        self.repository.begin_formatting = AsyncMock(return_value=(False, ProcessingRecord(
            doc_key="cncert:1", document_id=1, formatting_status=FormattingStatus.RUNNING,
        )))
        with patch("intelligence_workflow.workflow.collect_source", AsyncMock(return_value=collection)):
            result = await self.workflow.run(IntelligenceWorkflowRequest(
                mode=IntelligenceWorkflowMode.INGEST_FULL,
                actor_id="user-1",
                source_id="cncert",
            ))
        self.formatter.assert_not_awaited()
        self.preview_extractor.assert_not_awaited()
        self.commit_extractor.assert_not_awaited()
        self.assertEqual(result.documents[0].action, "skipped_in_progress")

    async def test_listing_failure_is_returned_as_a_workflow_failure(self) -> None:
        collection = CollectionReport("cncert", listing_error="upstream unavailable")
        with patch("intelligence_workflow.workflow.collect_source", AsyncMock(return_value=collection)):
            result = await self.workflow.run(IntelligenceWorkflowRequest(
                mode=IntelligenceWorkflowMode.FORMAT_ONLY,
                actor_id="user-1",
                source_id="cncert",
            ))
        self.assertEqual(result.failures, ["来源列表抓取失败：cncert"])
        self.formatter.assert_not_awaited()

    async def test_skipped_collection_outcome_is_visible_to_the_caller(self) -> None:
        collection = CollectionReport("cncert", (CollectionOutcome(
            url="https://example.test/blocked", status="skipped", reason="来源规则已跳过",
        ),))
        with patch("intelligence_workflow.workflow.collect_source", AsyncMock(return_value=collection)):
            result = await self.workflow.run(IntelligenceWorkflowRequest(
                mode=IntelligenceWorkflowMode.FORMAT_ONLY,
                actor_id="user-1",
                source_id="cncert",
            ))
        self.assertEqual(result.documents[0].action, "skipped_collection")
        self.assertEqual(result.documents[0].detail, "来源规则已跳过")

    async def test_format_export_registers_only_actual_tool_output(self) -> None:
        """用户请求下载时，A 的统一工具结果才会成为 artifact。"""
        registry = FakeDeliverableRegistry()
        self.workflow._deliverable_registry = registry
        self.formatter.return_value = {"messages": [{
            "type": "tool",
            "name": "write_deliverable",
            "content": json.dumps({
                "type": "deliverable_spec",
                "path": "/deliverables/formatted.md",
                "filename": "formatted.md",
                "mime_type": "text/markdown",
                "label": "格式化文章",
            }),
        }]}
        collection = CollectionReport("cncert", (CollectionOutcome(
            url="https://example.test/1", status="ok", document=collected_document(),
        ),))
        with patch("intelligence_workflow.workflow.collect_source", AsyncMock(return_value=collection)):
            result = await self.workflow.run(IntelligenceWorkflowRequest(
                mode=IntelligenceWorkflowMode.FORMAT_ONLY,
                actor_id="user-1",
                source_id="cncert",
                requested_deliverables=["formatted_markdown"],
            ))
        self.assertEqual(len(registry.calls), 1)
        self.assertEqual(registry.calls[0]["user_id"], "user-1")
        self.assertEqual(result.deliverables[0].filename, "formatted.md")

    async def test_scheduler_never_registers_user_deliverables(self) -> None:
        """定期采集的调度身份不能创建无归属下载件。"""
        registry = FakeDeliverableRegistry()
        self.workflow._deliverable_registry = registry
        self.formatter.return_value = {"messages": [{
            "name": "write_deliverable",
            "content": json.dumps({
                "type": "deliverable_spec",
                "path": "/deliverables/formatted.md",
                "filename": "formatted.md",
                "mime_type": "text/markdown",
                "label": "格式化文章",
            }),
        }]}
        collection = CollectionReport("cncert", (CollectionOutcome(
            url="https://example.test/1", status="ok", document=collected_document(),
        ),))
        with patch("intelligence_workflow.workflow.collect_source", AsyncMock(return_value=collection)):
            result = await self.workflow.run(IntelligenceWorkflowRequest(
                mode=IntelligenceWorkflowMode.FORMAT_ONLY,
                actor_id="system-scheduler",
                source_id="cncert",
                requested_deliverables=["formatted_markdown"],
            ))
        self.assertEqual(registry.calls, [])
        self.assertEqual(result.deliverables, [])


if __name__ == "__main__":
    unittest.main()
