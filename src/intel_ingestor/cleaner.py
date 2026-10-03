"""确定性的 HTML 正文提取与初步格式化。

职责：把公开来源的文章 HTML 转成可供 Agent 深度整理的 Markdown 草稿，并顺带
提取标题与发布时间。代码只处理机械性的编码修复、正文容器定位和结构渲染；广告、
无关信息、重复内容和混乱段落由 Agent 判断，避免把业务语义硬编码为脆弱规则。

清洗规则（对应 product/CONFIRMED_DECISIONS）：
- 剔除脚本、样式、表单等无法构成正文的 HTML 元素；
- 保留标题层级（h1-h6）、段落、列表、表格与可读文本；
- 统一 HTML 实体和编码异常，保留正文草稿供 Agent 做深度清洗。

实现说明：项目未引入 bs4/lxml，这里基于 stdlib ``html.parser`` 与一个轻量
标签/属性扫描器完成，避免为单一来源增加第三方解析依赖。
"""

from __future__ import annotations

import html as html_lib
import logging
import re
from typing import Any

from html.parser import HTMLParser

logger = logging.getLogger(__name__)

# 被整体剔除的页面装置标签。
_SKIP_TAGS = frozenset(
    {
        "script", "style", "noscript", "iframe", "object", "embed",
        "form", "input", "button", "select", "textarea", "nav", "header", "footer",
    }
)
_BLOCK_TAGS = frozenset({"p", "div", "section", "article", "blockquote", "pre"})
_HEADING_TAGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
_TAG_TOKEN_RE = re.compile(
    r"<!--.*?-->|<\s*(?P<close>/?)\s*(?P<name>[a-zA-Z][a-zA-Z0-9]*)(?P<attrs>[^>]*?)(?P<self>/?)\s*>",
    re.DOTALL,
)


def decode_html(content: bytes, declared_charset: str | None = None) -> str:
    """按声明/常见编码解码，乱码启发式回退到 GBK。

    部分中文站点声明 utf-8 实为 GBK，若按声明解码出现较多替换符（U+FFFD），
    则回退尝试 GBK。
    """
    candidates = []
    if declared_charset:
        candidates.append(declared_charset)
    candidates.extend(["utf-8", "gbk"])
    best: str | None = None
    best_replacement = 10**9
    for enc in dict.fromkeys(candidates):
        try:
            text = content.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
        replacement = text.count("\ufffd")
        if replacement == 0:
            return text
        if replacement < best_replacement:
            best_replacement = replacement
            best = text
    return best or content.decode("utf-8", errors="replace")


def _normalize_inline(text: str) -> str:
    """反转义实体、统一空白；返回折叠后的单行文本。"""
    text = html_lib.unescape(text)
    text = text.replace("\xa0", " ").replace("\u3000", " ")
    text = re.sub(r"[ \t\r\n\f\v]+", " ", text)
    return text.strip()


# ---------------------------------------------------------------------------
# 轻量 CSS-lite 选择器（tag / .class / #id，可组合），仅用于定位容器。
# ---------------------------------------------------------------------------


def _parse_selector(selector: str) -> tuple[str | None, tuple[str, ...], str | None]:
    """把 ``tag.class#id`` 解析为 (tag, classes, id)。"""
    if not selector:
        return None, (), None
    m = re.match(r"^([a-zA-Z][a-zA-Z0-9]*)?((?:[.#][\w\-]+)*)$", selector)
    if not m:
        raise ValueError(f"不支持的简单选择器: {selector}")
    tag = m.group(1)
    classes: list[str] = []
    element_id = None
    for token in re.findall(r"([.#])([\w\-]+)", m.group(2) or ""):
        if token[0] == ".":
            classes.append(token[1])
        else:
            element_id = token[1]
    return tag, tuple(classes), element_id


def _attrs_of(raw_tag: str) -> tuple[set[str], str | None]:
    """从原始开始标签提取 class 集合与 id。"""
    class_match = re.search(r'class\s*=\s*["\']([^"\']+)["\']', raw_tag, re.IGNORECASE)
    id_match = re.search(r'\bid\s*=\s*["\']([^"\']+)["\']', raw_tag, re.IGNORECASE)
    classes = set(class_match.group(1).split()) if class_match else set()
    return classes, id_match.group(1) if id_match else None


def _tag_matches_selector(tag_name: str, raw_tag: str, selector: str) -> bool:
    tag, wanted_classes, wanted_id = _parse_selector(selector)
    name = tag_name.lower()
    if tag and name != tag.lower():
        return False
    classes, element_id = _attrs_of(raw_tag)
    if wanted_id and element_id is None:
        return False
    if wanted_id and element_id and element_id.lower() != wanted_id.lower():
        return False
    return set(wanted_classes).issubset(classes)


def extract_element_inner(html: str, selector: str) -> str | None:
    """返回第一个匹配简单选择器的元素内部 HTML；未找到返回 None。

    通过同标签深度计数定位，可容忍内部标签结构不严谨（如段落嵌套）。
    """
    tag, _, _ = _parse_selector(selector)
    need_tag = tag
    depth = 0
    start: int | None = None
    for match in _TAG_TOKEN_RE.finditer(html):
        if match.group("name") is None:
            continue
        name = match.group("name").lower()
        is_close = bool(match.group("close"))
        is_self = bool(match.group("self"))
        if depth == 0:
            if not is_close and _tag_matches_selector(name, match.group(0), selector):
                depth = 1
                start = match.end()
            continue
        # 已在匹配元素内部：按同名标签计数找闭合。
        if is_close and name == need_tag:
            depth -= 1
            if depth == 0:
                return html[start:match.start()]
        elif not is_close and name == need_tag and not is_self:
            depth += 1
    # 元素未闭合：返回剩余部分。
    return html[start:] if start is not None else None


def first_text(html: str, selector: str) -> str | None:
    """提取第一个匹配选择器元素的内部纯文本（用于标题/日期）。"""
    inner = extract_element_inner(html, selector)
    if inner is None:
        return None
    stripped = re.sub(r"<[^>]+>", " ", inner)
    return _normalize_inline(stripped)


# ---------------------------------------------------------------------------
# Markdown 块收集器
# ---------------------------------------------------------------------------


class _MarkdownCollector(HTMLParser):
    """把正文片段转换成保留结构的 Markdown 块序列。"""

    def __init__(self, skip_selectors: tuple[str, ...] = ()) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_selectors = skip_selectors
        self._skip_depth = 0
        self._tag_stack: list[str] = []
        self._blocks: list[dict[str, Any]] = []
        self._current: dict[str, Any] | None = None
        # 表格状态
        self._in_cell = False
        self._cell_text: list[str] = []
        self._cells: list[str] = []
        self._in_row = False

    def _flush(self) -> None:
        if self._current is not None:
            block = self._current
            if block["kind"] == "table_row":
                # 表格单元格已在关闭 td/th 时规范化，保持列表结构供渲染。
                self._blocks.append(block)
            else:
                raw = block.get("text", []) or ""
                text = "".join(raw) if isinstance(raw, list) else str(raw)
                text = _normalize_inline(text)
                if text:
                    self._blocks.append({**block, "text": text})
            self._current = None

    def _should_skip_element(self, tag: str, attrs: list[tuple[str, str]]) -> bool:
        if tag in _SKIP_TAGS:
            return True
        if not self._skip_selectors:
            return False
        raw_class = " ".join(value for key, value in attrs if key == "class")
        raw_id = next((value for key, value in attrs if key == "id"), "")
        raw = f'class="{raw_class}" id="{raw_id}"' if raw_id else f'class="{raw_class}"'
        return any(_tag_matches_selector(tag, raw, sel) for sel in self._skip_selectors)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str]]) -> None:
        self._tag_stack.append(tag)
        if self._should_skip_element(tag, attrs):
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        name = tag.lower()
        if name in _HEADING_TAGS:
            self._flush()
            self._current = {"kind": "heading", "level": _HEADING_TAGS[name], "text": []}
        elif name == "li":
            self._flush()
            self._current = {"kind": "list_item", "level": 0, "text": []}
        elif name == "tr":
            self._flush()
            self._in_row = True
            self._cells = []
        elif name in ("td", "th"):
            self._in_cell = True
            self._cell_text = []
        elif name == "br":
            if self._current is not None:
                self._current["text"].append("\n")
        elif name in _BLOCK_TAGS:
            self._flush()
            self._current = {"kind": "para", "level": 0, "text": []}

    def handle_endtag(self, tag: str) -> None:
        if self._tag_stack and self._tag_stack[-1] == tag:
            self._tag_stack.pop()
        else:
            try:
                self._tag_stack.remove(tag)
            except ValueError:
                pass
        name = tag.lower()
        if name in _SKIP_TAGS or name in ("form", "input", "button", "select", "textarea", "nav", "header", "footer"):
            if self._skip_depth:
                self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if name in _HEADING_TAGS or name in _BLOCK_TAGS:
            self._flush()
        elif name == "li":
            self._flush()
        elif name in ("td", "th"):
            if self._in_cell:
                self._cells.append(_normalize_inline("".join(self._cell_text)))
                self._in_cell = False
        elif name == "tr":
            if self._in_row:
                self._in_row = False
                self._current = {"kind": "table_row", "level": 0, "text": self._cells}
                self._flush()

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_cell:
            self._cell_text.append(data)
        elif self._current is not None:
            self._current["text"].append(data)
        else:
            # 片段根部出现的散落文本，按段落处理。
            self._current = {"kind": "para", "level": 0, "text": []}
            self._current["text"].append(data)


def _blocks_to_markdown(blocks: list[dict[str, Any]]) -> str:
    """把块序列渲染为 Markdown：段落空行分隔、标题加井号、列表加短横、表格加竖线。"""
    lines: list[str] = []
    list_index = 0
    for block in blocks:
        kind = block["kind"]
        text = block["text"]
        if block.get("kind") == "table_row":
            cells = text if isinstance(text, list) else [text]
            line = "| " + " | ".join(c.replace("|", "\\|") for c in cells) + " |"
            lines.append(line)
            continue
        if not text:
            continue
        if kind == "heading":
            lines.append("#" * block["level"] + " " + str(text))
        elif kind == "list_item":
            lines.append(f"- {text}")
        else:
            lines.append(str(text))
    return "\n\n".join(lines)


def render_preliminary_markdown(
    fragment: str,
    *,
    skip_selectors: tuple[str, ...] = (),
) -> str:
    """把正文 HTML 渲染为初步 Markdown，不进行内容去重或语义删减。"""
    collector = _MarkdownCollector(skip_selectors=skip_selectors)
    collector.feed(fragment)
    collector.close()
    markdown = _blocks_to_markdown(collector._blocks)
    markdown = re.sub(r"\n{3,}", "\n\n", markdown).strip()
    return markdown


def detect_charset_meta(content: bytes) -> str | None:
    """从响应字节的 meta 中获取声明的字符集。"""
    match = re.search(rb'charset=["\'\\s]*([A-Za-z0-9_\\-]+)', content[:8192], re.IGNORECASE)
    return match.group(1).decode() if match else None


def prepare_article_html(
    html_bytes: bytes,
    *,
    charset: str | None,
    content_selector: str | None,
    title_selector: str | None,
    date_selector: str | None,
    skip_selectors: tuple[str, ...],
) -> tuple[str, str | None, str | None]:
    """准备文章草稿，返回 ``(title, published_at, preliminary_markdown)``。

    ``skip_selectors`` 只适用于明确配置为非正文的结构容器，不承担广告或业务内容
    判断；后者必须由 Agent 在入库前处理。
    """
    declared = charset or detect_charset_meta(html_bytes)
    html_text = decode_html(html_bytes, declared)

    # 正文区域优先使用 content_selector；缺失时退化为剔除正文区外的整页（较脆）。
    fragment = extract_element_inner(html_text, content_selector) if content_selector else None
    if fragment is None:
        fragment = html_text

    markdown = render_preliminary_markdown(fragment, skip_selectors=skip_selectors)

    title = None
    if title_selector:
        title = first_text(html_text, title_selector)
    if not title:
        # 回退：正文片段中的首个标题块，或页面 <title>。
        heading_match = re.search(r"<(h[1-3])[^>]*>(.*?)</\1>", fragment, re.DOTALL)
        if heading_match:
            title = _normalize_inline(re.sub(r"<[^>]+>", " ", heading_match.group(2)))
        else:
            page_title = re.search(r"<title>(.*?)</title>", html_text, re.DOTALL)
            title = _normalize_inline(page_title.group(1)) if page_title else None
    title = title or "未命名"

    published_at = None
    if date_selector:
        date_text = first_text(html_text, date_selector)
        date_match = re.search(r"(20\d{2}-\d{2}-\d{2})", date_text or "")
        if date_match:
            published_at = date_match.group(1)
    if not published_at:
        date_match = re.search(r"(20\d{2}-\d{2}-\d{2})", html_text)
        if date_match:
            published_at = date_match.group(1)

    return title, published_at, markdown
