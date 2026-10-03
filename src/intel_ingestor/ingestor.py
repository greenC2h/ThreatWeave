"""intel_ingestor 的确定性采集编排：列表发现 → 抓取 → 初步格式化。

来源启停校验、列表页文章定位、正文容器提取、基础 Markdown 渲染与稳定 ``doc_key``
生成由代码完成。深度清洗和 Java MCP 入库属于 Agent 职责，不在本模块执行。
"""

from __future__ import annotations

import hashlib
import logging
import re
from urllib.parse import urljoin, urlparse

from intel_ingestor.cleaner import decode_html, detect_charset_meta, prepare_article_html
from intel_ingestor.fetcher import PageFetcher
from intel_ingestor.schema import (
    ArticleRef,
    CollectedDocument,
    CollectionOutcome,
    CollectionReport,
    SourceConfig,
)
from intel_ingestor.sources import load_source, require_enabled

logger = logging.getLogger(__name__)

_ANCHOR_RE = re.compile(r"<a(?P<attrs>[^>]*)>(?P<inner>.*?)</a>", re.DOTALL)
_DATE_IN_LI_RE = re.compile(r"\[(\d{4}-\d{2}-\d{2})\]")
_WINDOW_OPEN_RE = re.compile(r'window\.open\(\s*["\']([^"\']+)["\']\s*\)')


def _attr(attrs: str, name: str) -> str | None:
    """从开始标签属性文本中提取指定属性的值，支持引号与无引号两种写法。"""
    match = re.search(
        r"(?:^|\s)" + re.escape(name) + r"\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s>]*))",
        attrs,
        re.IGNORECASE,
    )
    if not match:
        return None
    return match.group(1) or match.group(2) or match.group(3)


def _resolve_article_url(attrs: str, link_attribute: str, base_url: str) -> str | None:
    """按来源配置优先解析文章 URL，并兼容页面中混用的 href 与 onclick。"""

    def resolve(attribute: str) -> str | None:
        value = _attr(attrs, attribute)
        if not value:
            return None
        if attribute.lower() == "onclick":
            match = _WINDOW_OPEN_RE.search(value)
            return urljoin(base_url, match.group(1)) if match else None
        if value.strip() in ("", "#", "javascript:void(0)"):
            return None
        return urljoin(base_url, value)

    for attribute in dict.fromkeys((link_attribute, "onclick", "href")):
        url = resolve(attribute)
        if url:
            return url
    return None


def _enclosing_li_date(listing_html: str, pos: int) -> str | None:
    """提取某锚点所在 <li> 中的 [YYYY-MM-DD] 日期。"""
    start = listing_html.rfind("<li", 0, pos)
    end = listing_html.find("</li>", pos)
    if start == -1:
        return None
    chunk = listing_html[start: end if end != -1 else pos + 200]
    match = _DATE_IN_LI_RE.search(chunk)
    return match.group(1) if match else None


def discover_article_refs(listing_html: str | bytes, source: SourceConfig) -> list[ArticleRef]:
    """从来源列表页发现全部文章引用，只保留匹配 article_url_pattern 的文章。"""
    if isinstance(listing_html, (bytes, bytearray)):
        raw = bytes(listing_html)
        listing_html = decode_html(raw, source.charset or detect_charset_meta(raw))
    pattern = re.compile(source.article_url_pattern)
    base_url = source.entry_url
    refs: list[ArticleRef] = []
    seen: set[str] = set()
    for match in _ANCHOR_RE.finditer(listing_html):
        url = _resolve_article_url(match.group("attrs"), source.article_link_attribute, base_url)
        if not url or not pattern.match(url):
            continue
        if url in seen:
            continue
        seen.add(url)
        inner = re.sub(r"<[^>]+>", " ", match.group("inner"))
        title = re.sub(r"\s+", " ", inner).strip()
        refs.append(
            ArticleRef(
                url=url,
                title=title or None,
                published_at=_enclosing_li_date(listing_html, match.start()),
            )
        )
    return refs


def derive_external_id(url: str) -> str | None:
    """从文章 URL 导出稳定的外部文章标识（取末段路径，去扩展名与尾部下划线）。"""
    path = urlparse(url).path.rstrip("/")
    if not path:
        return None
    segment = path.rsplit("/", 1)[-1]
    segment = re.sub(r"\.html?$", "", segment, flags=re.IGNORECASE).rstrip("_")
    return segment or None


def derive_doc_key(source_id: str, external_id: str | None, url: str) -> str:
    """生成与正文无关的稳定 doc_key。

    以 (source_id, external_id) 为种子；无外部标识时退化为 (source_id, 规范 URL)。
    这样同一篇文章的更新会命中同一 doc_key，由 Java upsert 覆盖为最新版本。
    """
    seed = f"{source_id}:{external_id}" if external_id else f"{source_id}:{url}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


async def collect_article(
    ref: ArticleRef,
    source: SourceConfig,
    fetcher: PageFetcher,
) -> CollectionOutcome:
    """抓取一篇文章并生成待 Agent 深度格式化的草稿。"""
    fetched = await fetcher.fetch(ref.url)
    if fetched.status != "ok":
        return CollectionOutcome(ref.url, "failed", reason=f"抓取失败: {fetched.status} ({fetched.error})")

    title, published_at, preliminary_content = prepare_article_html(
        fetched.content,
        charset=source.charset,
        content_selector=source.content_selector,
        title_selector=source.title_selector,
        date_selector=source.date_selector,
        skip_selectors=source.skip_selectors,
    )
    # 列表页已提供的标题/日期可靠时优先采用，否则用正文页解析结果。
    title = title or ref.title or "未命名"
    published_at = published_at or ref.published_at

    if not preliminary_content or preliminary_content.strip() == "":
        return CollectionOutcome(ref.url, "skipped", reason="初步格式化后正文为空")

    external_id = derive_external_id(ref.url)
    doc_key = derive_doc_key(source.source_id, external_id, ref.url)
    document = CollectedDocument(
        doc_key=doc_key,
        source_id=source.source_id,
        source_name=source.display_name,
        external_id=external_id,
        title=title,
        url=ref.url,
        published_at=published_at,
        preliminary_content=preliminary_content.strip(),
    )
    return CollectionOutcome(ref.url, "ok", document=document)


async def collect_source(
    source_id: str,
    *,
    max_articles: int | None = None,
    article_url: str | None = None,
    fetcher: PageFetcher | None = None,
) -> CollectionReport:
    """采集来源文章草稿，支持全量列表页或一篇已批准的文章 URL。"""
    source = load_source(source_id)
    require_enabled(source)

    page_fetcher = fetcher or PageFetcher()
    if article_url:
        if not re.compile(source.article_url_pattern).match(article_url):
            raise ValueError(f"文章 URL 不属于已批准来源 {source.source_id}")
        refs = [ArticleRef(url=article_url, title=None, published_at=None)]
    else:
        listing = await page_fetcher.fetch(source.entry_url)
        if listing.status != "ok":
            return CollectionReport(
                source_id=source_id,
                listing_status=listing.status_code,
                listing_error=f"{listing.status} {listing.error or ''}".strip(),
            )
        refs = discover_article_refs(
            decode_html(listing.content, source.charset or detect_charset_meta(listing.content)),
            source,
        )
    if max_articles is not None:
        refs = refs[:max_articles]
    if not refs:
        return CollectionReport(source_id=source_id)
    logger.info("来源 %s 发现 %d 篇文章", source_id, len(refs))

    outcomes = [await collect_article(ref, source, page_fetcher) for ref in refs]
    return CollectionReport(source_id=source_id, outcomes=tuple(outcomes))
