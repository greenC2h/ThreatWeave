"""intel_ingestor 采集编排与来源校验的单元测试（不访问网络或数据库）。"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from intel_ingestor.fetcher import FetchResult
from intel_ingestor.ingestor import (
    collect_article,
    collect_source,
    derive_doc_key,
    derive_external_id,
    discover_article_refs,
)
from intel_ingestor.schema import ArticleRef, SourceConfig


def make_source(**overrides: object) -> SourceConfig:
    """构造最小受控来源配置。"""
    defaults = dict(
        source_id="cncert_cc_threat_warning",
        display_name="CNCERT/CC 威胁预警",
        enabled=True,
        entry_url="https://www.cert.org.cn/publish/main/11/index.html",
        parser_type="cncert_cc_listing_html",
        minimum_interval_seconds=86400,
        license="public_information_subject_to_source_terms",
        article_url_pattern=r"https://www.cert.org.cn/publish/main/11/20\d\d/.*",
        article_link_attribute="onclick",
        charset="utf-8",
        content_selector="div.artil_content",
        title_selector="h2.artil_tit",
        date_selector="div.artil_art",
    )
    defaults.update(overrides)
    return SourceConfig(**defaults)


LISTING_URL = "https://www.cert.org.cn/publish/main/11/index.html"
ARTICLE_A_URL = "https://www.cert.org.cn/publish/main/11/2026/20260601145618109326016/20260601145618109326016_.html"
ARTICLE_B_URL = "https://www.cert.org.cn/publish/main/11/2026/20260323113411436406469/20260323113411436406469_.html"
LISTING_HTML = (
    '<li><span>[2026-06-01]</span>'
    '<a href="javascript:void(0)" onclick=window.open("/publish/main/11/2026/20260601145618109326016/20260601145618109326016_.html")>文章A</a></li>'
    '<li><span>[2026-03-23]</span>'
    '<a href="/publish/main/11/2026/20260323113411436406469/20260323113411436406469_.html">文章B</a></li>'
    '<a href="/publish/main/11/index.html">其他威胁</a>'
    '<a href="/publish/main/11/index_2.html">下一页</a>'
)
ARTICLE_HTML = (
    '<html><head><title>国家互联网应急中心</title></head><body>'
    '<h2 class="artil_tit">关于测试风险提示</h2>'
    '<div class="artil_art">　时间：2026-06-01</div>'
    '<div class="artil_content" ID="Content"><p>&nbsp;&nbsp;正文第一段。</p><p>重复段落。</p><p>重复段落。</p></div>'
    '</body></html>'
)


class DiscoverArticleRefsTest(unittest.TestCase):
    def test_discovers_article_urls_from_onclick_and_href(self) -> None:
        refs = discover_article_refs(LISTING_HTML, make_source())
        self.assertEqual([ref.url for ref in refs], [ARTICLE_A_URL, ARTICLE_B_URL])
        self.assertEqual(refs[0].published_at, "2026-06-01")
        self.assertEqual(refs[0].title, "文章A")

    def test_filters_out_navigation_and_pagination(self) -> None:
        refs = discover_article_refs(LISTING_HTML, make_source())
        self.assertNotIn("https://www.cert.org.cn/publish/main/11/index.html", [ref.url for ref in refs])


class KeyDerivationTest(unittest.TestCase):
    def test_external_id(self) -> None:
        self.assertEqual(derive_external_id(ARTICLE_A_URL), "20260601145618109326016")

    def test_doc_key_is_stable_for_same_source_article(self) -> None:
        key = derive_doc_key("src", "ext1", "https://a/b/c_.html")
        self.assertEqual(key, derive_doc_key("src", "ext1", "https://a/b/c_.html"))
        self.assertNotEqual(key, derive_doc_key("src", "ext2", "https://a/b/c_.html"))


class RoutingFetcher:
    """使用 URL 路由的抓取替身，避免测试意外连接网络。"""

    def __init__(self, pages: dict[str, FetchResult]) -> None:
        self._pages = pages

    async def fetch(self, url: str) -> FetchResult:
        return self._pages[url]


class CollectArticleTest(unittest.IsolatedAsyncioTestCase):
    async def test_collect_article_returns_preliminary_document_without_writing(self) -> None:
        ref = ArticleRef(ARTICLE_A_URL, "关于测试风险提示", "2026-06-01")
        fetcher = RoutingFetcher({
            ARTICLE_A_URL: FetchResult("ok", ARTICLE_A_URL, status_code=200, content=ARTICLE_HTML.encode("utf-8")),
        })

        outcome = await collect_article(ref, make_source(), fetcher)

        self.assertEqual(outcome.status, "ok")
        assert outcome.document is not None
        self.assertEqual(outcome.document.title, "关于测试风险提示")
        self.assertEqual(outcome.document.external_id, "20260601145618109326016")
        self.assertIn("正文第一段", outcome.document.preliminary_content)
        self.assertEqual(outcome.document.preliminary_content.count("重复段落"), 2)

    async def test_collect_article_reports_fetch_failure(self) -> None:
        ref = ArticleRef(ARTICLE_A_URL, None, None)
        fetcher = RoutingFetcher({
            ARTICLE_A_URL: FetchResult("unavailable", ARTICLE_A_URL, status_code=503, error="HTTP 503"),
        })

        outcome = await collect_article(ref, make_source(), fetcher)

        self.assertEqual(outcome.status, "failed")
        self.assertIsNone(outcome.document)


class CollectSourceTest(unittest.IsolatedAsyncioTestCase):
    async def test_collect_source_returns_documents_for_agent_not_database_results(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            yaml_path = Path(tmp) / "cncert_cc_threat_warning.yaml"
            yaml_path.write_text(
                """
source_id: cncert_cc_threat_warning
display_name: CNCERT/CC 威胁预警
enabled: true
entry_url: https://www.cert.org.cn/publish/main/11/index.html
parser_type: cncert_cc_listing_html
minimum_interval_seconds: 86400
license: public_information_subject_to_source_terms
article_url_pattern: https://www.cert.org.cn/publish/main/11/20\\d\\d/.*
html:
  charset: utf-8
  article_link_attribute: onclick
  content_selector: div.artil_content
  title_selector: h2.artil_tit
  date_selector: div.artil_art
""".strip(),
                encoding="utf-8",
            )
            old = os.environ.get("INTEL_INGESTION_SOURCES_DIR")
            os.environ["INTEL_INGESTION_SOURCES_DIR"] = tmp
            try:
                fetcher = RoutingFetcher({
                    LISTING_URL: FetchResult("ok", LISTING_URL, status_code=200, content=LISTING_HTML.encode("utf-8")),
                    ARTICLE_A_URL: FetchResult("ok", ARTICLE_A_URL, status_code=200, content=ARTICLE_HTML.encode("utf-8")),
                    ARTICLE_B_URL: FetchResult("ok", ARTICLE_B_URL, status_code=200, content=ARTICLE_HTML.encode("utf-8")),
                })
                report = await collect_source("cncert_cc_threat_warning", fetcher=fetcher)
            finally:
                if old is None:
                    os.environ.pop("INTEL_INGESTION_SOURCES_DIR", None)
                else:
                    os.environ["INTEL_INGESTION_SOURCES_DIR"] = old

        self.assertEqual(report.collected_count, 2)
        payload = report.to_agent_payload()
        self.assertIn('"documents"', payload)
        self.assertIn('"preliminary_content"', payload)
        self.assertNotIn('"content_sha256"', payload)

    async def test_collect_source_rejects_unknown_source(self) -> None:
        with self.assertRaises(ValueError):
            await collect_source("not_a_registered_source")


if __name__ == "__main__":
    unittest.main()
