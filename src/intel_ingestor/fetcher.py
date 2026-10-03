"""对公开来源页面的健壮抓取：浏览器 UA、超时、重定向、限速、重试与状态判定。

抓取判定约定（供采集端据此决定跳过还是重试）：
- ``status == "ok"``：200 且内容非空。
- ``status == "blocked"``：WAF/反爬（403 或命中机器人校验页），通常不值得重试。
- ``status == "unavailable"``：404/5xx/连接失败，来源暂时不可达。
- ``status == "timeout"``：请求超时。

所有请求统一使用 ``trust_env=False``，即不读取系统 HTTP_PROXY/NO_PROXY，从源头
规避本机损坏代理配置导致的 httpx 构造失败，也不依赖进程启动时的环境修正。
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)

BROWSER_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Accept-Encoding": "gzip, deflate",
}

# 重试请求：连接错误、5xx；不重试被 WAF 拦截的 4xx。
_RETRYABLE_STATUS = frozenset({500, 502, 503, 504})


@dataclass(frozen=True)
class FetchResult:
    """一次抓取的判定结果，``content`` 仅在 ``status == "ok"`` 时非空。"""

    status: str  # ok | blocked | unavailable | timeout
    final_url: str
    status_code: int | None = None
    content: bytes = b""
    error: str | None = None


class PageFetcher:
    """按来源抓取 HTML 页面，串联限速与重试。"""

    def __init__(
        self,
        *,
        timeout: float = 20.0,
        max_retries: int = 3,
        min_interval_seconds: float = 2.0,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._timeout = timeout
        self._max_retries = max_retries
        self._min_interval = min_interval_seconds
        self._headers = headers or BROWSER_HEADERS
        self._last_request_at = 0.0

    async def _throttle(self) -> None:
        """控制同一采集批次的请求间隔，避免短时间连续请求来源站点。"""
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self._min_interval:
            await asyncio.sleep(self._min_interval - elapsed)
        self._last_request_at = time.monotonic()

    async def _single(self, client: httpx.AsyncClient, url: str) -> FetchResult:
        await self._throttle()
        try:
            response = await client.get(url)
        except httpx.TimeoutException as exc:
            return FetchResult("timeout", url, error="请求超时")
        except httpx.RequestError as exc:
            return FetchResult("unavailable", url, error=f"连接失败: {exc}")

        status = response.status_code
        final_url = str(response.url)
        if status == 200 and response.content:
            return FetchResult("ok", final_url, status_code=status, content=response.content)
        if status in (403, 429):  # 反爬/限流，通常重试无益且可能加重拦截
            return FetchResult("blocked", final_url, status_code=status, error=f"HTTP {status}")
        if status >= 500 or status == 404:
            return FetchResult("unavailable", final_url, status_code=status, error=f"HTTP {status}")
        if status in (301, 302, 303, 307, 308):
            # follow_redirects=True 已处理；此处仅兜底记录非 200 的响应。
            return FetchResult("unavailable", final_url, status_code=status, error=f"HTTP {status}")
        return FetchResult("unavailable", final_url, status_code=status, error=f"HTTP {status}")

    async def fetch(self, url: str) -> FetchResult:
        """抓取一个 URL，对可重试失败进行有限次数退避重试。"""
        timeout = httpx.Timeout(self._timeout)
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers=self._headers,
            trust_env=False,
        ) as client:
            for attempt in range(1, self._max_retries + 1):
                result = await self._single(client, url)
                if result.status != "unavailable" or attempt == self._max_retries:
                    return result
                if result.status_code not in _RETRYABLE_STATUS and result.status_code is not None:
                    return result
                await asyncio.sleep(min(2 ** (attempt - 1), 8.0))
        return FetchResult("unavailable", url, error="已达到最大重试次数")
