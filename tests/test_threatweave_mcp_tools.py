"""ThreatWeave MCP 请求形状兼容测试。"""

from __future__ import annotations

import unittest

from mcp_server.tools.threatweave_tools import _normalize_extraction_records


class ExtractionRecordNormalizationTests(unittest.TestCase):
    """验证 MCP 只适配传输格式，不补造语义字段。"""

    def test_normalizes_wrapped_snake_case_entity_and_evidence(self) -> None:
        records = _normalize_extraction_records(
            {
                "entities": [{
                    "entity_type": "cve",
                    "canonical_value": "CVE-2026-1234",
                    "evidence": {
                        "evidence_quote": "CVE-2026-1234",
                        "char_start": 0,
                        "char_end": 13,
                        "extractor": "entity_relation_extractor",
                    },
                }]
            },
            "entities",
        )

        self.assertEqual(records[0]["entityType"], "cve")
        self.assertEqual(records[0]["canonicalValue"], "CVE-2026-1234")
        self.assertEqual(records[0]["evidence"][0]["evidenceQuote"], "CVE-2026-1234")

    def test_rejects_non_object_collection(self) -> None:
        with self.assertRaises(ValueError):
            _normalize_extraction_records("not-a-record", "entities")


if __name__ == "__main__":
    unittest.main()
