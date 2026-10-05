"""ThreatPipeline 的确定性采集阶段：列表发现 → 抓取 → 初步格式化。

来源启停校验、列表页文章定位、正文容器提取、基础 Markdown 渲染与稳定 ``doc_key``
生成由代码完成。深度清洗和 Java 写入由 ThreatPipeline 的后续阶段执行。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from urllib.parse import parse_qs, urljoin, urlparse

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


def derive_external_id(url: str, query_parameter: str | None = None) -> str | None:
    """从文章 URL 导出稳定的外部文章标识，支持路径或查询参数来源。"""
    if query_parameter:
        value = parse_qs(urlparse(url).query).get(query_parameter, [None])[0]
        if value:
            return value
    path = urlparse(url).path.rstrip("/")
    if not path:
        return None
    segment = path.rsplit("/", 1)[-1]
    segment = re.sub(r"\.html?$", "", segment, flags=re.IGNORECASE).rstrip("_")
    return segment or None


def _resolve_fetch_url(url: str, source: SourceConfig) -> str:
    """按来源配置把文章页 URL 转换为实际详情接口 URL。"""
    if not source.fetch_url_template:
        return url
    query = {key: values[0] for key, values in parse_qs(urlparse(url).query).items() if values}
    try:
        return source.fetch_url_template.format(**query)
    except KeyError as exc:
        raise ValueError(f"来源 {source.source_id} 的详情接口缺少 URL 参数: {exc.args[0]}") from exc


def _strip_markup(value: object) -> str:
    """把来源 JSON 中的少量 HTML 字段转换成纯文本。"""
    return re.sub(r"<[^>]+>", " ", str(value or "")).strip()


def _prepare_hillstone_json(content: bytes) -> tuple[str, str | None, str]:
    """把 Hillstone 详情接口 JSON 转为保留字段语义的 Markdown 草稿。"""
    payload = json.loads(content.decode("utf-8"))
    result = payload.get("result") if isinstance(payload, dict) else None
    if not isinstance(result, dict):
        raise ValueError("Hillstone 详情接口未返回 result 对象")

    title = str(result.get("name") or "未命名")
    sections = [f"# {title}"]
    field_sections = (
        ("内容摘要", result.get("contentSummary")),
        ("详细内容", result.get("contentDetail")),
        ("受影响系统", result.get("affectedSystem")),
    )
    for heading, value in field_sections:
        if value:
            sections.append(f"## {heading}\n\n{str(value).strip()}")

    tags = result.get("threatTags") or []
    tag_values = [str(item.get("threatTagValue")) for item in tags if isinstance(item, dict) and item.get("threatTagValue")]
    if tag_values:
        sections.append("## 威胁标签\n\n" + "\n".join(f"- {value}" for value in tag_values))

    ioc_fields = (
        ("关联 IP", result.get("associatedIp")),
        ("关联域名", result.get("associatedDomain")),
        ("关联文件", result.get("associatedFile")),
    )
    for heading, value in ioc_fields:
        if value:
            sections.append(f"## {heading}\n\n{str(value).strip()}")

    references = _strip_markup(result.get("references"))
    if references:
        sections.append(f"## 参考链接\n\n{references}")
    advice = str(result.get("protectionAdvice") or "").strip()
    if advice:
        sections.append(f"## 防护建议\n\n{advice}")
    scope = str(result.get("eventScope") or "").strip()
    if scope:
        sections.append(f"## 事件范围\n\n{scope}")

    published_at = None
    publish_time = result.get("publishTime")
    if isinstance(publish_time, (int, float)):
        published_at = datetime.fromtimestamp(publish_time / 1000, tz=timezone.utc).date().isoformat()
    return title, published_at, "\n\n".join(sections)


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
    """抓取一篇文章并生成待 Pipeline 深度格式化的草稿。"""
    try:
        fetch_url = _resolve_fetch_url(ref.url, source)
    except ValueError as exc:
        return CollectionOutcome(ref.url, "failed", reason=f"详情接口地址无效: {exc}")
    fetched = await fetcher.fetch(fetch_url)
    if fetched.status != "ok":
        return CollectionOutcome(ref.url, "failed", reason=f"抓取失败: {fetched.status} ({fetched.error})")

    try:
        if source.parser_type == "hillstone_hot_threat_json":
            title, published_at, preliminary_content = _prepare_hillstone_json(fetched.content)
        else:
            title, published_at, preliminary_content = prepare_article_html(
                fetched.content,
                charset=source.charset,
                content_selector=source.content_selector,
                title_selector=source.title_selector,
                date_selector=source.date_selector,
                skip_selectors=source.skip_selectors,
            )
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        return CollectionOutcome(ref.url, "failed", reason=f"正文解析失败: {exc}")
    # 列表页已提供的标题/日期可靠时优先采用，否则用正文页解析结果。
    title = title or ref.title or "未命名"
    published_at = published_at or ref.published_at

    if not preliminary_content or preliminary_content.strip() == "":
        return CollectionOutcome(ref.url, "skipped", reason="初步格式化后正文为空")

    external_id = derive_external_id(ref.url, source.external_id_query_parameter)
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
    elif source.parser_type == "hillstone_hot_threat_json":
        # 该来源配置的是公开详情页而不是列表页，入口 URL 本身就是待处理文章。
        refs = [ArticleRef(url=source.entry_url, title=None, published_at=None)]
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
