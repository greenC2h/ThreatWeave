"""工作流访问 Java 文档 CRUD 的受控 HTTP 适配器。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from mcp_server.http_base import request_threatweave_api
from mcp_server.server_config import JAVA_API_BASE_URL


@dataclass(frozen=True)
class CanonicalDocument:
    """工作流需要的规范文档最小视图。"""

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
        """将 Java 的蛇形字段响应转换为稳定的 Python 类型。"""
        try:
            return cls(
                document_id=int(value["id"]),
                doc_key=str(value["doc_key"]),
                source_id=value.get("source_id"),
                source_name=str(value["source_name"]),
                title=value.get("title"),
                content=str(value["content"]),
                content_sha256=str(value["content_sha256"]),
                external_id=value.get("external_id"),
                url=value.get("url"),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("ThreatWeave 文档接口返回了不完整的数据") from exc


class DocumentGateway:
    """以短生命周期 HTTP 客户端读取 Java 所拥有的规范文档。"""

    async def get_by_id(self, document_id: int) -> CanonicalDocument:
        """读取指定规范文档。"""
        async with self._client() as client:
            response = await request_threatweave_api(
                client, "GET", f"/threatweave/documents/{document_id}"
            )
        return CanonicalDocument.from_api(response)

    async def get_by_key(self, doc_key: str) -> CanonicalDocument:
        """在 A 写入后按稳定键确认文档 ID 与规范正文指纹。"""
        async with self._client() as client:
            response = await request_threatweave_api(
                client,
                "GET",
                "/threatweave/documents/by-key",
                params={"docKey": doc_key},
            )
        return CanonicalDocument.from_api(response)

    async def get_extraction(self, document_id: int) -> dict[str, Any]:
        """读取已确认写入知识图谱的实体、关系和证据，供确定性导出使用。"""
        async with self._client() as client:
            response = await request_threatweave_api(
                client, "GET", f"/threatweave/documents/{document_id}/extraction"
            )
        if not isinstance(response, dict):
            raise ValueError("ThreatWeave 抽取结果接口返回了无效数据")
        return response

    @staticmethod
    def _client() -> httpx.AsyncClient:
        """创建受限超时的客户端，避免工作流持有跨任务连接。"""
        return httpx.AsyncClient(base_url=JAVA_API_BASE_URL, timeout=httpx.Timeout(20.0))
