"""Create a self-contained HTML graph artifact from ThreatWeave query results."""

from __future__ import annotations

import html
import json
from typing import Any

from langchain_core.tools import tool

from services.visualization_artifacts import save_visualization


@tool
def generate_threat_graph(title: str, entities: list[dict[str, Any]], relations: list[dict[str, Any]]) -> str:
    """将查询到的实体和关系生成可嵌入会话的静态 HTML 图谱 artifact。"""
    payload = json.dumps({"entities": entities, "relations": relations}, ensure_ascii=False).replace("</", "<\\/")
    safe_title = html.escape(title)
    document = f"""<!doctype html><html lang=\"zh-CN\"><meta charset=\"utf-8\"><title>{safe_title}</title>
<style>body{{font:14px Arial;margin:24px;color:#14213d}}#graph{{display:grid;gap:10px}}.edge{{padding:8px;border-left:3px solid #176b87;background:#f4f8fa}}code{{color:#9b2226}}</style>
<h1>{safe_title}</h1><div id=\"graph\"></div><script>const data={payload};const root=document.querySelector('#graph');
for(const edge of data.relations){{const el=document.createElement('div');el.className='edge';el.textContent=`${{edge.src_canonical_value||edge.src_entity_id}} --${{edge.relation_type}}--> ${{edge.dst_canonical_value||edge.dst_entity_id}}`;root.append(el)}}
if(!data.relations.length){{root.textContent=`共 ${{data.entities.length}} 个实体，未查询到匹配关系。`}}</script></html>"""
    artifact = save_visualization(document.encode("utf-8"), "text/html")
    return json.dumps({
        "type": "chart_artifact", "status": "generated", "artifact_id": artifact["artifact_id"],
        "mime_type": "text/html", "message": "威胁关系图已生成。",
    }, ensure_ascii=False)
