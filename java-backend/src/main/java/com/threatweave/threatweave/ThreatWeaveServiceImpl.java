package com.threatweave.threatweave;

import com.threatweave.common.exception.BusinessException;
import java.util.List;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.Map;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.ResultSetMetaData;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.ConnectionCallback;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

/** 使用 JDBC 实现文档、图谱和出处模型。 */
@Service
public class ThreatWeaveServiceImpl implements ThreatWeaveService {
    private final JdbcTemplate jdbcTemplate;
    private final ThreatWeaveReadQueryPolicy readQueryPolicy;

    public ThreatWeaveServiceImpl(JdbcTemplate jdbcTemplate, ThreatWeaveReadQueryPolicy readQueryPolicy) {
        this.jdbcTemplate = jdbcTemplate;
        this.readQueryPolicy = readQueryPolicy;
    }

    @Override
    @Transactional
    public Map<String, Object> upsertDocument(ThreatWeaveRequests.DocumentUpsertRequest request) {
        List<Map<String, Object>> existingDocuments = jdbcTemplate.queryForList(
                "SELECT id, content_sha256 FROM threatweave.documents WHERE doc_key = ? FOR UPDATE",
                request.docKey());
        boolean hasContentChanged = !existingDocuments.isEmpty()
                && !request.contentSha256().equals(existingDocuments.get(0).get("content_sha256"));
        jdbcTemplate.update("""
            INSERT INTO threatweave.documents (doc_key, source_id, source_name, external_id, title, url, published_at, content, content_sha256, formatted_at, ingested_at)
            VALUES (?, ?, ?, ?, ?, ?, CAST(? AS TIMESTAMPTZ), ?, ?, now(), now())
            ON CONFLICT (doc_key) DO UPDATE SET source_id = EXCLUDED.source_id, source_name = EXCLUDED.source_name, external_id = EXCLUDED.external_id,
              title = EXCLUDED.title, url = EXCLUDED.url, published_at = EXCLUDED.published_at, content = EXCLUDED.content,
              content_sha256 = EXCLUDED.content_sha256, formatted_at = now(), ingested_at = now()
            """, request.docKey(), request.sourceId(), request.sourceName(), request.externalId(), request.title(), request.url(),
            request.publishedAt(), request.content(), request.contentSha256());
        Map<String, Object> document = getDocumentByKey(request.docKey());
        if (hasContentChanged) {
            // 出处位置是格式化正文中的字符偏移。文档覆盖后保留旧出处会使证据错误地指向新正文。
            jdbcTemplate.update("DELETE FROM threatweave.provenance WHERE document_id = ?", document.get("id"));
        }
        return document;
    }

    @Override
    public Map<String, Object> getDocument(long documentId) {
        List<Map<String, Object>> rows = jdbcTemplate.queryForList(
                "SELECT * FROM threatweave.documents WHERE id = ?", documentId);
        if (rows.isEmpty()) {
            throw new BusinessException("文档不存在");
        }
        return rows.get(0);
    }

    @Override
    public Map<String, Object> getDocumentExtraction(long documentId) {
        // 读取前先确认文档存在，避免空结果与不存在的 document_id 混淆。
        getDocument(documentId);
        List<Map<String, Object>> entities = jdbcTemplate.queryForList("""
            SELECT e.id, e.entity_type, e.canonical_value, e.display_name, e.semantic_role, e.confidence,
                   p.evidence_quote, p.char_start, p.char_end, p.extractor AS evidence_extractor,
                   p.confidence AS evidence_confidence
            FROM threatweave.provenance p
            JOIN threatweave.entities e ON e.id = p.entity_id
            WHERE p.document_id = ?
            ORDER BY e.id, p.id
            """, documentId);
        List<Map<String, Object>> relations = jdbcTemplate.queryForList("""
            SELECT r.id, r.relation_type, r.confidence,
                   src.entity_type AS src_entity_type, src.canonical_value AS src_canonical_value,
                   dst.entity_type AS dst_entity_type, dst.canonical_value AS dst_canonical_value,
                   p.evidence_quote, p.char_start, p.char_end, p.extractor AS evidence_extractor,
                   p.confidence AS evidence_confidence
            FROM threatweave.provenance p
            JOIN threatweave.relations r ON r.id = p.relation_id
            JOIN threatweave.entities src ON src.id = r.src_entity_id
            JOIN threatweave.entities dst ON dst.id = r.dst_entity_id
            WHERE p.document_id = ?
            ORDER BY r.id, p.id
            """, documentId);
        return Map.of("document_id", documentId, "entities", entities, "relations", relations);
    }

    @Override
    public Map<String, Object> getDocumentByKey(String docKey) {
        List<Map<String, Object>> rows = jdbcTemplate.queryForList(
                "SELECT * FROM threatweave.documents WHERE doc_key = ?", docKey);
        if (rows.isEmpty()) {
            throw new BusinessException("文档不存在");
        }
        return rows.get(0);
    }

    @Override
    @Transactional
    public Map<String, Object> writeExtraction(ThreatWeaveRequests.ExtractionWriteRequest request) {
        Map<String, Object> document = getDocument(request.documentId());
        String documentContent = (String) document.get("content");
        // 本次抽取代表该文档的完整最新事实；旧出处必须先移除，避免陈旧事实继续可查询。
        jdbcTemplate.update("DELETE FROM threatweave.provenance WHERE document_id = ?", request.documentId());
        int entityCount = 0;
        int relationCount = 0;
        for (ThreatWeaveRequests.EntityInput entity : request.entities()) {
            long entityId = upsertEntity(entity);
            entityCount++;
            if (entity.aliases() != null) {
                for (String alias : entity.aliases()) {
                    if (alias != null && !alias.isBlank()) {
                        jdbcTemplate.update("INSERT INTO threatweave.entity_aliases (entity_id, alias) VALUES (?, ?) ON CONFLICT DO NOTHING", entityId, alias);
                    }
                }
            }
            writeEvidence(request.documentId(), documentContent, entityId, null, entity.evidence());
        }
        if (request.relations() != null) {
            for (ThreatWeaveRequests.RelationInput relation : request.relations()) {
                long sourceId = findEntityId(relation.srcEntityType(), relation.srcCanonicalValue());
                long targetId = findEntityId(relation.dstEntityType(), relation.dstCanonicalValue());
                if (sourceId == targetId) {
                    throw new BusinessException("关系两端不能是同一实体");
                }
                jdbcTemplate.update("""
                    INSERT INTO threatweave.relations (src_entity_id, dst_entity_id, relation_type, confidence, first_seen_at, last_seen_at, updated_at)
                    VALUES (?, ?, ?, ?, CAST(? AS TIMESTAMPTZ), CAST(? AS TIMESTAMPTZ), now())
                    ON CONFLICT (src_entity_id, dst_entity_id, relation_type) DO UPDATE SET confidence = EXCLUDED.confidence,
                      first_seen_at = COALESCE(LEAST(threatweave.relations.first_seen_at, EXCLUDED.first_seen_at), threatweave.relations.first_seen_at, EXCLUDED.first_seen_at),
                      last_seen_at = COALESCE(GREATEST(threatweave.relations.last_seen_at, EXCLUDED.last_seen_at), threatweave.relations.last_seen_at, EXCLUDED.last_seen_at), updated_at = now()
                    """, sourceId, targetId, relation.relationType(), relation.confidence(), relation.firstSeenAt(), relation.lastSeenAt());
                long relationId = jdbcTemplate.queryForObject("SELECT id FROM threatweave.relations WHERE src_entity_id = ? AND dst_entity_id = ? AND relation_type = ?", Long.class, sourceId, targetId, relation.relationType());
                writeEvidence(request.documentId(), documentContent, null, relationId, relation.evidence());
                relationCount++;
            }
        }
        removeOrphanedGraphData();
        return Map.of("documentId", request.documentId(), "entitiesProcessed", entityCount, "relationsProcessed", relationCount);
    }

    @Override
    public Map<String, Object> queryGraph(String query, List<Long> documentIds, int limit) {
        String term = query == null ? "" : query.trim();
        List<Long> scopedDocumentIds = documentIds == null ? List.of() : documentIds.stream()
            .filter(documentId -> documentId != null && documentId > 0)
            .distinct()
            .toList();
        String documentFilter = scopedDocumentIds.isEmpty()
            ? ""
            : " AND EXISTS (SELECT 1 FROM threatweave.provenance p "
                + "WHERE p.%s = %s.id AND p.document_id IN ("
                + String.join(", ", java.util.Collections.nCopies(scopedDocumentIds.size(), "?")) + "))";

        String entitySql = """
            SELECT e.* FROM threatweave.entities e WHERE (? = '' OR e.canonical_value ILIKE ? OR e.display_name ILIKE ?)
            """ + String.format(documentFilter, "entity_id", "e") + " ORDER BY e.updated_at DESC LIMIT ?";
        List<Object> entityArguments = new java.util.ArrayList<>(List.of(term, "%" + term + "%", "%" + term + "%"));
        entityArguments.addAll(scopedDocumentIds);
        entityArguments.add(limit);
        List<Map<String, Object>> entities = jdbcTemplate.queryForList(entitySql, entityArguments.toArray());

        String relationSql = """
            SELECT r.*, src.entity_type AS src_entity_type, src.canonical_value AS src_canonical_value,
                   dst.entity_type AS dst_entity_type, dst.canonical_value AS dst_canonical_value
            FROM threatweave.relations r JOIN threatweave.entities src ON src.id = r.src_entity_id
              JOIN threatweave.entities dst ON dst.id = r.dst_entity_id
            WHERE (? = '' OR src.canonical_value ILIKE ? OR dst.canonical_value ILIKE ?)
            """ + String.format(documentFilter, "relation_id", "r") + " ORDER BY r.updated_at DESC LIMIT ?";
        List<Object> relationArguments = new java.util.ArrayList<>(List.of(term, "%" + term + "%", "%" + term + "%"));
        relationArguments.addAll(scopedDocumentIds);
        relationArguments.add(limit);
        List<Map<String, Object>> relations = jdbcTemplate.queryForList(relationSql, relationArguments.toArray());
        return Map.of("entities", entities, "relations", relations);
    }

    @Override
    public Map<String, Object> describeReadModel() {
        return Map.of(
            "schemaVersion", 1,
            "datasets", List.of(
                Map.of("name", "threatweave.documents", "primaryKey", "id", "semantics", "清洗后的规范情报正文与来源元数据",
                    "fields", List.of(field("id", "文档主键"), field("doc_key", "来源内稳定标识"), field("source_id", "来源标识"), field("source_name", "来源显示名"), field("external_id", "来源文章标识"), field("title", "清洗后的标题"), field("url", "文章 URL"), field("published_at", "来源发布时间"), field("content", "清洗后的 Markdown 正文"), field("content_sha256", "正文哈希"), field("formatted_at", "格式化时间"), field("ingested_at", "入库时间"))),
                Map.of("name", "threatweave.entities", "primaryKey", "id", "semantics", "规范化实体和语义角色",
                    "fields", List.of(field("id", "实体主键"), field("entity_type", "实体类型"), field("canonical_value", "规范值"), field("display_name", "展示名称"), field("semantic_role", "语义角色"), field("confidence", "置信度"), field("first_seen_at", "首次观察时间"), field("last_seen_at", "最后观察时间"), field("updated_at", "更新时间"))),
                Map.of("name", "threatweave.entity_aliases", "primaryKey", "id", "semantics", "实体别名",
                    "fields", List.of(field("id", "别名主键"), field("entity_id", "所属实体"), field("alias", "别名文本"), field("source_url", "别名来源 URL"))),
                Map.of("name", "threatweave.relations", "primaryKey", "id", "semantics", "有向实体关系",
                    "fields", List.of(field("id", "关系主键"), field("src_entity_id", "源实体"), field("dst_entity_id", "目标实体"), field("relation_type", "关系类型"), field("confidence", "置信度"), field("first_seen_at", "首次观察时间"), field("last_seen_at", "最后观察时间"), field("updated_at", "更新时间"))),
                Map.of("name", "threatweave.provenance", "primaryKey", "id", "semantics", "实体或关系对应的文档证据与字符范围",
                    "fields", List.of(field("id", "证据主键"), field("document_id", "证据文档"), field("entity_id", "被支持的实体，可为空"), field("relation_id", "被支持的关系，可为空"), field("evidence_quote", "原文引文"), field("char_start", "引文起始字符偏移"), field("char_end", "引文结束字符偏移"), field("extractor", "抽取器标识"), field("confidence", "证据置信度"))),
                Map.of("name", "workflow.document_processing", "primaryKey", "doc_key", "semantics", "导入 Pipeline 的状态和失败信息",
                    "fields", List.of(field("doc_key", "来源内稳定标识"), field("source_id", "来源标识"), field("document_id", "规范文档主键"), field("source_fingerprint", "采集正文哈希"), field("formatted_content_sha256", "规范正文哈希"), field("status", "pending/running/completed/failed"), field("last_failed_stage", "失败阶段"), field("last_error", "受限错误信息"), field("formatted_at", "格式化时间"), field("extracted_at", "抽取完成时间"), field("updated_at", "状态更新时间")))
            ),
            "joins", List.of(
                "provenance.document_id = documents.id",
                "provenance.entity_id = entities.id",
                "provenance.relation_id = relations.id",
                "relations.src_entity_id = entities.id 或 relations.dst_entity_id = entities.id",
                "entity_aliases.entity_id = entities.id",
                "document_processing.doc_key = documents.doc_key"
            ),
            "examples", List.of(
                "SELECT id, title, url, formatted_at FROM threatweave.documents ORDER BY formatted_at DESC LIMIT 20",
                "SELECT entity_type, canonical_value, confidence FROM threatweave.entities ORDER BY updated_at DESC LIMIT 20"
            ),
            "constraints", Map.of("maxRows", ThreatWeaveReadQueryPolicy.MAX_ROWS, "parameters", "使用 ? 占位符并按顺序提供 parameters")
        );
    }

    @Override
    public Map<String, Object> executeReadQuery(ThreatWeaveRequests.ReadQueryRequest request) {
        String validated = readQueryPolicy.validate(request.sql());
        List<Object> parameters = request.parameters() == null ? List.of() : request.parameters();
        String boundedSql = "SELECT * FROM (" + validated + ") AS threatweave_read_result LIMIT " + ThreatWeaveReadQueryPolicy.MAX_ROWS;
        List<Map<String, Object>> rows = jdbcTemplate.execute((ConnectionCallback<List<Map<String, Object>>>) connection -> {
            try (PreparedStatement statement = connection.prepareStatement(boundedSql)) {
                statement.setQueryTimeout(3);
                statement.setMaxRows(ThreatWeaveReadQueryPolicy.MAX_ROWS);
                for (int index = 0; index < parameters.size(); index++) {
                    statement.setObject(index + 1, parameters.get(index));
                }
                try (ResultSet resultSet = statement.executeQuery()) {
                    return readRows(resultSet, 1024 * 1024);
                }
            }
        });
        return Map.of("rows", rows, "rowCount", rows.size(), "maxRows", ThreatWeaveReadQueryPolicy.MAX_ROWS);
    }

    private static List<Map<String, Object>> readRows(ResultSet resultSet, int maxBytes) throws java.sql.SQLException {
        List<Map<String, Object>> rows = new ArrayList<>();
        ResultSetMetaData metadata = resultSet.getMetaData();
        int byteCount = 0;
        while (resultSet.next()) {
            Map<String, Object> row = new LinkedHashMap<>();
            for (int index = 1; index <= metadata.getColumnCount(); index++) {
                Object value = resultSet.getObject(index);
                row.put(metadata.getColumnLabel(index), value);
                byteCount += String.valueOf(value).getBytes(java.nio.charset.StandardCharsets.UTF_8).length;
            }
            if (byteCount > maxBytes) {
                break;
            }
            rows.add(row);
        }
        return rows;
    }

    private static Map<String, String> field(String name, String semantics) {
        return Map.of("name", name, "semantics", semantics);
    }

    private void removeOrphanedGraphData() {
        jdbcTemplate.update("DELETE FROM threatweave.relations r WHERE NOT EXISTS (SELECT 1 FROM threatweave.provenance p WHERE p.relation_id = r.id)");
        jdbcTemplate.update("""
            DELETE FROM threatweave.entities e
            WHERE NOT EXISTS (SELECT 1 FROM threatweave.provenance p WHERE p.entity_id = e.id)
              AND NOT EXISTS (SELECT 1 FROM threatweave.relations r WHERE r.src_entity_id = e.id OR r.dst_entity_id = e.id)
            """);
    }

    private long upsertEntity(ThreatWeaveRequests.EntityInput entity) {
        String role = entity.semanticRole() == null || entity.semanticRole().isBlank() ? "unknown" : entity.semanticRole();
        jdbcTemplate.update("""
            INSERT INTO threatweave.entities (entity_type, canonical_value, display_name, semantic_role, confidence, first_seen_at, last_seen_at, updated_at)
            VALUES (?, ?, ?, ?, ?, CAST(? AS TIMESTAMPTZ), CAST(? AS TIMESTAMPTZ), now())
            ON CONFLICT (entity_type, canonical_value) DO UPDATE SET display_name = COALESCE(EXCLUDED.display_name, threatweave.entities.display_name),
              semantic_role = EXCLUDED.semantic_role, confidence = EXCLUDED.confidence, updated_at = now()
            """, entity.entityType(), entity.canonicalValue(), entity.displayName(), role, entity.confidence(), entity.firstSeenAt(), entity.lastSeenAt());
        return findEntityId(entity.entityType(), entity.canonicalValue());
    }

    private long findEntityId(String type, String canonicalValue) {
        Long id = jdbcTemplate.queryForObject("SELECT id FROM threatweave.entities WHERE entity_type = ? AND canonical_value = ?", Long.class, type, canonicalValue);
        if (id == null) {
            throw new BusinessException("关系引用的实体不存在");
        }
        return id;
    }

    private void writeEvidence(
            long documentId,
            String documentContent,
            Long entityId,
            Long relationId,
            List<ThreatWeaveRequests.EvidenceInput> evidence) {
        if (evidence == null) {
            return;
        }
        for (ThreatWeaveRequests.EvidenceInput item : evidence) {
            if (item.charStart() < 0 || item.charEnd() <= item.charStart()
                    || item.charEnd() > documentContent.length()) {
                throw new BusinessException("出处字符范围无效");
            }
            if (!documentContent.substring(item.charStart(), item.charEnd()).equals(item.evidenceQuote())) {
                throw new BusinessException("出处引文与文档正文不匹配");
            }
            jdbcTemplate.update("""
                INSERT INTO threatweave.provenance (document_id, entity_id, relation_id, evidence_quote, char_start, char_end, extractor, confidence)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING
                """, documentId, entityId, relationId, item.evidenceQuote(), item.charStart(), item.charEnd(), item.extractor(), item.confidence());
        }
    }
}
