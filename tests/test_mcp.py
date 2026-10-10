"""MCP 客户端、ThreatWeave 只读工具和 HTTP 错误边界测试。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call, patch

import httpx
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from agent.tools.mcp_client import load_common_tools, load_threatweave_tools
from mcp_server.http_base import request_threatweave_api
from mcp_server.tools.threatweave_tools import register_threatweave_tools


class McpClientTests(unittest.IsolatedAsyncioTestCase):
    """验证 MCP 客户端按最小权限加载和规范化工具。"""

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


class ThreatWeaveReadMcpTests(unittest.IsolatedAsyncioTestCase):
    """验证 ThreatWeave MCP 服务只暴露只读查询工具。"""

    async def test_only_read_model_tools_are_exposed(self) -> None:
        server = FastMCP(name="read-tools-test")
        register_threatweave_tools(server)
        tools = {tool.name: tool for tool in await server.list_tools()}

        self.assertEqual(set(tools), {"describe_read_model", "execute_read_query"})
        self.assertIn("description", tools["execute_read_query"].parameters["properties"]["sql"])
        self.assertIn("description", tools["execute_read_query"].parameters["properties"]["parameters"])


class ThreatWeaveResponseTests(unittest.IsolatedAsyncioTestCase):
    """验证 ThreatWeave API 错误不会伪装成成功数据或泄露响应正文。"""

    async def test_empty_success_is_distinct_from_business_failure(self) -> None:
        for code in (200, 400):
            with self.subTest(code=code):
                transport = httpx.MockTransport(
                    lambda request: httpx.Response(200, json={"code": code, "data": []})
                )
                async with httpx.AsyncClient(transport=transport, base_url="http://threatweave.test") as client:
                    if code == 200:
                        self.assertEqual(await request_threatweave_api(client, "GET", "/threatweave/graph"), [])
                    else:
                        with self.assertRaises(ToolError):
                            await request_threatweave_api(client, "GET", "/threatweave/graph")

    async def test_invalid_response_does_not_leak_body(self) -> None:
        for status in (200, 503):
            with self.subTest(status=status):
                transport = httpx.MockTransport(
                    lambda request: httpx.Response(status, text="private-debug-secret")
                )
                async with httpx.AsyncClient(transport=transport, base_url="http://threatweave.test") as client:
                    with self.assertRaises(ToolError) as caught:
                        await request_threatweave_api(client, "GET", "/threatweave/graph")
                    self.assertNotIn("private-debug-secret", str(caught.exception))

    async def test_business_error_includes_safe_code_and_message(self) -> None:
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, json={"code": 422, "message": "实体类型不受支持"})
        )
        async with httpx.AsyncClient(transport=transport, base_url="http://threatweave.test") as client:
            with self.assertRaisesRegex(ToolError, "业务码 422.*实体类型不受支持"):
                await request_threatweave_api(client, "POST", "/threatweave/extractions", json={})

    async def test_business_error_does_not_expose_sensitive_message(self) -> None:
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, json={"code": 500, "message": "token=private-debug-secret"})
        )
        async with httpx.AsyncClient(transport=transport, base_url="http://threatweave.test") as client:
            with self.assertRaises(ToolError) as caught:
                await request_threatweave_api(client, "POST", "/threatweave/extractions", json={})
        self.assertIn("业务码 500", str(caught.exception))
        self.assertNotIn("private-debug-secret", str(caught.exception))

    async def test_write_timeout_is_not_retried(self) -> None:
        requests = []

        def respond(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            raise httpx.ReadTimeout("private-debug-secret", request=request)

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(respond), base_url="http://threatweave.test"
        ) as client:
            with self.assertRaisesRegex(ToolError, "结果尚未确认"):
                await request_threatweave_api(client, "POST", "/threatweave/documents", json={})
        self.assertEqual(len(requests), 1)
