"""intel_ingestor 初步格式化的单元测试（不访问网络）。"""

from __future__ import annotations

import unittest

from intel_ingestor.cleaner import (
    prepare_article_html,
    render_preliminary_markdown,
    decode_html,
    detect_charset_meta,
    extract_element_inner,
)


ARTICLe_HTML = (
    '<html><head><meta charset="utf-8"/><title>国家互联网应急中心</title></head>'
    '<body>'
    '<div class="header"><nav>网站地图 RSS订阅</nav></div>'
    '<div class="con_list1">'
    '<h2 class="artil_tit"><font color=>关于测试风险提示</font></h2>'
    '<div class="artil_art">　时间：2026-06-01</div>'
    '</div>'
    '<div class="artil_content" ID="Content">'
    '<p><p>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; 第一段正文内容，介绍事件背景。</p>'
    '<p>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; 一、风险描述</p>'
    '<p>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; 第二段正文，包含&ldquo;引号&rdquo;与&nbsp;空白。</p>'
    '<ul><li>列表项A</li><li>列表项B</li></ul>'
    '<table><tr><th>字段</th><th>说明</th></tr>'
    '<tr><td>类型</td><td>恶意代码</td></tr></table>'
    '<p>重复段落内容</p><p>重复段落内容</p>'
    '</div>'
    '<div class="footer">关于我们 版权声明 联系方式</div>'
    '</body></html>'
)


class CleanArticleHtmlTest(unittest.TestCase):
    def _prepare(self) -> tuple[str, str | None, str]:
        title, published_at, content = prepare_article_html(
            ARTICLe_HTML.encode("utf-8"),
            charset="utf-8",
            content_selector="div.artil_content",
            title_selector="h2.artil_tit",
            date_selector="div.artil_art",
            skip_selectors=("div.header", "div.footer"),
        )
        return title, published_at, content

    def test_title_and_date(self) -> None:
        title, published_at, _ = self._prepare()
        self.assertEqual(title, "关于测试风险提示")
        self.assertEqual(published_at, "2026-06-01")

    def test_content_preserves_paragraphs(self) -> None:
        *_ , content = self._prepare()
        self.assertIn("第一段正文内容", content)
        self.assertIn("二、风险描述" if "二、风险描述" in content else "一、风险描述", content)
        self.assertIn("第二段正文", content)

    def test_entities_normalized(self) -> None:
        *_, content = self._prepare()
        self.assertIn("“引号”", content)

    def test_list_and_table_preserved(self) -> None:
        *_, content = self._prepare()
        self.assertIn("- 列表项A", content)
        self.assertIn("| 字段 | 说明 |", content)

    def test_outer_nav_and_footer_do_not_leak_from_content_container(self) -> None:
        *_, content = self._prepare()
        self.assertNotIn("网站地图", content)
        self.assertNotIn("关于我们 版权声明", content)

    def test_duplicate_blocks_are_left_for_agent_review(self) -> None:
        *_, content = self._prepare()
        self.assertEqual(content.count("重复段落内容"), 2)


class CleanBodyFragmentTest(unittest.TestCase):
    def test_handles_raw_paragraphs(self) -> None:
        fragment = "<p>甲</p><p>乙</p>"
        result = render_preliminary_markdown(fragment)
        self.assertIn("甲", result)
        self.assertIn("乙", result)

    def test_strips_scripts(self) -> None:
        fragment = "<p>正文</p><script>alert(1)</script><p>收尾</p>"
        result = render_preliminary_markdown(fragment)
        self.assertNotIn("alert", result)


class SelectorHelpersTest(unittest.TestCase):
    def test_extract_element_inner(self) -> None:
        html = '<div class="artil_content">hello <b>world</b></div>'
        self.assertEqual(extract_element_inner(html, "div.artil_content"), "hello <b>world</b>")

    def test_decode_html_meta(self) -> None:
        content = '<meta charset="gbk"/>'.encode("utf-8")
        self.assertEqual(detect_charset_meta(content), "gbk")

    def test_decode_html_utf8(self) -> None:
        text = "中文内容".encode("utf-8")
        self.assertEqual(decode_html(text, "utf-8"), "中文内容")


if __name__ == "__main__":
    unittest.main()
