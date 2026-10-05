"""为同步工作流按需创建在调用方沙箱内运行的 A/B 执行图。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend

from agent.config import (
    ASYNC_SUBAGENT_MODEL_RUN_LIMIT,
    ASYNC_SUBAGENT_TOOL_RUN_LIMIT,
    MAIN_MODEL,
    SKILLS_ROOT,
    SUMMARY_MODEL,
)
from agent.backends.sandbox_proxy import SandboxBackendProxy
from agent.backends.skill_sync import SandboxSkillSynchronizer
from agent.middlewares.agent_protection import build_agent_protection_middleware
from agent.middlewares.skills_sync import SandboxSkillsMiddleware
from agent.subagents.loader import load_subagent
from services.deliverables import create_write_deliverable_tool
from agent.tools.mcp_client import load_threatweave_tools


_CONFIG_DIRECTORY = Path(__file__).parent / "configs"


async def run_internal_subagent(
    name: str,
    instruction: str,
    sandbox_backend: SandboxBackendProxy,
) -> object:
    """在调用方沙箱执行一次 A 或 B，并隔离上一批文章的模型上下文。"""
    config = await _load_config(name, sandbox_backend)
    backend = CompositeBackend(default=sandbox_backend, routes={})
    skill_synchronizer = SandboxSkillSynchronizer(
        SKILLS_ROOT,
        Path(__file__).parent.parent / "memory" / "AGENTS.md",
    )
    graph = create_deep_agent(
        model=MAIN_MODEL,
        system_prompt=config["system_prompt"],
        tools=config["tools"],
        skills=config.get("skills", []),
        backend=backend,
        middleware=[
            # 运行前把版本库中的受控技能同步进调用方沙箱，Agent 只从 /skills/ 读取。
            SandboxSkillsMiddleware(
                backend=backend,
                sources=config.get("skills", []),
                synchronizer=skill_synchronizer,
            ),
            *build_agent_protection_middleware(
                backend=backend,
                summary_model=SUMMARY_MODEL,
                model_run_limit=ASYNC_SUBAGENT_MODEL_RUN_LIMIT,
                tool_run_limit=ASYNC_SUBAGENT_TOOL_RUN_LIMIT,
            ),
        ],
        checkpointer=None,
        name=f"internal_{name}",
    )
    return await graph.ainvoke({"messages": [{"role": "user", "content": instruction}]})


async def _load_config(name: str, sandbox_backend: SandboxBackendProxy) -> dict[str, Any]:
    """按名称加载内部 Agent 的最小 MCP 工具集合。"""
    if name == "intel_ingestor":
        _, threat_tools = await load_threatweave_tools({"threat_document_upsert"})
        return load_subagent(
            _CONFIG_DIRECTORY / "intel_ingestor.yaml",
            threat_tools,
            local_tools=[create_write_deliverable_tool(sandbox_backend)],
        )
    if name == "entity_relation_extractor_preview":
        common_tools, threat_tools = await load_threatweave_tools({
            "threat_document_get", "validate_extraction_evidence", "threat_extraction_preview",
        })
        return load_subagent(
            _CONFIG_DIRECTORY / "entity_relation_extractor_preview.yaml",
            [*common_tools, *threat_tools],
            local_tools=[create_write_deliverable_tool(sandbox_backend)],
        )
    if name == "entity_relation_extractor_commit":
        common_tools, threat_tools = await load_threatweave_tools({
            "threat_document_get", "threat_extraction_get", "validate_extraction_evidence", "threat_extraction_write",
        })
        return load_subagent(
            _CONFIG_DIRECTORY / "entity_relation_extractor_commit.yaml",
            [*common_tools, *threat_tools],
            local_tools=[create_write_deliverable_tool(sandbox_backend)],
        )
    if name == "entity_relation_extractor_draft_commit":
        _, threat_tools = await load_threatweave_tools({"commit_extraction_draft"})
        return load_subagent(
            _CONFIG_DIRECTORY / "entity_relation_extractor_draft_commit.yaml",
            threat_tools,
            local_tools=[create_write_deliverable_tool(sandbox_backend)],
        )
    raise ValueError(f"不支持的内部工作流 Agent: {name}")
