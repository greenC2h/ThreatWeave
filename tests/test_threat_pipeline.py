"""ThreatPipeline 的完整导入与幂等行为测试。"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from intel_ingestor.schema import CollectedDocument, CollectionOutcome, CollectionReport
from threat_pipeline.gateway import CanonicalDocument
from threat_pipeline.pipeline import ThreatPipeline
from threat_pipeline.schema import ProcessingRecord, ThreatPipelineRequest


class FakeRepository:
    """隔离 Pipeline 状态机，记录每篇文章是否已经完成。"""

    def __init__(self, acquired: bool = True) -> None:
        self.acquired = acquired
        self.completed: list[str] = []
        self.failed: list[tuple[str, str]] = []

    async def ensure_schema(self) -> None:
        return None

    async def begin(self, **kwargs):
        return self.acquired, ProcessingRecord(
            doc_key=kwargs["doc_key"], document_id=7 if not self.acquired else None,
            status="completed" if not self.acquired else "running",
        )

    async def record_formatted(self, **kwargs) -> None:
        return None

    async def mark_completed(self, doc_key: str, run_id: str) -> None:
        self.completed.append(doc_key)

    async def mark_failed(self, doc_key: str, run_id: str, stage: str, error: str) -> None:
        self.failed.append((doc_key, stage))


class FakeGateway:
    """验证 Pipeline 只通过类型化 Java 适配器写入。"""

    def __init__(self) -> None:
        self.extractions: list[tuple[int, list[dict], list[dict]]] = []

    async def upsert_document(self, document, content: str, title: str | None) -> CanonicalDocument:
        return CanonicalDocument(
            document_id=7, doc_key=document.doc_key, source_id=document.source_id,
            source_name=document.source_name, content=content, content_sha256="content-hash",
            title=title, external_id=document.external_id, url=document.url,
        )

    async def replace_extraction(self, document_id: int, entities: list[dict], relations: list[dict]):
        self.extractions.append((document_id, entities, relations))
        return {"documentId": document_id}


class FakeModel:
    """模拟模型只返回转换数据，从不直接访问数据库。"""

    async def format_batch(self, documents: list[dict[str, str]]) -> list[dict[str, str]]:
        return [{"doc_key": item["doc_key"], "title": item["title"], "content": "攻击者使用 198.51.100.7。"} for item in documents]

    async def extract_chunk(self, document_id: int, chunk_index: int, content: str) -> dict:
        return {
            "entities": [{
                "entityType": "ipv4", "canonicalValue": "198.51.100.7",
                "semanticRole": "malicious_infrastructure", "evidence": "198.51.100.7",
            }],
            "relations": [],
        }


def collected_document() -> CollectedDocument:
    return CollectedDocument(
        doc_key="source:1", source_id="source", source_name="Source", external_id="1",
        title="标题", url="https://example.test/1", published_at=None, preliminary_content="原始正文",
    )


class ThreatPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_pipeline_collects_formats_extracts_and_writes_through_gateway(self) -> None:
        repository = FakeRepository()
        gateway = FakeGateway()
        pipeline = ThreatPipeline(repository=repository, gateway=gateway, model=FakeModel())
        report = CollectionReport("source", (CollectionOutcome("https://example.test/1", "ok", document=collected_document()),))

        with patch("threat_pipeline.pipeline.collect_source", AsyncMock(return_value=report)):
            result = await pipeline.run(ThreatPipelineRequest(actor_id="u1", source_id="source"))

        self.assertEqual(result.documents[0].action, "ingested")
        self.assertEqual(repository.completed, ["source:1"])
        self.assertEqual(gateway.extractions[0][0], 7)
        self.assertEqual(gateway.extractions[0][1][0]["evidence"][0]["charStart"], 6)

    async def test_completed_unchanged_document_is_not_sent_to_model_or_gateway(self) -> None:
        pipeline = ThreatPipeline(repository=FakeRepository(acquired=False), gateway=FakeGateway(), model=FakeModel())
        report = CollectionReport("source", (CollectionOutcome("https://example.test/1", "ok", document=collected_document()),))

        with patch("threat_pipeline.pipeline.collect_source", AsyncMock(return_value=report)):
            result = await pipeline.run(ThreatPipelineRequest(actor_id="u1", source_id="source"))

        self.assertEqual(result.documents[0].action, "skipped_unchanged")

    async def test_direct_article_url_does_not_require_source_allowlist(self) -> None:
        pipeline = ThreatPipeline(repository=FakeRepository(), gateway=FakeGateway(), model=FakeModel())
        report = CollectionReport(
            "direct_url",
            (CollectionOutcome("https://unregistered.example/article", "ok", document=collected_document()),),
        )

        with patch("threat_pipeline.pipeline.collect_source", AsyncMock(return_value=report)) as collect:
            result = await pipeline.run(
                ThreatPipelineRequest(actor_id="u1", article_url="https://unregistered.example/article")
            )

        collect.assert_awaited_once_with(
            "direct_url", max_articles=3, article_url="https://unregistered.example/article"
        )
        self.assertEqual(result.documents[0].action, "ingested")

    def test_request_requires_source_or_article_url(self) -> None:
        with self.assertRaises(ValueError):
            ThreatPipelineRequest(actor_id="u1")
