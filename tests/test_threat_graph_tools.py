"""Charts MCP 通用适配工具的单元测试。"""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from agent.tools.threat_graph_tools import (
    _normalize_g6_v5_html,
    _normalize_network_graph_config,
    create_chart_tools,
)


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

    def test_normalizes_legacy_g6_data_api_in_generated_html(self) -> None:
        """G6 5 页面不能保留已移除的 graph.data 调用，否则会白屏。"""
        html = """<!doctype html>
<script src=\"https://cdn.jsdelivr.net/npm/@antv/g6@5/dist/g6.min.js\"></script>
<div id=\"container\"></div>
<script>
const data = { nodes: [{ id: 'one', label: 'One' }], edges: [] };
const graph = new Graph({ container: 'container', modes: { default: ['drag-canvas'] }, fitView: true });
graph.data(data);
graph.render();
graph.fitView();
</script>"""

        normalized = _normalize_g6_v5_html(html)

        self.assertIn("const g6Data =", normalized)
        self.assertIn("data: g6Data", normalized)
        self.assertIn("labelText: node.label || node.id", normalized)
        self.assertIn("labelText: edge.label || ''", normalized)
        self.assertIn("behaviors:", normalized)
        self.assertIn("autoFit: 'view'", normalized)
        self.assertNotIn("graph.data(data)", normalized)
        self.assertNotIn("graph.fitView()", normalized)

    def test_normalizes_g6_v5_legacy_behavior_mode_without_legacy_data_call(self) -> None:
        """已升级 data API 的页面仍可能保留旧的 behaviors.default 并导致 G6 5 白屏。"""
        html = """<!doctype html>
<script src="https://cdn.jsdelivr.net/npm/@antv/g6@5/dist/g6.min.js"></script>
<script>
const graph = new Graph({
  data: { nodes: [], edges: [] },
  behaviors: { default: ['drag-canvas', 'zoom-canvas', 'drag-node'] },
});
graph.render();
</script>"""

        normalized = _normalize_g6_v5_html(html)

        self.assertIn("behaviors: ['drag-canvas', 'zoom-canvas', 'drag-element']", normalized)
        self.assertNotIn("behaviors: { default:", normalized)

    def test_normalizes_threat_entities_for_network_graph_schema(self) -> None:
        """威胁实体的惯用字段必须映射到 Charts MCP 要求的 name。"""
        config = _normalize_network_graph_config("network_graph", {
            "data": {
                "nodes": [
                    {"id": "entity-1", "canonicalValue": "APT-Q"},
                    {"id": "entity-2", "label": "198.51.100.7"},
                ],
                "edges": [{
                    "source": "entity-1",
                    "target": "entity-2",
                    "relationType": "USES",
                }],
            },
        })

        self.assertEqual(config["data"]["nodes"], [{"name": "APT-Q"}, {"name": "198.51.100.7"}])
        self.assertEqual(
            config["data"]["edges"],
            [{"source": "APT-Q", "target": "198.51.100.7", "name": "USES"}],
        )

    async def test_generate_visualization_normalizes_network_graph_before_mcp_call(self) -> None:
        """生成工具必须把威胁实体字段转换后再交给 Charts MCP。"""
        network_tool = SimpleNamespace(
            name="generate_network_graph",
            description="生成关系图。",
            args_schema=_Schema,
            ainvoke=AsyncMock(return_value=[{"text": "<html><body>graph</body></html>"}]),
        )
        tools = create_chart_tools()
        generate_visualization = next(item for item in tools if item.name == "generate_visualization")

        with patch(
            "agent.tools.threat_graph_tools._discover_chart_tools",
            new=AsyncMock(return_value={"network_graph": network_tool}),
        ):
            result = json.loads(await generate_visualization.ainvoke({
                "chart_type": "network_graph",
                "chart_config": {"data": {
                    "nodes": [{"id": "attacker", "label": "APT-Q"}, {"id": "host", "canonicalValue": "host.example"}],
                    "edges": [{"source": "attacker", "target": "host", "relation_type": "TARGETS"}],
                }},
            }))

        self.assertEqual(result["type"], "deliverable_content")
        network_tool.ainvoke.assert_awaited_once_with({
            "data": {
                "nodes": [{"name": "APT-Q"}, {"name": "host.example"}],
                "edges": [{"source": "APT-Q", "target": "host.example", "name": "TARGETS"}],
            },
            "format": "html",
        })


if __name__ == "__main__":
    unittest.main()
