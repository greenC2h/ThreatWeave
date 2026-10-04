"""A/B 内部运行器的沙箱边界测试。"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from agent.backends.sandbox_proxy import SandboxBackendProxy
from agent.middlewares.skills_sync import SandboxSkillsMiddleware
from agent.subagents.internal_runner import run_internal_subagent


class InternalRunnerSandboxTests(unittest.IsolatedAsyncioTestCase):
    """确保同步工作流不会回退到宿主技能目录或内存文件系统。"""

    async def test_uses_the_caller_sandbox_and_sandbox_skill_middleware(self) -> None:
        sandbox_backend = SandboxBackendProxy(MagicMock())
        graph = MagicMock()
        graph.ainvoke = AsyncMock(return_value={"messages": []})
        config = {
            "system_prompt": "test",
            "tools": [],
            "skills": ["/skills/subagents/intel_ingestor/intel-ingestion/"],
        }

        with (
            patch("agent.subagents.internal_runner._load_config", new=AsyncMock(return_value=config)),
            patch("agent.subagents.internal_runner.create_deep_agent", return_value=graph) as factory,
        ):
            await run_internal_subagent("intel_ingestor", "处理文章", sandbox_backend)

        backend = factory.call_args.kwargs["backend"]
        self.assertIs(backend.default, sandbox_backend)
        self.assertEqual(backend.routes, {})
        self.assertIsInstance(factory.call_args.kwargs["middleware"][0], SandboxSkillsMiddleware)
