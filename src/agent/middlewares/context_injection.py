"""将运行时用户身份注入主 Agent 的系统提示词。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import SystemMessage


class ContextInjectionMiddleware(AgentMiddleware):
    """将 ``runtime.context`` 中的用户身份写入每次主模型调用的系统提示词。"""

    name = "context_injection"

    @staticmethod
    def _context_notice(context: Any) -> str | None:
        """构造当前调用可见的用户身份说明，缺失身份时跳过注入。"""
        user_id = str(getattr(context, "user_id", "") or "").strip()
        if not user_id:
            return None
        username = str(getattr(context, "username", "") or user_id).strip()
        return (
            "当前运行时用户信息：\n"
            f"- user_id: {user_id}\n"
            f"- username: {username}\n"
            f"- 偏好文件: /memories/{user_id}/preferences.md\n"
            "处理本轮任务前先读取该偏好文件；recent_queries 由系统自动维护。"
        )

    @staticmethod
    def _task_intent_notice(context: Any) -> str | None:
        """把本轮可信分类转换为路由参考，不让它覆盖用户原始请求。"""
        intent = getattr(context, "task_intent", None)
        if intent is None:
            return None

        task_type_labels = {
            "article_import": "导入文章或来源",
            "single_document_query": "查询指定单篇文章",
            "library_analysis": "全库查询或关联分析",
            "current_result_revision": "修改当前会话已有结果",
            "task_management": "查询或管理后台任务",
            "skill_management": "技能管理",
            "general": "通用问答或文件处理",
            "mixed": "包含多个独立任务",
            "unclear": "任务类型尚不明确",
        }
        scope_labels = {
            "single_document": "单篇文章",
            "specified_objects": "用户指定对象集合",
            "entire_library": "全部已入库情报",
            "current_conversation": "当前会话已有结果",
            "unspecified": "未明确范围",
            "not_applicable": "不适用",
        }
        details: list[str] = []
        if intent.task_type:
            details.append(f"- 任务类型：{task_type_labels.get(intent.task_type, intent.task_type)}")
        if intent.scope:
            details.append(f"- 处理范围：{scope_labels.get(intent.scope, intent.scope)}")
        if intent.wants_markdown_report is not None:
            details.append(
                "- Markdown 报告：用户明确要求"
                if intent.wants_markdown_report else "- Markdown 报告：用户未明确要求"
            )
        if intent.wants_html_chart is not None:
            details.append(
                "- HTML 图表：用户明确要求"
                if intent.wants_html_chart else "- HTML 图表：用户未明确要求"
            )
        if not details:
            return None
        return (
            "本轮 Jev 前置分类（仅作路由参考，原始用户请求与项目规则优先）：\n"
            + "\n".join(details)
            + "\n分类未提供的条件不得自行补全；范围不足时按现有规则处理。"
        )

    def _inject_context(self, request: ModelRequest) -> ModelRequest:
        """返回包含运行时身份信息的模型请求，不修改持久化消息历史。"""
        runtime = request.runtime
        notice = self._context_notice(getattr(runtime, "context", None))
        intent_notice = self._task_intent_notice(getattr(runtime, "context", None))
        if notice is None and intent_notice is None:
            return request
        system_message = request.system_message or SystemMessage(content="")
        content = system_message.content
        notices = [item for item in (notice, intent_notice) if item]
        combined_notices = "\n\n".join(notices)
        if isinstance(content, str):
            injected_content = f"{content}\n\n{combined_notices}".strip()
        else:
            injected_content = [
                *content,
                *({"type": "text", "text": item} for item in notices),
            ]
        return request.override(
            system_message=SystemMessage(content=injected_content),
        )

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        """在同步模型调用前注入用户上下文。"""
        return handler(self._inject_context(request))

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        """在异步模型调用前注入用户上下文。"""
        return await handler(self._inject_context(request))
