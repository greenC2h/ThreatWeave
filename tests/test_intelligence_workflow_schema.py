"""情报工作流请求边界与状态枚举测试。"""

from __future__ import annotations

import unittest

from pydantic import ValidationError

from intelligence_workflow.schema import IntelligenceWorkflowMode, IntelligenceWorkflowRequest


class IntelligenceWorkflowRequestTests(unittest.TestCase):
    """验证模式与目标组合，阻止含糊请求进入异步执行层。"""

    def test_source_article_request_is_valid(self) -> None:
        request = IntelligenceWorkflowRequest(
            mode=IntelligenceWorkflowMode.INGEST_FULL,
            actor_id="system-scheduler",
            source_id="cncert_cc",
            article_url="https://example.test/article",
        )
        self.assertEqual(request.max_articles, 3)

    def test_existing_document_request_is_valid(self) -> None:
        request = IntelligenceWorkflowRequest(
            mode=IntelligenceWorkflowMode.EXTRACT_PREVIEW,
            actor_id="user-1",
            document_ids=[3],
        )
        self.assertEqual(request.document_ids, [3])

    def test_non_list_request_requires_one_target_kind(self) -> None:
        with self.assertRaises(ValidationError):
            IntelligenceWorkflowRequest(
                mode=IntelligenceWorkflowMode.FORMAT_ONLY,
                actor_id="user-1",
            )

    def test_document_ids_and_source_cannot_be_combined(self) -> None:
        with self.assertRaises(ValidationError):
            IntelligenceWorkflowRequest(
                mode=IntelligenceWorkflowMode.INGEST_FULL,
                actor_id="user-1",
                source_id="cncert_cc",
                document_ids=[1],
            )

    def test_list_processing_accepts_optional_source_filter_only(self) -> None:
        request = IntelligenceWorkflowRequest(
            mode=IntelligenceWorkflowMode.LIST_PROCESSING,
            actor_id="user-1",
            source_id="cncert_cc",
        )
        self.assertEqual(request.source_id, "cncert_cc")

    def test_extract_pending_can_process_all_sources(self) -> None:
        request = IntelligenceWorkflowRequest(
            mode=IntelligenceWorkflowMode.EXTRACT_PENDING,
            actor_id="user-1",
        )
        self.assertIsNone(request.source_id)


if __name__ == "__main__":
    unittest.main()
