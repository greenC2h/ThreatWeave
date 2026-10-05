"""固定执行采集、清洗、写文档、抽取和写图谱的 ThreatPipeline。"""

from __future__ import annotations

import hashlib
from uuid import uuid4

from intel_ingestor.ingestor import collect_source
from intel_ingestor.sources import resolve_source_for_article
from threat_pipeline.batching import split_document_batches
from threat_pipeline.extraction import build_extraction_payload, split_document_content
from threat_pipeline.gateway import CanonicalDocument, ThreatWeaveCommandGateway
from threat_pipeline.models import JsonPipelineModel, PipelineModel
from threat_pipeline.repository import PipelineRepository
from threat_pipeline.schema import PipelineDocumentResult, ThreatPipelineRequest, ThreatPipelineResult


class ThreatPipeline:
    """以确定性代码控制全文导入，模型只负责清洗和抽取转换。"""

    def __init__(
        self,
        *,
        repository: PipelineRepository | None = None,
        gateway: ThreatWeaveCommandGateway | None = None,
        model: PipelineModel | None = None,
    ) -> None:
        self._repository = repository or PipelineRepository()
        self._gateway = gateway or ThreatWeaveCommandGateway()
        self._model = model or JsonPipelineModel()

    async def run(self, request: ThreatPipelineRequest) -> ThreatPipelineResult:
        """完整导入一批文章；正文来源未变且已完成时不重复处理。"""
        await self._repository.ensure_schema()
        source_id = request.source_id or resolve_source_for_article(str(request.article_url))
        collection = await collect_source(
            source_id, max_articles=request.max_articles, article_url=request.article_url,
        )
        result = ThreatPipelineResult(run_id=str(uuid4()))
        if collection.listing_error:
            result.failures.append(f"来源列表抓取失败：{source_id}")
        for outcome in collection.outcomes:
            if outcome.status == "failed":
                result.failures.append(f"采集失败：{outcome.url}")
            elif outcome.status == "skipped":
                result.documents.append(PipelineDocumentResult(action="skipped_collection", detail=outcome.reason or outcome.url))

        collected = [outcome.document for outcome in collection.outcomes if outcome.document]
        for batch in split_document_batches(collected):
            await self._run_batch(batch, request, result)
        return result

    async def _run_batch(self, documents, request: ThreatPipelineRequest, result: ThreatPipelineResult) -> None:
        runnable = []
        for document in documents:
            acquired, record = await self._repository.begin(
                doc_key=document.doc_key, source_id=document.source_id, external_id=document.external_id,
                url=document.url, source_fingerprint=self._fingerprint(document.preliminary_content),
                run_id=result.run_id, force_refresh=request.force_refresh,
            )
            if acquired:
                runnable.append(document)
            else:
                result.documents.append(PipelineDocumentResult(
                    action="skipped_unchanged" if record.status == "completed" else "skipped_in_progress",
                    doc_key=document.doc_key, document_id=record.document_id, title=document.title,
                ))
        if not runnable:
            return
        try:
            formatted = await self._model.format_batch([
                {"doc_key": item.doc_key, "title": item.title, "content": item.preliminary_content}
                for item in runnable
            ])
            formatted_by_key = self._validate_formatted(runnable, formatted)
        except Exception as exc:
            for document in runnable:
                await self._repository.mark_failed(document.doc_key, result.run_id, "formatting", str(exc))
                result.failures.append(f"清洗失败：{document.url}")
            return
        for document in runnable:
            try:
                formatted_document = formatted_by_key[document.doc_key]
                canonical = await self._gateway.upsert_document(
                    document, formatted_document["content"], formatted_document["title"],
                )
                await self._repository.record_formatted(
                    doc_key=document.doc_key, document_id=canonical.document_id,
                    content_sha256=canonical.content_sha256, run_id=result.run_id,
                )
                await self._extract_and_write(canonical)
                await self._repository.mark_completed(document.doc_key, result.run_id)
                result.documents.append(PipelineDocumentResult(
                    action="ingested", doc_key=document.doc_key, document_id=canonical.document_id,
                    title=canonical.title,
                ))
            except Exception as exc:
                await self._repository.mark_failed(document.doc_key, result.run_id, "extraction", str(exc))
                result.failures.append(f"抽取失败：{document.url}")

    async def _extract_and_write(self, document: CanonicalDocument) -> None:
        all_entities: list[dict] = []
        all_relations: list[dict] = []
        for chunk_index, chunk in enumerate(split_document_content(document.content)):
            raw = await self._model.extract_chunk(document.document_id, chunk_index, chunk)
            entities = raw.get("entities", []) if isinstance(raw, dict) else []
            relations = raw.get("relations", []) if isinstance(raw, dict) else []
            if not isinstance(entities, list) or not isinstance(relations, list):
                raise ValueError("抽取模型必须返回 entities 和 relations 数组")
            all_entities.extend(entities)
            all_relations.extend(relations)
        entities, relations, rejected = build_extraction_payload(
            document.content, {"entities": all_entities, "relations": all_relations},
        )
        if rejected:
            # 逐块候选可能因跨块关系或重复证据失效；保留已通过的事实但不把拒绝项写入。
            pass
        await self._gateway.replace_extraction(document.document_id, entities, relations)

    @staticmethod
    def _validate_formatted(documents, formatted: list[dict[str, str]]) -> dict[str, dict[str, str]]:
        expected = {document.doc_key for document in documents}
        values: dict[str, dict[str, str]] = {}
        for item in formatted:
            key = item.get("doc_key", "")
            content = item.get("content", "").strip()
            if key not in expected or key in values or not content:
                raise ValueError("清洗模型返回了缺失、重复或未授权的文档")
            values[key] = {"content": content, "title": item.get("title", "").strip()}
        if set(values) != expected:
            raise ValueError("清洗模型没有返回当前批次的全部文章")
        return values

    @staticmethod
    def _fingerprint(content: str) -> str:
        return hashlib.sha256(content.encode("utf-8")).hexdigest()
