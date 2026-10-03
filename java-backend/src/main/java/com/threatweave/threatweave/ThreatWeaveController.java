package com.threatweave.threatweave;

import com.threatweave.common.Result;
import jakarta.validation.Valid;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/** REST boundary for A document writes, B extraction writes and C read-only graph queries. */
@RestController
@RequestMapping("/api/threatweave")
public class ThreatWeaveController {
    private final ThreatWeaveService service;

    public ThreatWeaveController(ThreatWeaveService service) {
        this.service = service;
    }

    @PostMapping("/documents")
    public Result<?> upsertDocument(@Valid @RequestBody ThreatWeaveRequests.DocumentUpsertRequest request) {
        return Result.success(service.upsertDocument(request));
    }

    @GetMapping("/documents/{documentId}")
    public Result<?> getDocument(@PathVariable long documentId) {
        return Result.success(service.getDocument(documentId));
    }

    @PostMapping("/extractions")
    public Result<?> writeExtraction(@Valid @RequestBody ThreatWeaveRequests.ExtractionWriteRequest request) {
        return Result.success(service.writeExtraction(request));
    }

    @GetMapping("/graph")
    public Result<?> queryGraph(@RequestParam(defaultValue = "") String query,
                                @RequestParam(defaultValue = "100") @Min(1) @Max(500) int limit) {
        return Result.success(service.queryGraph(query, limit));
    }
}
