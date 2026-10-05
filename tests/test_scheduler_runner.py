"""定时调度器直接调用 ThreatPipeline 的测试。"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from scheduler.runner import load_sources, submit_source
from threat_pipeline.schema import ThreatPipelineResult


class SchedulerRunnerTests(unittest.IsolatedAsyncioTestCase):
    """确保调度器不再创建 Agent、工作流或沙箱。"""

    async def test_submit_source_runs_pipeline_as_system_scheduler(self) -> None:
        pipeline = type("Pipeline", (), {"run": AsyncMock(return_value=ThreatPipelineResult(
            run_id="run-1", source_id="cncert_cc",
        ))})()
        with patch("scheduler.runner.ThreatPipeline", return_value=pipeline):
            await submit_source({"source_id": "cncert_cc"})

        request = pipeline.run.await_args.args[0]
        self.assertEqual(request.actor_id, "system-scheduler")
        self.assertEqual(request.source_id, "cncert_cc")
        self.assertEqual(request.max_articles, 3)

    async def test_submit_source_surfaces_pipeline_failures(self) -> None:
        pipeline = type("Pipeline", (), {"run": AsyncMock(return_value=ThreatPipelineResult(
            run_id="run-1", source_id="cncert_cc", failures=["格式化失败"],
        ))})()
        with patch("scheduler.runner.ThreatPipeline", return_value=pipeline):
            with self.assertRaisesRegex(RuntimeError, "格式化失败"):
                await submit_source({"source_id": "cncert_cc"})

    def test_load_sources_includes_all_registered_sources(self) -> None:
        enabled = type("Source", (), {
            "source_id": "enabled", "enabled": True, "minimum_interval_seconds": 30,
        })()
        disabled = type("Source", (), {
            "source_id": "disabled", "enabled": False, "minimum_interval_seconds": 60,
        })()
        with (
            patch("scheduler.runner.list_source_ids", return_value=["enabled", "disabled"]),
            patch("scheduler.runner.load_source", side_effect=[enabled, disabled]),
        ):
            sources = load_sources()

        self.assertEqual([source["source_id"] for source in sources], ["enabled", "disabled"])


if __name__ == "__main__":
    unittest.main()
