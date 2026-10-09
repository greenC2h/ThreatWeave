"""Charts MCP 通用适配工具的单元测试。"""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from agent.tools.threat_graph_tools import create_chart_tools


class _Schema:
    @staticmethod
    def model_json_schema() -> dict:
        return {
            "type": "object",
            "properties": {
                "data": {"type": "array", "items": {"type": "string"}},
                "format": {"type": "string"},
            },
            "required": ["data"],
        }


class ChartsMcpToolTests(unittest.IsolatedAsyncioTestCase):
    """确保图表类型和参数不再绑定到固定关系图。"""

    def setUp(self) -> None:
        self.chart_tool = SimpleNamespace(
            name="generate_bar_chart",
            description="根据数据生成柱状图。",
            args_schema=_Schema,
            ainvoke=AsyncMock(return_value=[{"text": "<html><body><svg></svg></body></html>"}]),
        )

    async def test_get_chart_spec_lists_dynamic_types_and_returns_schema(self) -> None:
        tools = create_chart_tools()
        get_chart_spec = next(item for item in tools if item.name == "get_chart_spec")
        with patch(
            "agent.tools.threat_graph_tools._discover_chart_tools",
            new=AsyncMock(return_value={"bar": self.chart_tool}),
        ):
            catalog = json.loads(await get_chart_spec.ainvoke({"chart_type": ""}))
            specification = json.loads(await get_chart_spec.ainvoke({"chart_type": "bar"}))

        self.assertEqual(catalog["available_types"][0]["chart_type"], "bar")
        self.assertEqual(specification["tool_name"], "generate_bar_chart")
        self.assertEqual(specification["minimum_example"], {"data": ["示例"]})

    async def test_generate_visualization_preserves_deliverable_contract(self) -> None:
        tools = create_chart_tools()
        generate_visualization = next(item for item in tools if item.name == "generate_visualization")
        with patch(
            "agent.tools.threat_graph_tools._discover_chart_tools",
            new=AsyncMock(return_value={"bar": self.chart_tool}),
        ):
            result = json.loads(await generate_visualization.ainvoke({
                "chart_type": "bar",
                "chart_config": {"data": [{"name": "APT", "value": 3}]},
            }))

        self.assertEqual(result["type"], "deliverable_content")
        self.assertEqual(result["mime_type"], "text/html")
        self.assertEqual(result["suggested_filename"], "threatweave-bar.html")
        self.assertIn("<svg", result["content"])
        self.chart_tool.ainvoke.assert_awaited_once_with({
            "data": [{"name": "APT", "value": 3}],
            "format": "html",
        })


if __name__ == "__main__":
    unittest.main()
