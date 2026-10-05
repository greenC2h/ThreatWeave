"""将已批准的情报源直接提交给确定性 Threat Pipeline。"""

from __future__ import annotations

import asyncio
import logging
import os
from intel_ingestor.sources import list_source_ids, load_source
from threat_pipeline.pipeline import ThreatPipeline
from threat_pipeline.schema import ThreatPipelineRequest


logger = logging.getLogger(__name__)
POLL_INTERVAL_SECONDS = int(os.getenv("THREATWEAVE_SCHEDULER_POLL_SECONDS", "60"))
RETRY_INTERVAL_SECONDS = int(os.getenv("THREATWEAVE_SCHEDULER_RETRY_SECONDS", "300"))


def load_sources() -> list[dict[str, object]]:
    """加载全部已登记来源，调度循环仅提交显式启用的来源。"""
    return [
        {
            "source_id": source.source_id,
            "enabled": source.enabled,
            "minimum_interval_seconds": source.minimum_interval_seconds,
        }
        for source_id in list_source_ids()
        for source in [load_source(source_id)]
    ]


async def submit_source(
    source: dict[str, object],
) -> None:
    """直接等待来源的完整导入 Pipeline，不创建用户沙箱或异步任务。"""
    request = ThreatPipelineRequest(
        actor_id="system-scheduler",
        source_id=str(source["source_id"]),
        max_articles=3,
    )
    result = await ThreatPipeline().run(request)
    if result.failures:
        raise RuntimeError("; ".join(result.failures))


async def run() -> None:
    """运行低频进程内调度，并在提交失败后按固定间隔重试。"""
    next_run: dict[str, float] = {}
    loop = asyncio.get_running_loop()
    while True:
        now = loop.time()
        for source in load_sources():
            source_id = str(source["source_id"])
            if not bool(source["enabled"]) or now < next_run.get(source_id, 0):
                continue
            try:
                await submit_source(source)
            except Exception:
                logger.exception("提交情报采集任务失败: %s", source_id)
                next_run[source_id] = now + RETRY_INTERVAL_SECONDS
            else:
                next_run[source_id] = now + int(source["minimum_interval_seconds"])
                logger.info("已完成情报导入 Pipeline: %s", source_id)
        await asyncio.sleep(POLL_INTERVAL_SECONDS)


def main() -> None:
    """配置独立调度进程的日志并启动事件循环。"""
    logging.basicConfig(level=os.getenv("THREATWEAVE_SCHEDULER_LOG_LEVEL", "INFO"))
    asyncio.run(run())


if __name__ == "__main__":
    main()
