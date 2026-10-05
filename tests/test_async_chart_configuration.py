"""异步威胁分析器的只读查询配置测试。"""

from __future__ import annotations

import asyncio
import importlib.util
import inspect
import json
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import yaml
from agent.backends.sandbox_proxy import SandboxBackendProxy
from agent.memory.prompts import system_prompt
from agent.subagents.async_registry import get_async_subagent_instructions
from langchain_core.tools import tool
from langgraph_sdk.runtime import _ExecutionRuntime, _ReadRuntime


def _configured_tool(name: str):
    """创建可被 DeepAgents 编译的最小工具，避免网络依赖。"""

    def test_tool() -> str:
        """测试用工具。"""
        return "ok"

    test_tool.__name__ = name
    return tool(test_tool)


class AsyncSubagentConfigurationTests(unittest.TestCase):
    """确保高级分析任务只使用新的受控查询接口。"""

    def test_main_prompt_routes_article_handling_to_threat_handle(self) -> None:
        instructions = f"{system_prompt}\n{get_async_subagent_instructions()}"

        self.assertIn("`threat_handle`", instructions)
        self.assertIn("`run_threat_pipeline`", instructions)
        self.assertNotIn("intelligence_workflow_orchestrator", instructions)
        self.assertNotIn("format_only", instructions)
        self.assertNotIn("extract_preview", instructions)

    def test_registers_only_the_threat_analyst_async_graph(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        config = json.loads((project_root / "langgraph.json").read_text(encoding="utf-8"))

        self.assertEqual(
            config["graphs"]["threat_analyst_async"],
            "./src/agent/subagents/async_entry.py:threat_analyst_agent",
        )
        self.assertNotIn("intel_ingestion_orchestrator_async", config["graphs"])

    def test_async_graph_requires_shared_sandbox_context(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        module_path = project_root / "src" / "agent" / "subagents" / "async_entry.py"
        spec = importlib.util.spec_from_file_location("test_async_graph", module_path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)

        read_tools = [_configured_tool("describe_read_model"), _configured_tool("execute_read_query")]
        with patch(
            "agent.subagents.async_registry.load_threatweave_tools",
            new=AsyncMock(return_value=([_configured_tool("web_search")], read_tools)),
        ):
            spec.loader.exec_module(module)

        self.assertIn("ServerRuntime", str(inspect.signature(module.threat_analyst_agent)))

        async def verify() -> None:
            with self.assertRaisesRegex(RuntimeError, "sandbox_id"):
                async with module.threat_analyst_agent(
                    _ExecutionRuntime(access_context="threads.create_run", store=None, context={}),
                ):
                    self.fail("Missing execution context must fail")
            with patch.object(module, "build_async_subagent_graph", return_value=MagicMock()), patch.object(
                module.SandboxSync, "connect",
            ) as connect:
                async with module.threat_analyst_agent(_ReadRuntime(access_context="threads.read", store=None)):
                    pass
                connect.assert_not_called()

        asyncio.run(verify())

    def test_threat_analyst_uses_read_tools_and_deliverable_writer(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        config = (
            project_root / "src/agent/subagents/configs/threat_analyst.yaml"
        ).read_text(encoding="utf-8")

        self.assertIn("describe_read_model", config)
        self.assertIn("execute_read_query", config)
        self.assertIn("write_deliverable", config)
        self.assertNotIn("threat_graph_query", config)

    def test_threat_analyst_skill_has_valid_metadata_and_sql_guidance(self) -> None:
        skill_path = (
            Path(__file__).resolve().parents[1]
            / "src/agent/skills/subagents/threat_analyst/threat-analysis/SKILL.md"
        )
        text = skill_path.read_text(encoding="utf-8")
        _, front_matter, _ = text.split("---", maxsplit=2)
        metadata = yaml.safe_load(front_matter)

        self.assertEqual(metadata["name"], "threat-analysis")
        self.assertIn("Markdown", metadata["description"])
        self.assertIn("describe_read_model", text)
        self.assertIn("execute_read_query", text)
        self.assertNotIn("threat_graph_query", text)

    def test_compiled_graph_receives_only_read_tools(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        module_path = project_root / "src" / "agent" / "subagents" / "async_entry.py"
        spec = importlib.util.spec_from_file_location("test_async_tools", module_path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        read_tools = [_configured_tool("describe_read_model"), _configured_tool("execute_read_query")]
        with patch(
            "agent.subagents.async_registry.load_threatweave_tools",
            new=AsyncMock(return_value=([_configured_tool("web_search")], read_tools)),
        ):
            spec.loader.exec_module(module)

        with (
            patch.object(module, "OPEN_SANDBOX_API_KEY", "test-key"),
            patch.object(module.SandboxSync, "connect", return_value=MagicMock()),
            patch.object(module, "CompositeBackend", return_value=MagicMock()),
            patch.object(module, "create_deep_agent", return_value=MagicMock()) as graph_factory,
        ):
            module.build_async_subagent_graph("threat_analyst", SandboxBackendProxy())

        self.assertEqual(
            [tool.name for tool in graph_factory.call_args.kwargs["tools"]],
            [
                "web_search",
                "describe_read_model",
                "execute_read_query",
                "generate_network_graph_html",
                "write_deliverable",
            ],
        )


if __name__ == "__main__":
    unittest.main()
