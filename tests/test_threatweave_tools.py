"""ThreatWeave MCP 工具的机械数据处理测试。"""

from __future__ import annotations

import hashlib
import unittest

from mcp_server.tools.threatweave_tools import _content_sha256


class ThreatWeaveToolTests(unittest.TestCase):
    """确保 Agent 不承担应由 MCP 处理的正文哈希计算。"""

    def test_content_hash_uses_final_utf8_content(self) -> None:
        content = "最终格式化正文\n第二段"
        self.assertEqual(_content_sha256(content), hashlib.sha256(content.encode("utf-8")).hexdigest())


if __name__ == "__main__":
    unittest.main()
