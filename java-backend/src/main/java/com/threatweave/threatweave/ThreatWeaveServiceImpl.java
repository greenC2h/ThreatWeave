package com.threatweave.threatweave;

import com.threatweave.common.exception.BusinessException;
import java.util.List;
import java.util.Map;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

/** JDBC implementation of the confirmed document, graph and provenance model. */
@Service
public class ThreatWeaveServiceImpl implements ThreatWeaveService {
    private final JdbcTemplate jdbcTemplate;

    public ThreatWeaveServiceImpl(JdbcTemplate jdbcTemplate) {
        this.jdbcTemplate = jdbcTemplate;
    }

    @Override
    @Transactional
    public Map<String, Object> upsertDocument(ThreatWeaveRequests.DocumentUpsertRequest request) {
        jdbcTemplate.update("""
            INSERT INTO threatweave.documents (doc_key, source_name, external_id, title, url, published_at, content, content_sha256, formatted_at, ingested_at)
            VALUES (?, ?, ?, ?, ?, CAST(? AS TIMESTAMPTZ), ?, ?, now(), now())
            ON CONFLICT (doc_key) DO UPDATE SET source_name = EXCLUDED.source_name, external_id = EXCLUDED.external_id,
              title = EXCLUDED.title, url = EXCLUDED.url, published_at = EXCLUDED.published_at, content = EXCLUDED.content,
              content_sha256 = EXCLUDED.content_sha256, formatted_at = now(), ingested_at = now()
            """, request.docKey(), request.sourceName(), request.externalId(), request.title(), request.url(),
            request.publishedAt(), request.content(), request.contentSha256());
        return getDocumentByKey(request.docKey());
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
    @Transactional
    public Map<String, Object> writeExtraction(ThreatWeaveRequests.ExtractionWriteRequest request) {
        Map<String, Object> document = getDocument(request.documentId());
        String documentContent = (String) document.get("content");
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
        return Map.of("documentId", request.documentId(), "entitiesProcessed", entityCount, "relationsProcessed", relationCount);
    }

    @Override
    public Map<String, Object> queryGraph(String query, int limit) {
        String term = query == null ? "" : query.trim();
        List<Map<String, Object>> entities = jdbcTemplate.queryForList("""
            SELECT e.* FROM threatweave.entities e WHERE ? = '' OR e.canonical_value ILIKE ? OR e.display_name ILIKE ?
            ORDER BY e.updated_at DESC LIMIT ?
            """, term, "%" + term + "%", "%" + term + "%", limit);
        List<Map<String, Object>> relations = jdbcTemplate.queryForList("""
            SELECT r.*, src.entity_type AS src_entity_type, src.canonical_value AS src_canonical_value,
                   dst.entity_type AS dst_entity_type, dst.canonical_value AS dst_canonical_value
            FROM threatweave.relations r JOIN threatweave.entities src ON src.id = r.src_entity_id
              JOIN threatweave.entities dst ON dst.id = r.dst_entity_id
            WHERE ? = '' OR src.canonical_value ILIKE ? OR dst.canonical_value ILIKE ?
            ORDER BY r.updated_at DESC LIMIT ?
            """, term, "%" + term + "%", "%" + term + "%", limit);
        return Map.of("entities", entities, "relations", relations);
    }

    private Map<String, Object> getDocumentByKey(String docKey) {
        return jdbcTemplate.queryForMap("SELECT * FROM threatweave.documents WHERE doc_key = ?", docKey);
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
