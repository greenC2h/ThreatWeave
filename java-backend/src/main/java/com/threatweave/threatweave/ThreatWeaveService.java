package com.threatweave.threatweave;

import java.util.Map;

/** 定义 ThreatWeave Agent 使用的最小 CRUD 服务接口。 */
public interface ThreatWeaveService {
    Map<String, Object> upsertDocument(ThreatWeaveRequests.DocumentUpsertRequest request);

    Map<String, Object> getDocument(long documentId);

    Map<String, Object> writeExtraction(ThreatWeaveRequests.ExtractionWriteRequest request);

    Map<String, Object> queryGraph(String query, int limit);
}
