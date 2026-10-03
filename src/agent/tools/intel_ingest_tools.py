"""intel_ingestor 子 Agent 的确定性来源采集工具。"""

from __future__ import annotations

from langchain_core.tools import tool

from intel_ingestor import collect_source


@tool
async def collect_source_documents(source_id: str, max_articles: int = 20) -> str:
    """采集一个已登记来源的文章草稿，供 A 深度整理后通过 Java MCP 逐篇写入。

    工具校验来源配置，抓取列表和文章页，并完成编码修复、正文容器提取和基础
    Markdown 渲染。返回每篇文章的来源元数据、稳定 ``doc_key`` 与
    ``preliminary_content``；它不会删除广告或无关内容，也不会写数据库。
    """
    try:
        report = await collect_source(source_id, max_articles=max_articles)
    except ValueError as exc:
        return f"采集失败：{exc}"
    return report.to_agent_payload()
