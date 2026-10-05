"""PageFetcher 对 HTTP 状态和来源反爬页面的判定测试。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

from intel_ingestor.fetcher import PageFetcher


class PageFetcherChallengeTest(IsolatedAsyncioTestCase):
    async def test_http_200_bot_challenge_page_is_blocked(self) -> None:
        url = "https://mp.weixin.qq.com/s?mid=2247585680"
        client = SimpleNamespace(
            get=AsyncMock(
                return_value=SimpleNamespace(
                    status_code=200,
                    url="https://mp.weixin.qq.com/mp/wappoc_appmsgcaptcha?target_url=%2Fs",
                    content="环境异常，完成验证后即可继续访问。去验证".encode("utf-8"),
                )
            )
        )

        result = await PageFetcher(min_interval_seconds=0)._single(client, url)

        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.status_code, 200)
        self.assertIn("机器人校验页", result.error or "")
