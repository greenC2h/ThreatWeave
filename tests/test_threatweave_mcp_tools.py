"""ThreatWeave 只读查询 MCP 的工具发现测试。"""

from __future__ import annotations

import unittest

from fastmcp import FastMCP

from mcp_server.tools.threatweave_tools import register_threatweave_tools


class ThreatWeaveReadMcpTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_read_model_tools_are_exposed(self) -> None:
        server = FastMCP(name="read-tools-test")
        register_threatweave_tools(server)
        tools = {tool.name: tool for tool in await server.list_tools()}

        self.assertEqual(set(tools), {"describe_read_model", "execute_read_query"})
        self.assertIn("description", tools["execute_read_query"].parameters["properties"]["sql"])
        self.assertIn("description", tools["execute_read_query"].parameters["properties"]["parameters"])
