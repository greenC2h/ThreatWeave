"""工具结果中的可视化资源提取测试。"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from api.message_utils import content_to_text, extract_visualization


class VisualizationExtractionTests(unittest.TestCase):
    """验证图片和 HTML MCP 资源可以转换为前端展示模型。"""

    def test_extracts_persistent_html_chart_artifact_as_link(self) -> None:
        artifact_id = "a" * 32
        content = json.dumps(
            {
                "type": "chart_artifact",
                "artifact_id": artifact_id,
                "mime_type": "text/html",
                "message": "图表已生成，HTML 图表已暂存。",
            },
            ensure_ascii=False,
        )

        visualization = extract_visualization(content)

        self.assertEqual(visualization["kind"], "link")
        self.assertEqual(visualization["artifact_id"], artifact_id)
        self.assertEqual(visualization["src"], f"/visualizations/{artifact_id}")
        self.assertEqual(
            visualization["download_src"],
            f"/visualizations/{artifact_id}?download=1",
        )
        self.assertEqual(visualization["label"], "打开 HTML 图表")
        self.assertEqual(content_to_text(content), "图表已生成，HTML 图表已暂存。")

    def test_restores_legacy_png_by_its_actual_file_type(self) -> None:
        """旧文本错误标记图片时，存在的 PNG 仍应作为静态资源展示。"""
        artifact_id = "d" * 32
        with patch(
            "api.message_utils.get_visualization_path",
            return_value=Path(f"runtime/visualizations/{artifact_id}.png"),
        ):
            visualization = extract_visualization(f"artifact_id: {artifact_id}")

        self.assertEqual(visualization["kind"], "image")
        self.assertEqual(visualization["mime_type"], "image/png")
        self.assertNotIn("download_src", visualization)

    def test_extracts_artifact_id_from_subagent_result_text(self) -> None:
        artifact_id = "c" * 32

        visualization = extract_visualization(
            f"柱状图已生成。\n\n- **artifact_id**：`{artifact_id}`"
        )

        self.assertEqual(visualization["kind"], "link")
        self.assertEqual(visualization["artifact_id"], artifact_id)
        self.assertEqual(visualization["src"], f"/visualizations/{artifact_id}")
        self.assertEqual(
            visualization["download_src"],
            f"/visualizations/{artifact_id}?download=1",
        )

    def test_does_not_expose_raw_html_as_a_frontend_visualization(self) -> None:
        """图表必须经过本地资源暂存，不能直接嵌入外部 MCP 的 HTML。"""
        self.assertIsNone(extract_visualization("<html><body>chart</body></html>"))

    def test_deliverable_summary_is_not_misclassified_as_a_chart(self) -> None:
        """同步子 Agent 的 Markdown 交付说明只能进入下载模型。"""
        content = (
            "文件名：`extraction_document_1.md`\n"
            "类型：`sandbox_deliverable`（沙箱交付件，text/markdown）\n"
            "artifact_id：`" + "e" * 32 + "`"
        )
        from api.message_utils import extract_sandbox_deliverables

        self.assertIsNone(extract_visualization(content))
        self.assertEqual(extract_sandbox_deliverables(content)[0]["filename"], "extraction_document_1.md")

    def test_extracts_deliverable_from_fenced_workflow_json(self) -> None:
        """历史 task 结果中的 fenced JSON 也必须保留下载交付件。"""
        artifact_id = "f" * 32
        content = """```json
{"deliverables":[{"type":"sandbox_deliverable","artifact_id":"%s","filename":"document-1-formatted.md","mime_type":"text/markdown","label":"文档 1 清洗后原文"}]}
```""" % artifact_id
        from api.message_utils import extract_sandbox_deliverables

        deliverables = extract_sandbox_deliverables(content)

        self.assertEqual(len(deliverables), 1)
        self.assertEqual(deliverables[0]["artifact_id"], artifact_id)
        self.assertEqual(deliverables[0]["filename"], "document-1-formatted.md")

    def test_extracts_deliverables_from_fenced_json_with_trailing_explanation(self) -> None:
        """子 Agent 的附言不能让已登记的下载交付件丢失。"""
        formatted_id = "a" * 32
        extraction_id = "b" * 32
        graph_id = "c" * 32
        content = """```json
{"deliverables":[
  {"type":"sandbox_deliverable","artifact_id":"%s","filename":"document-1-formatted.md","mime_type":"text/markdown","label":"清洗后原文"},
  {"type":"sandbox_deliverable","artifact_id":"%s","filename":"document-1-extraction.md","mime_type":"text/markdown","label":"提取结果"},
  {"type":"sandbox_deliverable","artifact_id":"%s","filename":"document-1-graph.html","mime_type":"text/html","label":"关系图"}
]}
```

The requested files have been generated.""" % (formatted_id, extraction_id, graph_id)
        from api.message_utils import extract_sandbox_deliverables

        deliverables = extract_sandbox_deliverables(content)

        self.assertEqual(len(deliverables), 3)
        self.assertEqual(
            [item["filename"] for item in deliverables],
            [
                "document-1-formatted.md",
                "document-1-extraction.md",
                "document-1-graph.html",
            ],
        )


if __name__ == "__main__":
    unittest.main()
