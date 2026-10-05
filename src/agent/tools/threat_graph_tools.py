"""根据 ThreatWeave 查询结果构建自包含的 HTML 图谱内容。"""

from __future__ import annotations

import json
import os
from html import escape
from math import cos, pi, sin
import re
from typing import Any

from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_core.tools import tool


def _extract_chart_html(result: Any) -> str | None:
    """从 Modelscope Charts MCP 的内容块中取出完整 HTML 文档。"""
    if not isinstance(result, list):
        return None
    for block in result:
        if isinstance(block, dict):
            content = block.get("text")
        else:
            content = getattr(block, "text", None)
        if isinstance(content, str) and "<html" in content.lower():
            return content
    return None


def _render_static_network_graph(title: str, nodes: list[str], edges: list[dict[str, str]]) -> str:
    """生成无需 CDN 或脚本的 SVG，保证已返回图表在受限预览页中仍可见。"""
    unique_nodes = list(dict.fromkeys(node for node in nodes if node))
    width, height = 1120, 720
    center_x, center_y = width / 2, height / 2
    radius = min(width, height) * 0.31
    positions = {
        node: (
            center_x + radius * cos((index / max(len(unique_nodes), 1)) * 2 * pi - pi / 2),
            center_y + radius * sin((index / max(len(unique_nodes), 1)) * 2 * pi - pi / 2),
        )
        for index, node in enumerate(unique_nodes)
    }
    lines = []
    for edge in edges:
        source, target = edge.get("source", ""), edge.get("target", "")
        if source in positions and target in positions:
            x1, y1 = positions[source]
            x2, y2 = positions[target]
            lines.append(
                f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" />'
            )
    node_elements = []
    for node, (x, y) in positions.items():
        label = escape(node)
        node_elements.append(
            f'<g><circle cx="{x:.1f}" cy="{y:.1f}" r="25" />'
            f'<text x="{x:.1f}" y="{y + 45:.1f}">{label}</text></g>'
        )
    return f"""<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{escape(title)}</title>
    <style>
      html, body {{ margin: 0; min-height: 100%; background: #f8fafc; color: #172033; font-family: Arial, sans-serif; }}
      h1 {{ margin: 24px auto 8px; width: min(1120px, calc(100% - 48px)); font-size: 24px; }}
      svg {{ display: block; width: min(1120px, calc(100% - 48px)); height: auto; margin: 0 auto 24px; background: #fff; border: 1px solid #d6dde8; }}
      line {{ stroke: #92a2b8; stroke-width: 2; }}
      circle {{ fill: #d8f0ea; stroke: #087f6b; stroke-width: 2; }}
      text {{ fill: #172033; font-size: 14px; text-anchor: middle; }}
    </style>
  </head>
  <body>
    <h1>{escape(title)}</h1>
    <svg viewBox="0 0 {width} {height}" role="img" aria-label="{escape(title)}">
      {''.join(lines)}
      {''.join(node_elements)}
    </svg>
  </body>
</html>"""


def _ensure_renderable_chart_html(
    document: str,
    title: str,
    nodes: list[str],
    edges: list[dict[str, str]],
) -> str:
    """拒绝依赖外部脚本的图表页，防止 CSP 或离线环境只显示空容器。"""
    has_external_script = bool(re.search(r"<script[^>]+\\bsrc=", document, re.IGNORECASE))
    has_static_graph = bool(re.search(r"<(?:svg|canvas)\\b", document, re.IGNORECASE))
    if has_external_script or not has_static_graph:
        return _render_static_network_graph(title, nodes, edges)
    return document


@tool
async def generate_network_graph_html(
    title: str,
    nodes: list[str],
    edges: list[dict[str, str]],
) -> str:
    """调用 Charts MCP 生成网络图 HTML；失败时返回明确状态而不执行本地回退。"""
    chart_url = os.getenv("MODELSCOPE_CHARTS_MCP_URL", "").strip()
    if not chart_url:
        return json.dumps({
            "status": "unavailable",
            "message": "Charts MCP 未配置，无法生成 HTML 图。",
        }, ensure_ascii=False)

    try:
        client = MultiServerMCPClient({
            "charts-mcp": {"url": chart_url, "transport": "streamable_http"},
        })
        chart_tools = await client.get_tools(server_name="charts-mcp")
        chart_tool = next(tool for tool in chart_tools if tool.name == "generate_network_graph")
        result = await chart_tool.ainvoke({
            "data": {
                "nodes": [{"name": node} for node in dict.fromkeys(nodes) if node],
                "edges": [
                    {
                        "source": edge.get("source", ""),
                        "target": edge.get("target", ""),
                        "name": edge.get("name", ""),
                    }
                    for edge in edges
                    if edge.get("source") and edge.get("target")
                ],
            },
            "format": "html",
        })
    except Exception:
        return json.dumps({
            "status": "unavailable",
            "message": "Charts MCP 当前不可用，无法生成 HTML 图。",
        }, ensure_ascii=False)

    document = _extract_chart_html(result)
    if document is None:
        return json.dumps({
            "status": "unavailable",
            "message": "Charts MCP 未返回 HTML，无法生成图。",
        }, ensure_ascii=False)
    document = _ensure_renderable_chart_html(document, title, nodes, edges)
    return json.dumps({
        "type": "deliverable_content",
        "mime_type": "text/html",
        "suggested_filename": "threat-network.html",
        "title": title,
        "content": document,
        "message": "Charts MCP 网络图 HTML 已生成并通过离线可见性校验。请在用户要求 HTML 图时用 write_deliverable 保存。",
    }, ensure_ascii=False)
