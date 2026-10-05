"""intel_ingestor 确定性采集的跨模块数据模型。

此处只定义跨模块传递的纯数据对象，不包含任何 IO 或解析逻辑，便于在测试中
构造最小样例并隔离验证。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SourceConfig:
    """一个已登记、可采集的公开来源的静态配置（对应 sources/*.yaml）。

    字段约定：
    - ``article_url_pattern``：用于判断一个 URL 是否为文章详情页的正则（对
      ``urljoin(entry_url, href)`` 解析后的绝对 URL 匹配）。
    - ``article_link_attribute``：列表页中真实文章 URL 所在的 HTML 属性名。
      部分站点把 URL 放在 ``href``；CNCERT 站点把 URL 放在 ``onclick``（形如
      ``window.open("/publish/main/11/..._.html")``），因此需要显式配置。
    - ``content_selector`` / ``title_selector`` / ``date_selector``：定位正文、
      标题与发布时间的简易选择器（形如 ``div.artil_content`` / ``h2.artil_tit``）。
    - ``skip_selectors``：正文区域中明确不是正文的结构容器选择器。广告、推荐和
      其他业务无关内容仍由 Pipeline 的格式化模型判断。
    - ``fetch_url_template``：详情页由前端单页应用渲染时，按文章 URL 查询参数
      生成实际公开详情接口地址。
    - ``external_id_query_parameter``：外部文章标识位于查询参数时使用，避免同一路径
      的多篇文章共享同一个 doc_key。
    """

    source_id: str
    display_name: str
    enabled: bool
    entry_url: str
    parser_type: str
    minimum_interval_seconds: int
    license: str
    article_url_pattern: str
    article_link_attribute: str = "href"
    fetch_url_template: str | None = None
    external_id_query_parameter: str | None = None
    charset: str | None = None
    content_selector: str | None = None
    title_selector: str | None = None
    date_selector: str | None = None
    skip_selectors: tuple[str, ...] = ()
    notes: str = ""


@dataclass(frozen=True)
class CollectedDocument:
    """一篇待 Pipeline 深度格式化的来源文章。

    ``preliminary_content`` 只经过确定性的编码修复、正文容器提取和基础 Markdown
    渲染。广告、无关内容、重复段落和段落恢复由格式化模型判断。``doc_key`` 由来源与
    稳定文章标识导出，供 Pipeline 写入最终版本时保持幂等覆盖。
    """

    doc_key: str
    source_id: str
    source_name: str
    external_id: str | None
    title: str
    url: str
    published_at: str | None
    preliminary_content: str


@dataclass(frozen=True)
class ArticleRef:
    """列表页发现的一篇文章的最小引用，具体 URL 在清洗阶段才确定。"""

    url: str
    title: str | None
    published_at: str | None


@dataclass(frozen=True)
class CollectionOutcome:
    """单篇文章采集结果，成功时附带待格式化的文章草稿。"""

    url: str
    status: str  # ok | skipped | failed
    document: CollectedDocument | None = None
    reason: str | None = None


@dataclass(frozen=True)
class CollectionReport:
    """一次来源采集的汇总结果，供 ThreatPipeline 消费。"""

    source_id: str
    outcomes: tuple[CollectionOutcome, ...] = ()
    listing_status: int | None = None
    listing_error: str | None = None

    @property
    def collected_count(self) -> int:
        return sum(1 for o in self.outcomes if o.status == "ok")

    @property
    def failed_count(self) -> int:
        return sum(1 for o in self.outcomes if o.status == "failed")

    @property
    def skipped_count(self) -> int:
        return sum(1 for o in self.outcomes if o.status == "skipped")
