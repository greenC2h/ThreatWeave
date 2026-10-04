"""将已批准的情报源提交给系统管理的异步采集 Agent。"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

import yaml
from langgraph_sdk import get_client


logger = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_REGISTRY = (
    PROJECT_ROOT / "src" / "agent" / "skills" / "subagents" / "intel_ingestor"
    / "intel-ingestion" / "sources" / "cncert_cc.yaml"
)
POLL_INTERVAL_SECONDS = int(os.getenv("THREATWEAVE_SCHEDULER_POLL_SECONDS", "60"))
RETRY_INTERVAL_SECONDS = int(os.getenv("THREATWEAVE_SCHEDULER_RETRY_SECONDS", "300"))
ASYNC_AGENT_URL = os.getenv("MYAGENT_ASYNC_AGENT_PROTOCOL_URL", "http://127.0.0.1:18082")


def load_sources() -> list[dict[str, object]]:
    """加载仓库中的情报源清单，并拒绝缺少调度字段的配置。"""
    with SOURCE_REGISTRY.open(encoding="utf-8") as source_file:
        source = yaml.safe_load(source_file)
    required = {"source_id", "enabled", "entry_url", "minimum_interval_seconds"}
    if not isinstance(source, dict) or not required.issubset(source):
        raise ValueError("情报源配置缺少调度所需字段")
    return [source]


async def submit_source(source: dict[str, object]) -> None:
    """为一个已配置的情报源创建系统所有的 Agent Protocol 运行。"""
    client = get_client(url=ASYNC_AGENT_URL)
    thread = await client.threads.create()
    description = (
        f"系统调度采集来源 source_id={source['source_id']}，入口={source['entry_url']}。"
        "单次最多处理 3 篇文章（max_articles=3）；读取对应 Skill 的来源配置，"
        "按其流程格式化并写入这些文章。"
    )
    await client.runs.create(
        thread_id=thread["thread_id"],
        assistant_id="intel_ingestion_orchestrator_system",
        input={"messages": [{"role": "user", "content": description}]},
        context={"actor": "system-scheduler"},
    )


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
                logger.info("已提交情报采集任务: %s", source_id)
        await asyncio.sleep(POLL_INTERVAL_SECONDS)


def main() -> None:
    """配置独立调度进程的日志并启动事件循环。"""
    logging.basicConfig(level=os.getenv("THREATWEAVE_SCHEDULER_LOG_LEVEL", "INFO"))
    asyncio.run(run())


if __name__ == "__main__":
    main()
