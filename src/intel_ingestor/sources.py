"""加载已登记来源配置，并为用户直链提供通用采集配置。"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

import yaml

from intel_ingestor.schema import SourceConfig

logger = logging.getLogger(__name__)

DEFAULT_SOURCES_DIR = Path(__file__).resolve().parent / "sources"
DIRECT_URL_SOURCE_ID = "direct_url"

_SELECTOR_KEYS = ("content_selector", "title_selector", "date_selector", "article_link_attribute")


def _sources_dir() -> Path:
    """返回来源目录；优先使用环境变量指定的测试替身目录。"""
    override = os.getenv("THREATWEAVE_SOURCES_DIR")
    return Path(override) if override else DEFAULT_SOURCES_DIR


def _iter_source_files() -> list[tuple[Path, dict]]:
    """读取来源目录下全部 YAML，返回 (路径, 配置字典)；跳过无法解析的文件。"""
    results: list[tuple[Path, dict]] = []
    for path in sorted(_sources_dir().glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            logger.warning("来源配置无法解析，跳过：%s (%s)", path.name, exc)
            continue
        if isinstance(data, dict):
            results.append((path, data))
    return results


def list_source_ids() -> list[str]:
    """返回来源目录中全部已登记来源的 source_id（取自配置字段，非文件名）。"""
    return sorted({data.get("source_id") for _, data in _iter_source_files() if data.get("source_id")})


def direct_url_source(article_url: str) -> SourceConfig:
    """为未指定来源的文章直链创建通用 HTML 采集配置。"""
    if not re.match(r"^https?://", article_url, re.IGNORECASE):
        raise ValueError("文章 URL 必须是 http(s) 地址")
    return SourceConfig(
        source_id=DIRECT_URL_SOURCE_ID,
        display_name="用户直链文章",
        enabled=True,
        entry_url=article_url,
        parser_type="generic_html",
        minimum_interval_seconds=0,
        license="user_supplied_url",
        article_url_pattern=r"^https?://",
    )


def _find_source_path(source_id: str) -> Path | None:
    """按 source_id 定位来源文件：优先 ``<source_id>.yaml``，否则扫描配置文件字段。"""
    direct = _sources_dir() / f"{source_id}.yaml"
    if direct.is_file():
        return direct
    for path, data in _iter_source_files():
        if data.get("source_id") == source_id:
            return path
    return None


def load_source(source_id: str) -> SourceConfig:
    """读取并校验一个已登记来源；未登记、已停用或配置非法时直接失败。"""
    source_path = _find_source_path(source_id)
    if source_path is None:
        raise ValueError(f"未登记的来源: {source_id}；只允许采集已批准来源清单中的条目")
    data = yaml.safe_load(source_path.read_text(encoding="utf-8")) or {}

    if not isinstance(data, dict):
        raise ValueError(f"来源 {source_id} 配置格式错误：应为 YAML 映射")

    html = data.get("html") or {}
    if not isinstance(html, dict):
        raise ValueError(f"来源 {source_id} 的 html 配置格式错误：应为映射")

    required = ("entry_url", "parser_type", "article_url_pattern")
    missing = [key for key in required if not (data.get(key) or "").strip()]
    if missing:
        raise ValueError(f"来源 {source_id} 缺少必填字段: {', '.join(missing)}")

    entry_url = str(data["entry_url"]).strip()
    if not re.match(r"^https?://", entry_url, re.IGNORECASE):
        raise ValueError(f"来源 {source_id} 的 entry_url 必须是 http(s) URL")

    listing_api_url = data.get("listing_api_url")
    if listing_api_url and not re.match(
        r"^https?://", str(listing_api_url).strip(), re.IGNORECASE
    ):
        raise ValueError(f"来源 {source_id} 的 listing_api_url 必须是 http(s) URL")

    try:
        article_pattern = re.compile(str(data["article_url_pattern"]))
    except re.error as exc:
        raise ValueError(f"来源 {source_id} 的 article_url_pattern 不是合法正则: {exc}") from exc
    del article_pattern  # 校验后由采集端按调用点重新编译

    env_requirements = data.get("required_environment_variables") or []
    missing_env = [name for name in env_requirements if not os.getenv(name)]
    if missing_env:
        raise ValueError(f"来源 {source_id} 缺少必需环境变量: {', '.join(missing_env)}")

    return SourceConfig(
        source_id=str(data.get("source_id") or source_id),
        display_name=str(data.get("display_name") or source_id),
        enabled=bool(data.get("enabled", True)),
        entry_url=entry_url,
        parser_type=str(data["parser_type"]),
        minimum_interval_seconds=int(data.get("minimum_interval_seconds", 86400)),
        license=str(data.get("license", "")),
        article_url_pattern=str(data["article_url_pattern"]),
        article_link_attribute=str(html.get("article_link_attribute", "href")),
        fetch_url_template=data.get("fetch_url_template"),
        listing_api_url=str(listing_api_url).strip() if listing_api_url else None,
        article_url_template=data.get("article_url_template"),
        external_id_query_parameter=data.get("external_id_query_parameter"),
        charset=html.get("charset"),
        content_selector=html.get("content_selector"),
        title_selector=html.get("title_selector"),
        date_selector=html.get("date_selector"),
        skip_selectors=tuple(str(item) for item in (html.get("skip_selectors") or [])),
        notes=str(data.get("notes", "")),
    )


def require_enabled(source: SourceConfig) -> None:
    """校验来源已启用且为已批准状态；否则抛出可诊断错误。"""
    if not source.enabled:
        raise ValueError(f"来源 {source.source_id} 已停用，禁止采集")
    if not source.license:
        raise ValueError(f"来源 {source.source_id} 未声明许可说明，禁止采集")
