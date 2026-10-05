"""ThreatPipeline 到 Java ThreatWeave 命令接口的 HTTP 适配器。"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

import httpx

from mcp_server.server_config import JAVA_API_BASE_URL


@dataclass(frozen=True)
class CanonicalDocument:
    """Pipeline 处理和确认规范正文所需的最小视图。"""

    document_id: int
    doc_key: str
    source_id: str | None
    source_name: str
    content: str
    content_sha256: str
    title: str | None = None
    external_id: str | None = None
    url: str | None = None

    @classmethod
    def from_api(cls, value: dict[str, Any]) -> "CanonicalDocument":
        try:
            return cls(
                document_id=int(value["id"]), doc_key=str(value["doc_key"]),
                source_id=value.get("source_id"), source_name=str(value["source_name"]),
                content=str(value["content"]), content_sha256=str(value["content_sha256"]),
                title=value.get("title"), external_id=value.get("external_id"), url=value.get("url"),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("ThreatWeave 文档接口返回了不完整的数据") from exc


class ThreatWeaveCommandGateway:
    """由 Pipeline 独占的类型化 Java 写入和确认适配器。"""

    async def upsert_document(self, document: Any, content: str, title: str | None) -> CanonicalDocument:
        """写入模型清洗后的正文，身份字段始终来自采集器。"""
        payload = {
            "docKey": document.doc_key,
            "sourceId": document.source_id,
            "sourceName": document.source_name,
            "externalId": document.external_id,
            "title": title or document.title,
            "url": document.url,
            "publishedAt": document.published_at,
            "content": content,
            "contentSha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        }
        async with self._client() as client:
            response = await self._request(client, "POST", "/threatweave/documents", json=payload)
        return CanonicalDocument.from_api(response)

    async def replace_extraction(
        self, document_id: int, entities: list[dict[str, Any]], relations: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """原子替换一篇文章的抽取事实，由 Java 维护共享图谱的一致性。"""
        async with self._client() as client:
            response = await self._request(client, "POST", "/threatweave/extractions", json={
                "documentId": document_id, "entities": entities, "relations": relations,
            })
        if not isinstance(response, dict):
            raise ValueError("ThreatWeave 抽取写入接口返回了无效数据")
        return response

    @staticmethod
    def _client() -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=JAVA_API_BASE_URL, timeout=httpx.Timeout(30.0))

    @staticmethod
    async def _request(client: httpx.AsyncClient, method: str, path: str, **kwargs: Any) -> Any:
        """调用 Java 命令端点，并将 HTTP/业务协议失败转换为 Pipeline 失败。"""
        try:
            response = await client.request(method, path, **kwargs)
            response.raise_for_status()
            envelope = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise RuntimeError("ThreatWeave Java 命令接口未确认写入成功") from exc
        if not isinstance(envelope, dict) or envelope.get("code") != 200:
            raise RuntimeError("ThreatWeave Java 命令接口拒绝了写入请求")
        return envelope.get("data")
