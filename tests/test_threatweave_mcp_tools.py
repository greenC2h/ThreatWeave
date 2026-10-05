"""ThreatWeave MCP 请求形状兼容测试。"""

from __future__ import annotations

import unittest

from fastmcp import FastMCP

from mcp_server.tools.threatweave_tools import (
    _normalize_extraction_records,
    _split_document_content,
    _validate_extraction_candidates,
    register_threatweave_tools,
)
from mcp_server.schema import ExtractionRelationCandidate


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

    def test_accepts_flat_relation_contract(self) -> None:
        evidence = "NightHeron 使用 ShadowPipe。"
        result = _validate_extraction_candidates(
            evidence,
            [{
                "entityType": "threat_actor",
                "canonicalValue": "NightHeron",
                "evidence": "NightHeron",
            }, {
                "entityType": "tool",
                "canonicalValue": "ShadowPipe",
                "evidence": "ShadowPipe",
            }],
            [{
                "srcEntityType": "threat_actor",
                "srcCanonicalValue": "NightHeron",
                "dstEntityType": "tool",
                "dstCanonicalValue": "ShadowPipe",
                "relationType": "USES",
                "evidence": evidence,
            }],
        )

        self.assertEqual(result["rejected"], [])
        self.assertEqual(result["relations"][0]["relationType"], "USES")

    def test_rejects_nested_relation_endpoints_with_actionable_contract_error(self) -> None:
        result = _validate_extraction_candidates(
            "NightHeron 使用 ShadowPipe。",
            [],
            [{
                "source": {"entityType": "threat_actor", "canonicalValue": "NightHeron"},
                "target": {"entityType": "tool", "canonicalValue": "ShadowPipe"},
                "relationType": "USES",
                "evidence": "NightHeron 使用 ShadowPipe。",
            }],
        )

        self.assertIn("srcEntityType", result["rejected"][0]["reason"])

    def test_relation_schema_exposes_required_flat_endpoint_fields(self) -> None:
        schema = ExtractionRelationCandidate.model_json_schema(by_alias=True)

        self.assertEqual(
            set(schema["required"]),
            {
                "srcEntityType",
                "srcCanonicalValue",
                "dstEntityType",
                "dstCanonicalValue",
                "relationType",
                "evidence",
            },
        )


class ThreatWeaveToolMetadataTests(unittest.IsolatedAsyncioTestCase):
    """验证 MCP 向模型公开的工具说明与参数契约。"""

    async def test_tools_explain_purpose_and_parameters_without_internal_workflow_details(self) -> None:
        server = FastMCP(name="tool-metadata-test")
        register_threatweave_tools(server)
        tools = {tool.name: tool for tool in await server.list_tools()}

        self.assertEqual(
            set(tools),
            {
                "commit_extraction_draft",
                "threat_document_get",
                "threat_document_upsert",
                "threat_extraction_get",
                "threat_extraction_preview",
                "threat_extraction_write",
                "threat_graph_query",
                "validate_extraction_evidence",
            },
        )
        for tool in tools.values():
            self.assertTrue(tool.description)
            self.assertNotIn("工作流签发", tool.description)
            self.assertNotIn("仅供", tool.description)
            for parameter in tool.parameters["properties"].values():
                self.assertTrue(parameter.get("description"))

        upsert = tools["threat_document_upsert"]
        graph_query = tools["threat_graph_query"]

        self.assertIn("写入格式化情报文档", upsert.description)
        self.assertNotIn("Args:", upsert.description)
        self.assertEqual(
            upsert.parameters["properties"]["content"]["description"],
            "已清洗并整理为 Markdown 的完整文档正文。",
        )
        self.assertEqual(
            graph_query.parameters["properties"]["document_ids"]["description"],
            "可选的文档 ID 列表；提供时仅查询这些文档关联的实体和关系。",
        )


if __name__ == "__main__":
    unittest.main()
