"""图表 HTML 交付前的离线可见性回归测试。"""

from __future__ import annotations

import unittest

from agent.tools.threat_graph_tools import _ensure_renderable_chart_html


class ThreatGraphHtmlTests(unittest.TestCase):
    """确保 Charts MCP 的外部依赖不会导致受限预览显示空白。"""

    def test_external_script_chart_becomes_self_contained_svg(self) -> None:
        document = '<html><body><div id="container"></div><script src="https://cdn.test/g6.js"></script></body></html>'

        rendered = _ensure_renderable_chart_html(
            document,
            "文档 1 关系图",
            ["NightHeron", "ShadowPipe"],
            [{"source": "NightHeron", "target": "ShadowPipe"}],
        )

        self.assertIn("<svg", rendered)
        self.assertIn("NightHeron", rendered)
        self.assertIn("ShadowPipe", rendered)
        self.assertNotIn("cdn.test", rendered)


if __name__ == "__main__":
    unittest.main()
