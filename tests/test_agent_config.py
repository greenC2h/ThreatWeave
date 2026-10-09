"""模型配置的运行保护测试。"""

from __future__ import annotations

import unittest

from agent.config import (
    DEEPSEEK_REQUEST_TIMEOUT_SECONDS,
    MAIN_MODEL,
    SUMMARY_MODEL,
)


class ModelRequestTimeoutTests(unittest.TestCase):
    """验证所有 DeepSeek 客户端使用同一有限请求超时。"""

    def test_models_use_configured_finite_request_timeout(self) -> None:
        """流式会话必须在上游长期无响应时结束，不能无限占用输入锁。"""
        self.assertGreater(DEEPSEEK_REQUEST_TIMEOUT_SECONDS, 0)
        self.assertEqual(MAIN_MODEL.request_timeout, DEEPSEEK_REQUEST_TIMEOUT_SECONDS)
        self.assertEqual(SUMMARY_MODEL.request_timeout, DEEPSEEK_REQUEST_TIMEOUT_SECONDS)
