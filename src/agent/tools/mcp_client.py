"""按业务来源加载 MCP 工具并返回固定分组。"""

from __future__ import annotations

from copy import copy
import logging
import os
from typing import Any

from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_core.tools import tool


logger = logging.getLogger(__name__)


MCP_SERVER_CONFIG_COMMON = {
    "bing-search": {
        "url": (
            "https://mcp.api-inference.modelscope.net/"
            f"{os.getenv('MODELSCOPE_BING_SEARCH_MCP_TOKEN', '')}/mcp"
        ),
        "transport": "streamable_http",
    }
}

MCP_SERVER_CONFIG_THREATWEAVE = {
    "threatweave-api": {
        "url": (
            f"http://{os.getenv('MYAGENT_MCP_HOST', '127.0.0.1')}:"
            f"{os.getenv('MYAGENT_MCP_PORT', '18081')}"
            f"{os.getenv('MYAGENT_MCP_PATH', '/mcp')}"
        ),
        "transport": "streamable_http",
    }
}

# 子 Agent 依赖项目定义的工具名，不能直接依赖外部 MCP 的实现命名。
# 当前搜索 MCP 提供 bing_search，因此在工具发现边界统一为 web_search。
COMMON_TOOL_NAME_ALIASES = {"bing_search": "web_search"}


@tool
async def web_search(query: str) -> str:
    """在外部搜索 MCP 暂不可用时返回可诊断的降级结果。"""
    del query
    return "外部搜索服务当前不可用；请仅基于已提供的情报库数据完成任务，并在结果中说明限制。"


def _normalize_common_tool_names(tools: list[Any]) -> list[Any]:
    """将公共 MCP 工具名称适配为项目稳定的 Agent 工具契约。"""
    normalized_tools: list[Any] = []
    normalized_names: set[str] = set()

    for tool in tools:
        original_name = str(getattr(tool, "name", ""))
        normalized_name = COMMON_TOOL_NAME_ALIASES.get(original_name, original_name)

        if normalized_name in normalized_names:
            raise RuntimeError(f"公共 MCP 工具名称冲突: {normalized_name}")
        normalized_names.add(normalized_name)

        if normalized_name == original_name:
            normalized_tools.append(tool)
            continue

        # MCP 返回的 StructuredTool 是 Pydantic 模型；复制后改名会保留 schema 和回调。
        if hasattr(tool, "model_copy"):
            normalized_tools.append(tool.model_copy(update={"name": normalized_name}))
            continue

        # 测试替身和兼容工具不一定继承 Pydantic；复制避免改写调用方持有的对象。
        renamed_tool = copy(tool)
        setattr(renamed_tool, "name", normalized_name)
        normalized_tools.append(renamed_tool)

    return normalized_tools


async def _load_tools(
    server_config: dict[str, dict[str, str]],
    group_name: str,
) -> list[Any]:
    """
    连接一组 MCP Server 并合并其工具。

    Args:
        server_config: 当前工具组对应的 MCP Server 配置。
        group_name: 用于日志和异常信息的工具组名称。

    Returns:
        当前工具组内全部已发现的 MCP 工具。

    Raises:
        RuntimeError: MCP Server 连接或工具加载失败。
    """
    client = MultiServerMCPClient(server_config)
    tools_in_group: list[Any] = []

    for server_name in server_config:
        try:
            # MCP 连接和工具发现均为网络异步操作。
            server_tools = await client.get_tools(server_name=server_name)
        except Exception as exc:
            logger.exception("加载 %s MCP Server 工具失败: %s", group_name, server_name)
            raise RuntimeError(
                f"无法加载 {group_name} MCP Server 的工具: {server_name}"
            ) from exc

        tools_in_group.extend(server_tools)
        logger.info(
            "已从 %s MCP Server %s 加载 %d 个工具",
            group_name,
            server_name,
            len(server_tools),
        )

    return tools_in_group


async def load_common_tools(
    common_server_config: dict[str, dict[str, str]] | None = None,
) -> list[Any]:
    """加载主 Agent 可用的公共网络检索工具，失败时提供受限降级实现。"""
    if common_server_config is None and not os.getenv("MODELSCOPE_BING_SEARCH_MCP_TOKEN"):
        logger.warning("未配置公共搜索 MCP 令牌，使用受限降级工具启动服务")
        return [web_search]
    common_config = (
        MCP_SERVER_CONFIG_COMMON
        if common_server_config is None
        else common_server_config
    )
    try:
        return _normalize_common_tool_names(await _load_tools(common_config, "公共"))
    except RuntimeError:
        logger.warning("公共搜索 MCP 不可用，使用受限降级工具启动服务")
        return [web_search]


async def load_threatweave_tools(tool_names: set[str]) -> tuple[list[Any], list[Any]]:
    """加载公共搜索工具及指定 ThreatWeave Java MCP 工具。"""
    common_tools = await load_common_tools()
    java_tools = await _load_tools(MCP_SERVER_CONFIG_THREATWEAVE, "ThreatWeave")
    selected = [tool for tool in java_tools if str(getattr(tool, "name", "")) in tool_names]
    missing = tool_names - {str(getattr(tool, "name", "")) for tool in selected}
    if missing:
        raise RuntimeError(f"ThreatWeave MCP 缺少工具: {', '.join(sorted(missing))}")
    return common_tools, selected
