"""通用异步子 Agent 注册配置测试。"""

from __future__ import annotations

import json
import importlib.util
import inspect
import unittest
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

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
    """确保 LangGraph 服务从通用异步子 Agent 入口注册图。"""

    def test_async_guidance_hides_internal_task_identifiers(self) -> None:
        """
        防止提示词契约出现不支持的工具和虚假的完成声明。
        """
        instructions = f"{system_prompt}\n{get_async_subagent_instructions()}"
        config_directory = Path(__file__).resolve().parents[1] / "src/agent/subagents/configs"
        configured_prompts = "\n".join(
            path.read_text(encoding="utf-8") for path in config_directory.glob("*.yaml")
        )
        for unavailable in (
            "check_async_task", "update_async_task", "cancel_async_task",
            "list_async_tasks", "list_async_task", "才使用异步任务管理工具",
        ):
            self.assertNotIn(unavailable, instructions + configured_prompts)
        self.assertIn("`start_async_task`", instructions)
        self.assertIn("intel_ingestion_orchestrator", instructions)
        self.assertNotIn("intel_ingestor` 只能", instructions)
        self.assertIn("entity_relation_extractor", instructions)
        self.assertIn("threat_analyst", instructions)

    def test_registers_the_threatweave_async_graphs(self) -> None:
        """Agent Protocol 配置必须只公开已确认的 ThreatWeave 图。"""
        project_root = Path(__file__).resolve().parents[1]
        config = json.loads((project_root / "langgraph.json").read_text(encoding="utf-8"))

        self.assertEqual(
            config["graphs"]["intel_ingestor_async"],
            "./src/agent/subagents/async_entry.py:intel_ingestor_agent",
        )
        self.assertNotIn("procurement_analyst_async", config["graphs"])

    def test_async_graph_requires_a_shared_sandbox_context(self) -> None:
        """远程图必须由启动任务传入的共享 sandbox_id 构造。"""
        project_root = Path(__file__).resolve().parents[1]
        module_path = project_root / "src" / "agent" / "subagents" / "async_entry.py"
        spec = importlib.util.spec_from_file_location("test_async_chart_graph", module_path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)

        threat_tools = [
            _configured_tool("threat_document_upsert"),
            _configured_tool("threat_document_get"),
            _configured_tool("threat_extraction_write"),
            _configured_tool("threat_graph_query"),
        ]
        with (
            patch("agent.subagents.async_registry.load_threatweave_tools", new=AsyncMock(return_value=(
                [_configured_tool("web_search")], threat_tools,
            ))),
        ):
            spec.loader.exec_module(module)

        self.assertIn("ServerRuntime", str(inspect.signature(module.intel_ingestor_agent)))

        async def verify():
            with self.assertRaisesRegex(RuntimeError, "sandbox_id"):
                async with module.intel_ingestor_agent(
                    _ExecutionRuntime(access_context="threads.create_run", store=None, context={}),
                ):
                    self.fail("Missing execution context must fail")
            with patch.object(module, "build_async_subagent_graph", return_value=MagicMock()), patch.object(
                module.SandboxSync, "connect",
            ) as connect:
                for access in ("threads.read", "threads.update", "assistants.read"):
                    async with module.intel_ingestor_agent(_ReadRuntime(access_context=access, store=None)):
                        pass
                connect.assert_not_called()

        asyncio.run(verify())

    def test_async_graph_uses_an_empty_route_map_for_sandbox_default(self) -> None:
        """异步图只用共享沙箱默认后端时仍须传入 CompositeBackend 路由表。"""
        project_root = Path(__file__).resolve().parents[1]
        module_path = project_root / "src" / "agent" / "subagents" / "async_entry.py"
        spec = importlib.util.spec_from_file_location("test_async_chart_routes", module_path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        threat_tools = [
            _configured_tool("threat_document_upsert"), _configured_tool("threat_document_get"),
            _configured_tool("threat_extraction_write"), _configured_tool("threat_graph_query"),
        ]
        with (
            patch("agent.subagents.async_registry.load_threatweave_tools", new=AsyncMock(return_value=(
                [_configured_tool("web_search")], threat_tools,
            ))),
        ):
            spec.loader.exec_module(module)

        with (
            patch.object(module, "OPEN_SANDBOX_API_KEY", "test-key"),
            patch.object(module.SandboxSync, "connect", return_value=MagicMock()),
            patch.object(module, "CompositeBackend", return_value=MagicMock()) as backend_factory,
            patch.object(module, "create_deep_agent", return_value=MagicMock()) as graph_factory,
        ):
            module.build_async_subagent_graph("intel_ingestor", SandboxBackendProxy())

        self.assertEqual(backend_factory.call_args.kwargs["routes"], {})
        self.assertEqual(
            [tool.name for tool in graph_factory.call_args.kwargs["tools"]],
            ["threat_document_upsert", "submit_entity_extraction"],
        )
        self.assertEqual(
            type(graph_factory.call_args.kwargs["middleware"][0]).__name__,
            "SandboxSkillsMiddleware",
        )
        self.assertIsNone(graph_factory.call_args.kwargs["interrupt_on"])

    def test_threat_analyst_loads_its_skill_without_write_tools(self) -> None:
        """威胁分析器加载自身流程技能，但只能读取图谱。"""
        config_path = Path(__file__).resolve().parents[1] / "src/agent/subagents/configs/threat_analyst.yaml"
        config = config_path.read_text(encoding="utf-8")

        self.assertIn("skills:", config)
        self.assertIn("/skills/subagents/threat_analyst/", config)
        self.assertNotIn("threat_extraction_write", config)
        self.assertNotIn("request_additional_info", config)
        self.assertIn("threat-analysis Skill", config)
        self.assertIn("威胁", config)

    def test_execution_and_read_graphs_share_topology_and_close_clients(self) -> None:
        """实际编译的图在读取期间没有资源时也必须保持兼容。"""
        module_path = Path(__file__).resolve().parents[1] / "src/agent/subagents/async_entry.py"
        spec = importlib.util.spec_from_file_location("test_async_chart_lifetime", module_path)
        module = importlib.util.module_from_spec(spec)
        threat_tools = [
            _configured_tool("threat_document_upsert"), _configured_tool("threat_document_get"),
            _configured_tool("threat_extraction_write"), _configured_tool("threat_graph_query"),
        ]
        with (
            patch("agent.subagents.async_registry.load_threatweave_tools", new=AsyncMock(return_value=(
                [_configured_tool("web_search")], threat_tools,
            ))),
        ):
            spec.loader.exec_module(module)

        async def verify():
            sandbox = MagicMock(id="sandbox")
            with patch.object(module, "OPEN_SANDBOX_API_KEY", "test"), patch.object(
                module.SandboxSync, "connect", return_value=sandbox,
            ) as connect:
                async with module.intel_ingestor_agent(
                    _ReadRuntime(access_context="threads.read", store=None),
                ) as readonly:
                    connect.assert_not_called()
                    async with module.intel_ingestor_agent(_ExecutionRuntime(
                        access_context="threads.create_run", store=None, context={"sandbox_id": "sandbox"},
                    )) as execution:
                        self.assertEqual(set(readonly.nodes), set(execution.nodes))
                        self.assertEqual(readonly.get_graph().edges, execution.get_graph().edges)
                        self.assertEqual(readonly.get_input_jsonschema(), execution.get_input_jsonschema())
                        self.assertEqual(readonly.get_output_jsonschema(), execution.get_output_jsonschema())
                        sandbox.close.assert_not_called()
                sandbox.close.assert_called_once()
                sandbox.kill.assert_not_called()
                sandbox.reset_mock()
                with patch.object(module, "build_async_subagent_graph", side_effect=RuntimeError("compile failed")):
                    with self.assertRaisesRegex(RuntimeError, "compile failed"):
                        async with module.intel_ingestor_agent(_ExecutionRuntime(
                            access_context="threads.create_run", store=None, context={"sandbox_id": "sandbox"},
                        )):
                            pass
                sandbox.close.assert_called_once()

        asyncio.run(verify())
