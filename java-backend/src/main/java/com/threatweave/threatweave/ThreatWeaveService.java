package com.threatweave.threatweave;

import com.threatweave.common.exception.BusinessException;
import java.util.List;
import java.util.Map;

/** Defines the narrow CRUD surface used by ThreatWeave agents. */
public interface ThreatWeaveService {
    Map<String, Object> upsertDocument(ThreatWeaveRequests.DocumentUpsertRequest request);

    Map<String, Object> getDocument(long documentId);

    Map<String, Object> writeExtraction(ThreatWeaveRequests.ExtractionWriteRequest request);

    Map<String, Object> queryGraph(String query, int limit);
}
