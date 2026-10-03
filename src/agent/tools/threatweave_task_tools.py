"""System-owned handoff tools between ThreatWeave pipeline agents."""

from __future__ import annotations

import os

from langchain_core.tools import tool
from langgraph_sdk import get_client


@tool
async def submit_entity_extraction(document_id: int) -> str:
    """提交格式化文档的异步实体关系抽取任务。"""
    client = get_client(url=os.getenv("MYAGENT_ASYNC_AGENT_PROTOCOL_URL", "http://127.0.0.1:18082"))
    thread = await client.threads.create()
    run = await client.runs.create(
        thread_id=thread["thread_id"],
        assistant_id="entity_relation_extractor_system",
        input={"messages": [{
            "role": "user",
            "content": f"处理 document_id={document_id}。读取格式化文档并完成带精确出处的实体关系抽取。",
        }]},
        context={"actor": "system-scheduler"},
    )
    return f"已提交 entity_relation_extractor 任务: {thread['thread_id']}，run: {run['run_id']}"
