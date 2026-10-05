"""向 threat_handle 暴露完整 ThreatPipeline 的唯一导入工具。"""

from __future__ import annotations

from langchain_core.tools import BaseTool, tool

from intel_ingestor.sources import resolve_source_for_article
from threat_pipeline.pipeline import ThreatPipeline
from threat_pipeline.schema import ThreatPipelineRequest


def create_threat_pipeline_tools(actor_id: str) -> list[BaseTool]:
    """创建绑定发起者身份的 Pipeline 工具，模型不能选择内部阶段。"""

    @tool
    async def run_threat_pipeline(
        source_id: str | None = None,
        article_url: str | None = None,
        max_articles: int = 3,
        force_refresh: bool = False,
    ) -> dict[str, object]:
        """完整导入批准来源文章：采集、清洗、入库、抽取并写入图谱。

        提供 article_url 时必须属于批准来源；未提供 source_id 时会自动解析来源。
        正文来源未改变且此前已完成时会跳过；force_refresh 会强制重新处理。
        """
        if source_id and source_id.lower().startswith(("http://", "https://")):
            if article_url and article_url != source_id:
                raise ValueError("source_id 与 article_url 不能同时携带不同 URL")
            article_url, source_id = source_id, None
        if article_url and not source_id:
            source_id = resolve_source_for_article(article_url)
        request = ThreatPipelineRequest(
            actor_id=actor_id,
            source_id=source_id,
            article_url=article_url,
            max_articles=max_articles,
            force_refresh=force_refresh,
        )
        return (await ThreatPipeline().run(request)).model_dump(mode="json")

    return [run_threat_pipeline]
