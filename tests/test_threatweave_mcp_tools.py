"""ThreatWeave MCP 请求形状兼容测试。"""

from __future__ import annotations

import unittest

from mcp_server.tools.threatweave_tools import (
    _normalize_extraction_records,
    _split_document_content,
    _validate_extraction_candidates,
)


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

    def test_splits_long_document_without_losing_content(self) -> None:
        content = "first paragraph\nsecond paragraph\nthird paragraph"
        chunks = _split_document_content(content, 20)
        self.assertEqual("".join(chunks), content)
        self.assertGreater(len(chunks), 1)

    def test_validates_model_evidence_and_derives_internal_offsets(self) -> None:
        result = _validate_extraction_candidates(
            "The report cites CVE-2026-1234 exactly once.",
            [{
                "entity_type": "cve",
                "canonical_value": "CVE-2026-1234",
                "semantic_role": "research",
                "evidence": "CVE-2026-1234",
            }],
            [],
        )
        evidence = result["entities"][0]["evidence"][0]
        self.assertEqual(evidence["charStart"], 17)
        self.assertEqual(evidence["charEnd"], 30)
        self.assertEqual(result["rejected"], [])

    def test_rejects_ambiguous_evidence(self) -> None:
        result = _validate_extraction_candidates(
            "CVE-2026-1234 and CVE-2026-1234", [{
                "entity_type": "cve",
                "canonical_value": "CVE-2026-1234",
                "evidence": "CVE-2026-1234",
            }], [],
        )
        self.assertEqual(result["entities"], [])
        self.assertEqual(result["rejected"][0]["kind"], "entity")

    def test_accepts_a_validator_returned_evidence_object(self) -> None:
        result = _validate_extraction_candidates(
            "CVE-2026-1234", [{
                "entityType": "cve",
                "canonicalValue": "CVE-2026-1234",
                "evidence": [{"evidenceQuote": "CVE-2026-1234"}],
            }], [],
        )
        self.assertEqual(result["entities"][0]["evidence"][0]["evidenceQuote"], "CVE-2026-1234")
        self.assertEqual(result["rejected"], [])


if __name__ == "__main__":
    unittest.main()
