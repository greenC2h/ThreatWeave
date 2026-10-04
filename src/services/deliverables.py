"""统一校验、写入和登记用户可下载交付件。"""

from __future__ import annotations

import json
import re
from pathlib import PurePosixPath
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from langchain_core.tools import BaseTool, tool

from agent.backends.sandbox_proxy import SandboxBackendProxy


DELIVERABLE_MIME_TYPES = frozenset({"text/markdown", "text/html", "application/json"})
_DELIVERABLE_EXTENSION_BY_MIME = {
    "text/markdown": ".md",
    "text/html": ".html",
    "application/json": ".json",
}
_DELIVERABLE_FILENAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,119}$")
_MAX_DELIVERABLE_BYTES = 4 * 1024 * 1024
_DELIVERABLE_NAMESPACE_PREFIX = ("sandbox_deliverables",)


def create_write_deliverable_tool(sandbox_backend: SandboxBackendProxy) -> BaseTool:
    """创建绑定当前沙箱的唯一交付件写入工具。"""

    @tool
    async def write_deliverable(
        filename: str,
        content: str,
        mime_type: str,
        label: str,
    ) -> str:
        """写入一个用户明确要求的 Markdown、HTML 或 JSON 交付件。

        文件只能写入受控的 `/deliverables/` 根目录。工具返回结构化声明，调用方无需
        自行拼接下载 URL、artifact ID 或用户身份。
        """
        specification = normalize_deliverable_spec({
            "filename": filename,
            "content": content,
            "mime_type": mime_type,
            "label": label,
        })
        response = (
            await sandbox_backend.aupload_files([
                (specification["path"], content.encode("utf-8")),
            ])
        )[0]
        if response.error:
            raise RuntimeError("无法写入用户交付件")
        return json.dumps({"type": "deliverable_spec", **specification}, ensure_ascii=False)

    return write_deliverable


def normalize_deliverable_spec(value: dict[str, Any]) -> dict[str, str]:
    """验证交付件元数据并生成唯一允许登记的沙箱路径。"""
    filename = str(value.get("filename", "")).strip()
    mime_type = str(value.get("mime_type", "")).strip()
    label = str(value.get("label", "")).strip()
    content = value.get("content")
    if not _DELIVERABLE_FILENAME_PATTERN.fullmatch(filename):
        raise ValueError("交付件文件名只能包含字母、数字、点、下划线和连字符")
    if mime_type not in DELIVERABLE_MIME_TYPES:
        raise ValueError("交付件类型只支持 Markdown、HTML 或 JSON")
    if not filename.endswith(_DELIVERABLE_EXTENSION_BY_MIME[mime_type]):
        raise ValueError("交付件文件扩展名与 MIME 类型不匹配")
    if not isinstance(content, str) or len(content.encode("utf-8")) > _MAX_DELIVERABLE_BYTES:
        raise ValueError("交付件内容必须是 4 MiB 以内的 UTF-8 文本")
    if not label:
        label = filename
    return {
        "path": f"/deliverables/{filename}",
        "filename": filename,
        "mime_type": mime_type,
        "label": label[:120],
    }


def extract_deliverable_specs(content: Any, depth: int = 0) -> list[dict[str, str]]:
    """从工具结果或历史结构中递归提取可信的交付件声明。"""
    if depth > 5:
        return []
    if isinstance(content, str):
        try:
            return extract_deliverable_specs(json.loads(content), depth + 1)
        except json.JSONDecodeError:
            return []
    if isinstance(content, list):
        return [
            specification
            for item in content
            for specification in extract_deliverable_specs(item, depth + 1)
        ]
    if not isinstance(content, dict):
        return []
    if content.get("type") == "deliverable_spec":
        try:
            # 工具结果本身已写入内容；登记阶段只接受受限路径和显示元数据。
            path = str(content.get("path", ""))
            parsed_path = PurePosixPath(path)
            if (
                not path.startswith("/deliverables/")
                or parsed_path.parent != PurePosixPath("/deliverables")
            ):
                return []
            filename = parsed_path.name
            normalize_deliverable_spec({
                "filename": filename,
                "content": "",
                "mime_type": content.get("mime_type"),
                "label": content.get("label"),
            })
            return [{
                "path": path,
                "filename": filename,
                "mime_type": str(content["mime_type"]),
                "label": str(content.get("label") or filename)[:120],
            }]
        except (TypeError, ValueError):
            return []
    return [
        specification
        for item in content.values()
        for specification in extract_deliverable_specs(item, depth + 1)
    ]


class DeliverableRegistry:
    """将已写入沙箱的受控交付件登记为用户可下载 artifact。"""

    def __init__(self, store: Any) -> None:
        self._store = store

    async def register(
        self,
        *,
        user_id: str,
        delivery_id: str,
        specifications: list[dict[str, str]],
    ) -> list[dict[str, str]]:
        """按用户与本次交付标识登记文件，返回前端需要的稳定 metadata。"""
        registered: list[dict[str, str]] = []
        for specification in self._deduplicate(specifications):
            artifact_id = uuid5(
                NAMESPACE_URL,
                f"deliverable:{delivery_id}:{specification['path']}",
            ).hex
            await self._store.aput(
                _DELIVERABLE_NAMESPACE_PREFIX,
                artifact_id,
                {"user_id": user_id, **specification},
                index=False,
            )
            registered.append({
                "type": "sandbox_deliverable",
                "artifact_id": artifact_id,
                **specification,
            })
        return registered

    @staticmethod
    def _deduplicate(specifications: list[dict[str, str]]) -> list[dict[str, str]]:
        """同一任务重复声明同一路径时只登记最后一个有效文件。"""
        unique: dict[str, dict[str, str]] = {}
        for specification in specifications:
            unique[specification["path"]] = specification
        return list(unique.values())
