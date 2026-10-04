"""定期调度器的同步工作流提交测试。"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from agent.backends.sandbox_proxy import SandboxBackendProxy
from intelligence_workflow.schema import IntelligenceWorkflowMode, IntelligenceWorkflowResult
from scheduler.runner import SYSTEM_SANDBOX_OWNER, run, submit_source


class SchedulerRunnerTests(unittest.IsolatedAsyncioTestCase):
    """确保调度器不再创建 Agent Protocol 的异步运行。"""

    async def test_submit_source_waits_for_full_ingest_workflow(self) -> None:
        sandbox_backend = SandboxBackendProxy()
        workflow = type("Workflow", (), {"run": AsyncMock(return_value=IntelligenceWorkflowResult(
            workflow_id="workflow-1", mode=IntelligenceWorkflowMode.INGEST_FULL,
        ))})()
        with patch("scheduler.runner.create_intelligence_workflow", return_value=workflow):
            await submit_source(
                {"source_id": "cncert_cc", "entry_url": "https://example.test"},
                sandbox_backend,
            )

        request = workflow.run.await_args.args[0]
        self.assertEqual(request.mode, IntelligenceWorkflowMode.INGEST_FULL)
        self.assertEqual(request.actor_id, "system-scheduler")
        self.assertEqual(request.source_id, "cncert_cc")
        self.assertEqual(request.max_articles, 3)

    async def test_submit_source_binds_the_system_sandbox_to_the_workflow(self) -> None:
        sandbox_backend = SandboxBackendProxy()
        workflow = type("Workflow", (), {"run": AsyncMock(return_value=IntelligenceWorkflowResult(
            workflow_id="workflow-1", mode=IntelligenceWorkflowMode.INGEST_FULL,
        ))})()
        with patch("scheduler.runner.create_intelligence_workflow", return_value=workflow) as factory:
            await submit_source({"source_id": "cncert_cc", "entry_url": "https://example.test"}, sandbox_backend)

        factory.assert_called_once_with(sandbox_backend)

    async def test_submit_source_raises_when_workflow_has_failures(self) -> None:
        sandbox_backend = SandboxBackendProxy()
        workflow = type("Workflow", (), {"run": AsyncMock(return_value=IntelligenceWorkflowResult(
            workflow_id="workflow-1",
            mode=IntelligenceWorkflowMode.INGEST_FULL,
            failures=["格式化失败：https://example.test/article"],
        ))})()
        with patch("scheduler.runner.create_intelligence_workflow", return_value=workflow):
            with self.assertRaisesRegex(RuntimeError, "格式化失败"):
                await submit_source(
                    {"source_id": "cncert_cc", "entry_url": "https://example.test"},
                    sandbox_backend,
                )

    async def test_run_uses_a_dedicated_system_sandbox_and_closes_resources(self) -> None:
        store = object()
        checkpointer = object()
        sandbox_backend = SandboxBackendProxy()
        manager = type("SandboxManager", (), {
            "initialize": AsyncMock(),
            "get_backend": AsyncMock(return_value=sandbox_backend),
            "close": AsyncMock(),
        })()

        with (
            patch("scheduler.runner.create_async_persistence", new=AsyncMock(return_value=(store, checkpointer))),
            patch("scheduler.runner.SandboxManager", return_value=manager),
            patch("scheduler.runner.submit_source", new=AsyncMock()),
            patch("scheduler.runner.close_async_persistence", new=AsyncMock()) as close_persistence,
            patch("scheduler.runner.load_sources", return_value=[]),
            patch("scheduler.runner.asyncio.sleep", new=AsyncMock(side_effect=asyncio.CancelledError)),
        ):
            with self.assertRaises(asyncio.CancelledError):
                await run()

        manager.get_backend.assert_awaited_once_with(SYSTEM_SANDBOX_OWNER)
        manager.close.assert_awaited_once()
        close_persistence.assert_awaited_once_with(store, checkpointer)


if __name__ == "__main__":
    unittest.main()
