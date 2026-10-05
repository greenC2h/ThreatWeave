"""向同步编排子 Agent 暴露的受控情报工作流工具。"""

from __future__ import annotations

import json

from langchain_core.tools import BaseTool, tool

from agent.backends.sandbox_proxy import SandboxBackendProxy
from agent.subagents.internal_runner import run_internal_subagent
from intelligence_workflow.document_gateway import DocumentGateway
from intelligence_workflow.repository import WorkflowRepository
from intelligence_workflow.schema import IntelligenceWorkflowMode, IntelligenceWorkflowRequest
from intelligence_workflow.workflow import IntelligenceWorkflow, WorkflowAgents
from intel_ingestor.sources import resolve_source_for_article
from services.deliverables import DeliverableRegistry, write_deliverable_content


def create_intelligence_workflow(
    sandbox_backend: SandboxBackendProxy,
    deliverable_registry: DeliverableRegistry | None = None,
) -> IntelligenceWorkflow:
    """构造绑定调用方沙箱的确定性工作流服务。"""
    return IntelligenceWorkflow(
        repository=WorkflowRepository(),
        document_gateway=DocumentGateway(),
        agents=WorkflowAgents(
            formatter=lambda instruction: run_internal_subagent(
                "intel_ingestor", instruction, sandbox_backend
            ),
            preview_extractor=lambda instruction: run_internal_subagent(
                "entity_relation_extractor_preview", instruction, sandbox_backend
            ),
            commit_extractor=lambda instruction: run_internal_subagent(
                "entity_relation_extractor_commit", instruction, sandbox_backend
            ),
            draft_commit_extractor=lambda instruction: run_internal_subagent(
                "entity_relation_extractor_draft_commit", instruction, sandbox_backend
            ),
            deliverable_writer=lambda **kwargs: write_deliverable_content(sandbox_backend, **kwargs),
        ),
        deliverable_registry=deliverable_registry,
    )


def create_intelligence_workflow_tools(
    actor_id: str,
    sandbox_backend: SandboxBackendProxy,
    deliverable_registry: DeliverableRegistry,
) -> list[BaseTool]:
    """创建绑定身份和沙箱的工具，避免模型伪造用户归属或执行环境。"""
    @tool
    async def run_intelligence_workflow(
        mode: str,
        source_id: str | None = None,
        article_url: str | None = None,
        document_ids: list[int] | None = None,
        max_articles: int = 3,
        force_refresh: bool = False,
        requested_deliverables: list[str] | None = None,
    ) -> dict[str, object]:
        """同步执行指定的情报处理工作流并返回完整的紧凑结果。

        仅可使用 `format_only`、`extract_preview`、`ingest_full`、`extract_pending` 或
        `list_processing`。来源任务使用 `source_id`，已有文档任务使用 `document_ids`；
        `extract_pending` 可省略 `source_id` 以处理全部来源。`article_url` 必须属于该来源。
        可选交付类型为 `formatted_markdown` 与 `extraction_markdown`，仅用户明确要求下载时传入。
        只提供文章 URL 时，工具会从已启用来源清单中解析唯一 source_id。
        工具会等待 A/B 处理完成，不创建可轮询的异步任务。
        """
        # 模型有时会把用户提供的 URL 填入 source_id；在工具边界修正参数归位，
        # 防止未经来源校验的 URL 进入采集器。
        if source_id and source_id.lower().startswith(("http://", "https://")):
            if article_url and article_url != source_id:
                raise ValueError("source_id 与 article_url 不能同时携带不同 URL")
            article_url, source_id = source_id, None
        resolved_source_id = source_id
        if article_url and not resolved_source_id:
            resolved_source_id = resolve_source_for_article(article_url)
        request = IntelligenceWorkflowRequest(
            mode=IntelligenceWorkflowMode(mode),
            actor_id=actor_id,
            source_id=resolved_source_id,
            article_url=article_url,
            document_ids=document_ids or [],
            max_articles=max_articles,
            force_refresh=force_refresh,
            requested_deliverables=requested_deliverables or [],
        )
        result = await create_intelligence_workflow(sandbox_backend, deliverable_registry).run(request)
        payload = result.model_dump(mode="json")
        # 子 Agent 的 tool result 是 SSE 的唯一同步出口；把登记后的 artifact 放入
        # 结构化内容，前端与历史接口才能产生受用户权限约束的下载入口。
        payload["deliverables"] = [
            deliverable.model_dump(mode="json") for deliverable in result.deliverables
        ]
        return payload

    @tool
    async def list_intelligence_processing(source_id: str | None = None) -> str:
        """列出已格式化、待抽取、失败或已完成的文章状态，不触发任何处理。"""
        request = IntelligenceWorkflowRequest(
            mode=IntelligenceWorkflowMode.LIST_PROCESSING,
            actor_id=actor_id,
            source_id=source_id,
        )
        result = await create_intelligence_workflow(sandbox_backend, deliverable_registry).run(request)
        return json.dumps(result.model_dump(mode="json"), ensure_ascii=False)

    return [run_intelligence_workflow, list_intelligence_processing]
