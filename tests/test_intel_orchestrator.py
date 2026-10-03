"""intel_ingestion_orchestrator 的批次边界测试。"""

from __future__ import annotations

import unittest

from intel_ingestor.orchestrator import OrchestrationReport, split_document_batches
from intel_ingestor.schema import CollectedDocument


def make_document(index: int, size: int) -> CollectedDocument:
    """构造只含最小元数据的文章草稿。"""
    return CollectedDocument(
        doc_key=f"key-{index}", source_id="source", source_name="来源", external_id=str(index),
        title=f"标题 {index}", url=f"https://example.test/{index}", published_at=None,
        preliminary_content="x" * size,
    )


class BatchSplitTests(unittest.TestCase):
    def test_fourth_document_starts_a_new_batch_when_count_limit_is_three(self) -> None:
        batches = split_document_batches([make_document(index, 10) for index in range(4)], max_characters=1000)
        self.assertEqual([[document.doc_key for document in batch] for batch in batches], [["key-0", "key-1", "key-2"], ["key-3"]])

    def test_character_budget_starts_a_new_batch_before_count_limit(self) -> None:
        batches = split_document_batches([make_document(0, 20), make_document(1, 20)], max_characters=60)
        self.assertEqual([[document.doc_key for document in batch] for batch in batches], [["key-0"], ["key-1"]])

    def test_oversized_document_is_kept_as_one_complete_batch(self) -> None:
        batches = split_document_batches([make_document(0, 100)], max_characters=10)
        self.assertEqual([[document.doc_key for document in batch] for batch in batches], [["key-0"]])

    def test_summary_does_not_claim_async_downstream_completion(self) -> None:
        summary = OrchestrationReport("source", 1, 1, ()).to_summary()
        self.assertIn("已将 1 篇文章提交", summary)
        self.assertIn("不提供文档 ID 或最终写入状态", summary)


if __name__ == "__main__":
    unittest.main()
