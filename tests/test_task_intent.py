"""验证 Jev 前置任务分类的协议解析与回退语义。"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

import httpx

from services import task_intent


class TaskIntentTests(unittest.IsolatedAsyncioTestCase):
    """覆盖可信分类、低置信度降级和网络失败。"""

    @staticmethod
    def _response_payload() -> dict[str, object]:
        return {
            "model": "jev-1.13.0",
            "answers": {
                "task_type": {
                    "type": "choice",
                    "choice": "library_analysis",
                    "probabilities": {"library_analysis": 0.94, "general": 0.04},
                },
                "scope": {
                    "type": "choice",
                    "choice": "entire_library",
                    "probabilities": {"entire_library": 0.91, "unspecified": 0.06},
                },
                "wants_markdown_report": {"type": "noul", "noul": 0.04},
                "wants_html_chart": {"type": "noul", "noul": 0.96},
            },
        }

    async def test_classifies_trusted_fields_and_limits_history(self) -> None:
        received_request: httpx.Request | None = None

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal received_request
            received_request = request
            return httpx.Response(200, json=self._response_payload())

        with patch.object(task_intent, "JEVMODEL_API_KEY", "test-key"):
            async with httpx.AsyncClient(
                base_url="https://jevmodel.test",
                transport=httpx.MockTransport(handler),
            ) as client:
                intent = await task_intent.classify_task_intent(
                    "请对全库关系生成 HTML 图，不要 Markdown 报告。",
                    [{"role": "user", "content": "之前也在查全库。"}],
                    client=client,
                )

        self.assertIsNotNone(intent)
        assert intent is not None
        self.assertEqual(intent.task_type, "library_analysis")
        self.assertEqual(intent.scope, "entire_library")
        self.assertFalse(intent.wants_markdown_report)
        self.assertTrue(intent.wants_html_chart)
        self.assertIsNotNone(received_request)
        assert received_request is not None
        self.assertEqual(received_request.url.path, "/v1/systemone")
        self.assertIn("Current user request", received_request.content.decode("utf-8"))

    def test_discards_low_confidence_choice_and_ambiguous_noul(self) -> None:
        payload = self._response_payload()
        answers = payload["answers"]
        assert isinstance(answers, dict)
        answers["task_type"] = {
            "type": "choice",
            "choice": "library_analysis",
            "probabilities": {"library_analysis": 0.60, "general": 0.35},
        }
        answers["wants_html_chart"] = {"type": "noul", "noul": 0.50}

        intent = task_intent._parse_task_intent(payload)

        self.assertIsNotNone(intent)
        assert intent is not None
        self.assertIsNone(intent.task_type)
        self.assertIsNone(intent.wants_html_chart)
        self.assertEqual(intent.scope, "entire_library")

    async def test_http_failure_falls_back_without_raising(self) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(502, json={"error": {"message": "upstream unavailable"}})

        with patch.object(task_intent, "JEVMODEL_API_KEY", "test-key"):
            async with httpx.AsyncClient(
                base_url="https://jevmodel.test",
                transport=httpx.MockTransport(handler),
            ) as client:
                intent = await task_intent.classify_task_intent("查询全库情报", [], client=client)

        self.assertIsNone(intent)

    async def test_total_timeout_falls_back_without_delaying_the_agent(self) -> None:
        async def handler(_: httpx.Request) -> httpx.Response:
            await asyncio.sleep(0.05)
            return httpx.Response(200, json=self._response_payload())

        with patch.object(task_intent, "JEVMODEL_API_KEY", "test-key"), patch.object(
            task_intent,
            "JEVMODEL_TIMEOUT_SECONDS",
            0.01,
        ):
            async with httpx.AsyncClient(
                base_url="https://jevmodel.test",
                transport=httpx.MockTransport(handler),
            ) as client:
                intent = await task_intent.classify_task_intent("查询全库情报", [], client=client)

        self.assertIsNone(intent)
