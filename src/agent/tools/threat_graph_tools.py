"""根据 ThreatWeave 查询结果构建自包含的 HTML 图谱内容。"""

from __future__ import annotations

import html
import json
import os
from typing import Any

from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_core.tools import tool


@tool
def build_threat_graph_html(
    title: str,
    entities: list[dict[str, Any]],
    relations: list[dict[str, Any]],
) -> str:
    """生成图谱 HTML，调用方必须按需用 write_file 写入自己的沙箱交付件。"""
    payload = json.dumps({"entities": entities, "relations": relations}, ensure_ascii=False).replace("</", "<\\/")
    safe_title = html.escape(title)
    document = f"""<!doctype html><html lang=\"zh-CN\"><meta charset=\"utf-8\"><title>{safe_title}</title>
<style>body{{font:14px Arial;margin:24px;color:#14213d}}#graph{{display:grid;gap:10px}}.edge{{padding:8px;border-left:3px solid #176b87;background:#f4f8fa}}code{{color:#9b2226}}</style>
<h1>{safe_title}</h1><div id=\"graph\"></div><script>const data={payload};const root=document.querySelector('#graph');
for(const edge of data.relations){{const el=document.createElement('div');el.className='edge';el.textContent=`${{edge.src_canonical_value||edge.src_entity_id}} --${{edge.relation_type}}--> ${{edge.dst_canonical_value||edge.dst_entity_id}}`;root.append(el)}}
if(!data.relations.length){{root.textContent=`共 ${{data.entities.length}} 个实体，未查询到匹配关系。`}}</script></html>"""
    return json.dumps({
        "type": "deliverable_content", "mime_type": "text/html",
        "suggested_filename": "threat-graph.html", "content": document,
        "message": "图谱 HTML 已生成。请仅在用户要求 HTML 图时用 write_file 保存，并登记为交付件。",
    }, ensure_ascii=False)


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


@tool
async def generate_network_graph_html(
    title: str,
    nodes: list[str],
    edges: list[dict[str, str]],
) -> str:
    """调用 Charts MCP 生成网络图 HTML；不可用时返回可供本地回退的状态。"""
    chart_url = os.getenv("MODELSCOPE_CHARTS_MCP_URL", "").strip()
    if not chart_url:
        return json.dumps({
            "status": "unavailable",
            "message": "Charts MCP 未配置，请改用 build_threat_graph_html 生成本地 HTML。",
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
            "message": "Charts MCP 当前不可用，请改用 build_threat_graph_html 生成本地 HTML。",
        }, ensure_ascii=False)

    document = _extract_chart_html(result)
    if document is None:
        return json.dumps({
            "status": "unavailable",
            "message": "Charts MCP 未返回 HTML，请改用 build_threat_graph_html。",
        }, ensure_ascii=False)
    return json.dumps({
        "type": "deliverable_content",
        "mime_type": "text/html",
        "suggested_filename": "threat-network.html",
        "title": title,
        "content": document,
        "message": "Charts MCP 网络图 HTML 已生成。请在用户要求 HTML 图时用 write_file 保存，并登记为交付件。",
    }, ensure_ascii=False)
