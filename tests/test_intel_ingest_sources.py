"""intel_ingestor 来源配置加载与校验的单元测试（使用临时来源目录）。"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from intel_ingestor.sources import list_source_ids, load_source, require_enabled


class SourcesConfigTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._old = os.environ.get("INTEL_INGESTION_SOURCES_DIR")
        os.environ["INTEL_INGESTION_SOURCES_DIR"] = self._tmp.name
        (Path(self._tmp.name) / "cncert.yaml").write_text(
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
  content_selector: div.artil_content
""".strip(),
            encoding="utf-8",
        )
        (Path(self._tmp.name) / "disabled.yaml").write_text(
            """
source_id: old_source
display_name: 旧来源
enabled: false
entry_url: https://example.com
parser_type: generic
minimum_interval_seconds: 86400
license: some_license
article_url_pattern: https://example.com/.*
""".strip(),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()
        if self._old is None:
            os.environ.pop("INTEL_INGESTION_SOURCES_DIR", None)
        else:
            os.environ["INTEL_INGESTION_SOURCES_DIR"] = self._old

    def test_list_source_ids_uses_field(self) -> None:
        self.assertEqual(set(list_source_ids()), {"cncert_cc_threat_warning", "old_source"})

    def test_load_source_by_field_when_filename_differs(self) -> None:
        source = load_source("cncert_cc_threat_warning")
        self.assertTrue(source.enabled)
        self.assertEqual(source.content_selector, "div.artil_content")

    def test_reject_unregistered(self) -> None:
        with self.assertRaises(ValueError):
            load_source("unknown_source")

    def test_require_enabled_rejects_disabled(self) -> None:
        source = load_source("old_source")
        with self.assertRaises(ValueError):
            require_enabled(source)


if __name__ == "__main__":
    unittest.main()