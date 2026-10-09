"""按用户意图控制异步任务状态工具的模型可见性。"""

from __future__ import annotations

import re
from typing import Any, Callable

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse


ASYNC_TASK_STATUS_INTENT_PATTERN = re.compile(
    r"(?:任务|后台(?:分析|任务)?).{0,12}(?:状态|进度|完成|结果)|"
    r"(?:状态|进度|完成|结果).{0,12}(?:任务|后台(?:分析|任务)?)|"
    r"(?:check|status).{0,12}(?:task|任务)|"
    r"(?:task|任务).{0,12}(?:check|status)",
    re.IGNORECASE,
)


class AsyncTaskStatusVisibilityMiddleware(AgentMiddleware):
    """仅在用户明确询问时向模型暴露异步任务状态查询工具。"""

    name = "async_task_status_visibility"

    def __init__(self, tools: list[Any]) -> None:
        """记录需要按当前用户意图隐藏的状态查询工具。"""
        self._tool_names = {str(getattr(tool, "name", "")) for tool in tools}

    @staticmethod
    def _latest_user_content(messages: list[Any]) -> str:
        """读取当前模型请求中最近一条用户消息的可比较文本。"""
        for message in reversed(messages):
            role = getattr(message, "type", None)
            if role is None and isinstance(message, dict):
                role = message.get("role")
            if role not in {"human", "user"}:
                continue
            content = getattr(message, "content", None)
            if content is None and isinstance(message, dict):
                content = message.get("content", "")
            if isinstance(content, list):
                return " ".join(
                    str(item.get("text", "")) if isinstance(item, dict) else str(item)
                    for item in content
                )
            return str(content or "")
        return ""

    def _should_expose_tools(self, messages: list[Any]) -> bool:
        """仅把用户主动提出的任务进度或状态请求视为查询授权。"""
        return bool(ASYNC_TASK_STATUS_INTENT_PATTERN.search(self._latest_user_content(messages)))

    def _visible_tools(self, request: ModelRequest) -> list[Any]:
        """去掉当前请求不应使用的异步状态查询工具。"""
        return [
            tool
            for tool in request.tools
            if str(getattr(tool, "name", "")) not in self._tool_names
        ]

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        """同步路径按用户意图过滤任务状态查询工具。"""
        if self._should_expose_tools(request.messages):
            return handler(request)
        return handler(request.override(tools=self._visible_tools(request)))

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Any],
    ) -> Any:
        """流式路径按用户意图过滤任务状态查询工具。"""
        if self._should_expose_tools(request.messages):
            return await handler(request)
        return await handler(request.override(tools=self._visible_tools(request)))
