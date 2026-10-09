"""异步任务状态工具的按需暴露测试。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from agent.middlewares.async_task_visibility import AsyncTaskStatusVisibilityMiddleware


class AsyncTaskStatusVisibilityTests(unittest.IsolatedAsyncioTestCase):
    """避免主 Agent 在普通库查询中自行轮询异步任务。"""

    def setUp(self) -> None:
        self.status_tool = SimpleNamespace(name="check_async_task")
        self.start_tool = SimpleNamespace(name="start_async_task")
        self.middleware = AsyncTaskStatusVisibilityMiddleware([self.status_tool])

    def test_hides_status_tool_for_library_query(self) -> None:
        """全库文章查询只应启动分析，不能自动调用任务状态查询。"""
        request = self._request("库里有哪些文章？")
        captured = []

        self.middleware.wrap_model_call(request, lambda value: captured.append(value) or value)

        self.assertEqual(captured[0].tools, [self.start_tool])

    def test_keeps_status_tool_for_explicit_progress_request(self) -> None:
        """用户明确询问进度时，才允许使用任务状态查询工具。"""
        request = self._request("请查看刚才任务的进度和状态")
        captured = []

        self.middleware.wrap_model_call(request, lambda value: captured.append(value) or value)

        self.assertEqual(captured[0], request)

    async def test_async_path_hides_status_tool_for_library_query(self) -> None:
        """流式主图同样不能向模型暴露自动轮询工具。"""
        request = self._request("列出情报库中的文章")

        async def handler(value: SimpleNamespace) -> SimpleNamespace:
            return value

        result = await self.middleware.awrap_model_call(request, handler)

        self.assertEqual(result.tools, [self.start_tool])

    def _request(self, content: str) -> SimpleNamespace:
        request = SimpleNamespace(
            messages=[{"role": "user", "content": content}],
            tools=[self.start_tool, self.status_tool],
        )
        request.override = lambda **kwargs: SimpleNamespace(
            messages=request.messages,
            tools=kwargs["tools"],
        )
        return request
