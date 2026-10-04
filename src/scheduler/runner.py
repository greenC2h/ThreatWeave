"""将已批准的情报源提交给系统管理的异步采集 Agent。"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

import yaml

from agent.backends.sandbox_manager import SandboxManager
from agent.backends.sandbox_proxy import SandboxBackendProxy
from agent.config import close_async_persistence, create_async_persistence
from agent.tools.intelligence_workflow_tools import create_intelligence_workflow
from intelligence_workflow.schema import IntelligenceWorkflowMode, IntelligenceWorkflowRequest


logger = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_REGISTRY = (
    PROJECT_ROOT / "src" / "agent" / "skills" / "subagents" / "intel_ingestor"
    / "intel-ingestion" / "sources" / "cncert_cc.yaml"
)
POLL_INTERVAL_SECONDS = int(os.getenv("THREATWEAVE_SCHEDULER_POLL_SECONDS", "60"))
RETRY_INTERVAL_SECONDS = int(os.getenv("THREATWEAVE_SCHEDULER_RETRY_SECONDS", "300"))
# 系统调度不共享任何用户工作区；该身份仅用于保存调度任务的长期沙箱绑定。
SYSTEM_SANDBOX_OWNER = "system-scheduler"


def load_sources() -> list[dict[str, object]]:
    """加载仓库中的情报源清单，并拒绝缺少调度字段的配置。"""
    with SOURCE_REGISTRY.open(encoding="utf-8") as source_file:
        source = yaml.safe_load(source_file)
    required = {"source_id", "enabled", "entry_url", "minimum_interval_seconds"}
    if not isinstance(source, dict) or not required.issubset(source):
        raise ValueError("情报源配置缺少调度所需字段")
    return [source]


async def submit_source(
    source: dict[str, object],
    sandbox_backend: SandboxBackendProxy,
) -> None:
    """在系统专用沙箱中等待来源的完整入库工作流。"""
    request = IntelligenceWorkflowRequest(
        mode=IntelligenceWorkflowMode.INGEST_FULL,
        actor_id="system-scheduler",
        source_id=str(source["source_id"]),
        max_articles=3,
    )
    result = await create_intelligence_workflow(sandbox_backend).run(request)
    if result.failures:
        raise RuntimeError("; ".join(result.failures))


async def run() -> None:
    """运行低频进程内调度，并在提交失败后按固定间隔重试。"""
    store: Any | None = None
    checkpointer: Any | None = None
    sandbox_manager: SandboxManager | None = None
    try:
        store, checkpointer = await create_async_persistence()
        sandbox_manager = SandboxManager(store)
        await sandbox_manager.initialize()
        sandbox_backend = await sandbox_manager.get_backend(SYSTEM_SANDBOX_OWNER)

        next_run: dict[str, float] = {}
        loop = asyncio.get_running_loop()
        while True:
            now = loop.time()
            for source in load_sources():
                source_id = str(source["source_id"])
                if not bool(source["enabled"]) or now < next_run.get(source_id, 0):
                    continue
                try:
                    await submit_source(source, sandbox_backend)
                except Exception:
                    logger.exception("提交情报采集任务失败: %s", source_id)
                    next_run[source_id] = now + RETRY_INTERVAL_SECONDS
                else:
                    next_run[source_id] = now + int(source["minimum_interval_seconds"])
                    logger.info("已完成情报采集工作流: %s", source_id)
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
    finally:
        if sandbox_manager is not None:
            await sandbox_manager.close()
        if store is not None and checkpointer is not None:
            await close_async_persistence(store, checkpointer)


def main() -> None:
    """配置独立调度进程的日志并启动事件循环。"""
    logging.basicConfig(level=os.getenv("THREATWEAVE_SCHEDULER_LOG_LEVEL", "INFO"))
    asyncio.run(run())


if __name__ == "__main__":
    main()
