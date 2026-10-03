"""将已认证 Cookie 会话绑定到用户请求。"""

from __future__ import annotations

from agent.schema import AuthResponse, ChatRequest, ResumeChatRequest


def bind_chat_identity(request: ChatRequest, current_user: AuthResponse) -> ChatRequest:
    """返回使用当前登录身份的聊天请求，忽略客户端提交的身份字段。"""
    return request.model_copy(
        update={
            "user_id": current_user.user_id,
            "username": current_user.username,
            "name": None,
        }
    )


def bind_resume_identity(
    request: ResumeChatRequest,
    current_user: AuthResponse,
) -> ResumeChatRequest:
    """返回使用当前登录身份的恢复请求，忽略客户端提交的身份字段。"""
    return request.model_copy(
        update={
            "user_id": current_user.user_id,
            "username": current_user.username,
        }
    )
