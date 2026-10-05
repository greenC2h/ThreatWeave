"""统一交付件工具与登记边界测试。"""

from __future__ import annotations

import json
import unittest

from services.deliverables import (
    DeliverableRegistry,
    create_write_deliverable_tool,
    extract_deliverable_specs,
    normalize_deliverable_spec,
)


class FakeSandbox:
    """捕获上传请求，模拟成功的用户沙箱。"""

    def __init__(self) -> None:
        self.uploads: list[list[tuple[str, bytes]]] = []

    async def aupload_files(self, files):
        self.uploads.append(files)
        return [type("Response", (), {"error": None})()]


class FakeStore:
    """记录 artifact 元数据，不触达真实持久化存储。"""

    def __init__(self) -> None:
        self.entries: dict[tuple[tuple[str, ...], str], dict[str, str]] = {}

    async def aput(self, namespace, key, value, *, index):
        self.entries[(namespace, key)] = value


class DeliverableTests(unittest.IsolatedAsyncioTestCase):
    """验证写入路径、结构化声明和用户归属由服务端收敛。"""

    async def test_tool_writes_only_controlled_deliverable_path(self) -> None:
        sandbox = FakeSandbox()
        tool = create_write_deliverable_tool(sandbox)

        response = await tool.ainvoke({
            "filename": "report.md",
            "content": "# 报告",
            "mime_type": "text/markdown",
            "label": "下载报告",
        })

        specification = json.loads(response)
        self.assertEqual(sandbox.uploads, [[("/deliverables/report.md", "# 报告".encode("utf-8"))]])
        self.assertEqual(specification["type"], "deliverable_spec")
        self.assertEqual(extract_deliverable_specs(response)[0]["filename"], "report.md")

    async def test_tool_normalizes_non_ascii_model_filename(self) -> None:
        """模型的中文展示名称不能导致已经完成的导出事务失败。"""
        sandbox = FakeSandbox()
        tool = create_write_deliverable_tool(sandbox)

        response = await tool.ainvoke({
            "filename": "清洗后的原文.md",
            "content": "# 正文",
            "mime_type": "text/markdown",
            "label": "下载清洗后的原文",
        })

        specification = json.loads(response)
        self.assertRegex(specification["filename"], r"^deliverable-[a-f0-9]{16}\.md$")
        self.assertEqual(specification["label"], "下载清洗后的原文")

    async def test_registry_binds_metadata_to_current_user(self) -> None:
        store = FakeStore()
        registered = await DeliverableRegistry(store).register(
            user_id="user-1",
            delivery_id="workflow-1",
            specifications=[normalize_deliverable_spec({
                "filename": "report.md",
                "content": "# 报告",
                "mime_type": "text/markdown",
                "label": "下载报告",
            })],
        )

        self.assertEqual(registered[0]["type"], "sandbox_deliverable")
        self.assertEqual(next(iter(store.entries.values()))["user_id"], "user-1")

    async def test_tool_rejects_path_like_filename(self) -> None:
        sandbox = FakeSandbox()
        tool = create_write_deliverable_tool(sandbox)

        with self.assertRaises(ValueError):
            await tool.ainvoke({
                "filename": "../outside.md",
                "content": "不应写入",
                "mime_type": "text/markdown",
                "label": "无效",
            })


if __name__ == "__main__":
    unittest.main()
