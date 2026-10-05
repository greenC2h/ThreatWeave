"""集中定义可由 Agent Protocol 托管的异步子 Agent。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from deepagents import AsyncSubAgent

from agent.tools.mcp_client import load_threatweave_tools
from agent.tools.threat_graph_tools import generate_network_graph_html


AsyncToolLoader = Callable[[], Awaitable[list[Any]]]


@dataclass(frozen=True)
class AsyncSubagentRegistration:
    """描述一个可独立启动的异步子 Agent 的固定配置。"""

    name: str
    graph_id: str
    description: str
    config_path: Path
    tool_loader: AsyncToolLoader
    launch_instruction: str
    is_public: bool = True


async def _load_threat_analyst_tools() -> list[Any]:
    common_tools, threat_tools = await load_threatweave_tools({"describe_read_model", "execute_read_query"})
    return [*common_tools, *threat_tools, generate_network_graph_html]


ASYNC_SUBAGENTS: dict[str, AsyncSubagentRegistration] = {
    "threat_analyst": AsyncSubagentRegistration(
        name="threat_analyst", graph_id="threat_analyst_async",
        description="异步威胁分析器。只读查询图谱并生成分析交付物。",
        config_path=Path(__file__).parent / "configs" / "threat_analyst.yaml",
        tool_loader=_load_threat_analyst_tools,
        launch_instruction="需要威胁关联、图谱或报告时使用 `start_async_task` 启动 `threat_analyst`。",
    ),
}


def get_async_subagent_registration(name: str) -> AsyncSubagentRegistration:
    """按名称返回异步子 Agent 注册信息，未知名称直接失败。"""
    try:
        return ASYNC_SUBAGENTS[name]
    except KeyError as exc:
        raise ValueError(f"未知异步子 Agent: {name}") from exc


def get_async_subagent_specs(protocol_url: str) -> list[AsyncSubAgent]:
    """返回主 Agent 注册远程任务工具所需的最小子 Agent 描述。"""
    return [
        {
            "name": registration.name,
            "description": registration.description,
            "graph_id": registration.graph_id,
            "url": protocol_url,
        }
        for registration in ASYNC_SUBAGENTS.values()
        if registration.is_public
    ]


def get_async_subagent_instructions() -> str:
    """生成主 Agent 需要遵守的异步任务委派与用户可见性规则。"""
    instructions = "\n".join(
        f"- {registration.launch_instruction}"
        for registration in ASYNC_SUBAGENTS.values()
        if registration.is_public
    )
    return f"""
## 异步业务任务

{instructions}
""".strip()
