"""提供给 intel_ingestion_orchestrator 的单一批处理工具。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from langchain_core.tools import tool

from intel_ingestor.orchestrator import MAX_DOCUMENTS_PER_BATCH, orchestrate_source


FormatterFactory = Callable[[], Any]
_system_formatter_factory: FormatterFactory | None = None


def configure_system_formatter_factory(formatter_factory: FormatterFactory) -> None:
    """注册系统任务为每个批次创建全新 A 图所需的工厂。"""
    global _system_formatter_factory
    _system_formatter_factory = formatter_factory


@tool
async def orchestrate_intelligence_ingestion(
    source_id: str,
    article_url: str | None = None,
    max_articles: int = MAX_DOCUMENTS_PER_BATCH,
) -> str:
    """按上下文预算分批采集并分配情报文章给 A。

    每批最多三篇且受字符预算限制。每批都会新建无状态的 A 格式化图，因此前一批正文
    不会进入下一批上下文。``max_articles`` 限制本次从来源拉取的文章数，默认三篇。
    ``article_url`` 仅接受属于该已批准来源的一篇文章地址。
    """
    async def run_formatter(batch_instruction: str) -> object:
        if _system_formatter_factory is None:
            raise RuntimeError("intel_ingestor 图工厂尚未初始化")
        formatter = _system_formatter_factory()
        return await formatter.ainvoke({"messages": [{"role": "user", "content": batch_instruction}]})

    try:
        report = await orchestrate_source(
            source_id,
            run_formatter,
            max_articles=max_articles,
            article_url=article_url,
        )
    except ValueError as exc:
        return f"编排失败：{exc}"
    return report.to_summary()
