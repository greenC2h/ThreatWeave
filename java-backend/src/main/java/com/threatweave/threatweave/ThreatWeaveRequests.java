package com.threatweave.threatweave;

import jakarta.validation.Valid;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotEmpty;
import jakarta.validation.constraints.NotNull;
import java.util.List;

/** 定义 ThreatWeave 采集与抽取边界使用的请求对象。 */
public final class ThreatWeaveRequests {
    private ThreatWeaveRequests() { }

    public record DocumentUpsertRequest(
            @NotBlank String docKey, @NotBlank String sourceName, String externalId,
            String title, String url, String publishedAt, @NotBlank String content,
            @NotBlank String contentSha256) { }

    public record EntityInput(
            @NotBlank String entityType, @NotBlank String canonicalValue, String displayName,
            String semanticRole, @Min(0) @Max(100) Integer confidence, String firstSeenAt,
            String lastSeenAt, List<String> aliases, @Valid List<EvidenceInput> evidence) { }

    public record RelationInput(
            @NotBlank String srcEntityType, @NotBlank String srcCanonicalValue,
            @NotBlank String dstEntityType, @NotBlank String dstCanonicalValue,
            @NotBlank String relationType, @Min(0) @Max(100) Integer confidence,
            String firstSeenAt, String lastSeenAt, @Valid List<EvidenceInput> evidence) { }

    public record EvidenceInput(
            @NotBlank String evidenceQuote, @Min(0) int charStart, @Min(1) int charEnd,
            @NotBlank String extractor, @Min(0) @Max(100) Integer confidence) { }

    public record ExtractionWriteRequest(
            @NotNull Long documentId, @NotEmpty @Valid List<EntityInput> entities,
            @Valid List<RelationInput> relations) { }
}
