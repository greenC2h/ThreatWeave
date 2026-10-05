"""ThreatWeave MCP 工具加载测试。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call, patch

from agent.tools.mcp_client import load_common_tools, load_threatweave_tools


class McpClientTests(unittest.IsolatedAsyncioTestCase):
    """验证公共搜索与 ThreatWeave 工具按最小权限加载。"""

    async def test_common_loader_normalizes_bing_search_name(self) -> None:
        client = MagicMock()
        client.get_tools = AsyncMock(return_value=[SimpleNamespace(name="bing_search")])

        with patch("agent.tools.mcp_client.MultiServerMCPClient", return_value=client):
            tools = await load_common_tools({"bing-search": {"url": "https://common.example/mcp"}})

        self.assertEqual([tool.name for tool in tools], ["web_search"])
        client.get_tools.assert_awaited_once_with(server_name="bing-search")

    async def test_threatweave_loader_selects_requested_tools(self) -> None:
        common_tools = [SimpleNamespace(name="web_search")]
        java_tools = [
            SimpleNamespace(name="describe_read_model"),
            SimpleNamespace(name="execute_read_query"),
        ]
        client = MagicMock()
        client.get_tools = AsyncMock(return_value=java_tools)

        with (
            patch("agent.tools.mcp_client.load_common_tools", new=AsyncMock(return_value=common_tools)),
            patch("agent.tools.mcp_client.MultiServerMCPClient", return_value=client),
        ):
            loaded_common, selected = await load_threatweave_tools({"execute_read_query"})

        self.assertEqual(loaded_common, common_tools)
        self.assertEqual([tool.name for tool in selected], ["execute_read_query"])
        client.get_tools.assert_has_awaits([call(server_name="threatweave-api")])
