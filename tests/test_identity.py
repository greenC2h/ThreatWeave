"""验证业务请求只使用 Cookie 会话中的用户身份。"""

from __future__ import annotations

import unittest

from agent.schema import AuthResponse, ChatRequest, ResumeChatRequest
from api.identity import bind_chat_identity, bind_resume_identity


class IdentityBindingTests(unittest.TestCase):
    """防止客户端通过请求体中的 user_id 越权访问其他用户数据。"""

    def setUp(self) -> None:
        self.current_user = AuthResponse(user_id="u-session", username="session-user")

    def test_chat_identity_ignores_client_user_fields(self) -> None:
        """聊天请求必须覆盖客户端伪造的 user_id、username 和 name。"""
        request = ChatRequest(
            message="test message",
            thread_id="thread-1",
            user_id="u-forged",
            username="forged-user",
            name="forged name",
        )

        bound = bind_chat_identity(request, self.current_user)

        self.assertEqual(bound.user_id, "u-session")
        self.assertEqual(bound.username, "session-user")
        self.assertIsNone(bound.name)

    def test_resume_identity_ignores_client_user_fields(self) -> None:
        """恢复任务同样不得信任客户端提交的身份字段。"""
        request = ResumeChatRequest(
            resume={"decision": "continue"},
            user_id="u-forged",
            username="forged-user",
        )

        bound = bind_resume_identity(request, self.current_user)

        self.assertEqual(bound.user_id, "u-session")
        self.assertEqual(bound.username, "session-user")
