"""使用调用方共享的沙箱引用启动 Agent Protocol 异步任务。"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any, Mapping

from langchain.tools import ToolRuntime
from langchain_core.messages import ToolMessage
from langchain_core.tools import tool
from langgraph.types import Command
from langgraph_sdk import get_client

from agent.backends.sandbox_proxy import SandboxBackendProxy


TERMINAL_RUN_STATUSES = frozenset({"success", "error", "interrupted", "cancelled", "timeout"})


def _tracked_tasks(runtime: ToolRuntime) -> dict[str, dict[str, Any]]:
    """读取当前会话已登记的远程任务，避免跨会话查询或操作任务。"""
    state = runtime.state
    raw_tasks = state.get("async_tasks", {}) if isinstance(state, Mapping) else {}
    if not isinstance(raw_tasks, Mapping):
        return {}
    return {
        str(task_id): dict(task)
        for task_id, task in raw_tasks.items()
        if isinstance(task, Mapping)
    }


def _normalized_run_status(run: Any) -> str:
    """将 Agent Protocol 的运行状态归一化为对话工具使用的稳定枚举。"""
    status = run.get("status", "unknown") if isinstance(run, Mapping) else getattr(run, "status", "unknown")
    return {"failed": "error", "canceled": "cancelled", "timed_out": "timeout"}.get(
        str(status).lower(), str(status).lower(),
    )


def _run_id(run: Any) -> str | None:
    """兼容 SDK 返回对象与字典两种运行记录。"""
    value = run.get("run_id") if isinstance(run, Mapping) else getattr(run, "run_id", None)
    return str(value) if value else None


def create_async_sandbox_tools(
    async_subagents: list[dict[str, Any]],
    *,
    sandbox_backend: SandboxBackendProxy,
) -> list[Any]:
    """构建异步任务工具，并转发当前用户的沙箱 ID。"""
    registrations = {str(item["name"]): item for item in async_subagents}

    @tool
    async def start_async_task(
        description: str,
        subagent_type: str,
        runtime: ToolRuntime,
    ) -> str | Command:
        """启动后台子 Agent，并将任务写入主图状态供后续查询。"""
        registration = registrations.get(subagent_type)
        if registration is None:
            return f"未知异步子 Agent: {subagent_type}"
        try:
            sandbox_id = await asyncio.to_thread(lambda: sandbox_backend.id)
            client = get_client(url=str(registration["url"]))
            thread = await client.threads.create()
            run = await client.runs.create(
                thread_id=thread["thread_id"],
                assistant_id=str(registration["graph_id"]),
                input={"messages": [{
                    "role": "user",
                    "content": description,
                }]},
                context={"sandbox_id": sandbox_id},
            )
        except Exception as error:  # Agent Protocol 客户端的传输异常未提供稳定类型。
            return f"启动异步子 Agent 失败: {error}"

        # 自定义启动流程必须维护 DeepAgents 的 async_tasks 状态；否则默认的
        # list_async_tasks 只会读取空状态，无法查询刚刚提交的远程任务。
        task_id = str(thread["thread_id"])
        now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        tracked_task = {
            "task_id": task_id,
            "agent_name": subagent_type,
            "thread_id": task_id,
            "run_id": str(run["run_id"]),
            "status": "running",
            "created_at": now,
            "last_checked_at": now,
            "last_updated_at": now,
        }
        message = f"已启动异步子 Agent。task_id: {task_id}"
        return Command(
            update={
                "messages": [ToolMessage(message, tool_call_id=runtime.tool_call_id)],
                "async_tasks": {task_id: tracked_task},
            }
        )

    @tool
    async def check_async_task(task_id: str, runtime: ToolRuntime) -> str | Command:
        """查询当前会话中一个异步任务的最新状态。"""
        tasks = _tracked_tasks(runtime)
        tracked_task = tasks.get(task_id)
        if tracked_task is None:
            return "未找到当前会话中的异步任务。"
        if str(tracked_task.get("status", "")).lower() in TERMINAL_RUN_STATUSES:
            return f"任务状态：{tracked_task['status']}。"
        try:
            registration = registrations[str(tracked_task["agent_name"])]
            runs = await get_client(url=str(registration["url"])).runs.list(task_id, limit=1)
        except Exception:
            return "暂时无法查询任务状态，请稍后重试。"
        if not runs:
            return "任务状态：pending。"

        latest_run = runs[0]
        status = _normalized_run_status(latest_run)
        now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        updated_task = {
            **tracked_task,
            "status": status,
            "run_id": _run_id(latest_run) or tracked_task.get("run_id"),
            "last_checked_at": now,
            "last_updated_at": now,
        }
        return Command(
            update={
                "messages": [ToolMessage(f"任务状态：{status}。", tool_call_id=runtime.tool_call_id)],
                "async_tasks": {task_id: updated_task},
            }
        )

    @tool
    def list_async_tasks(runtime: ToolRuntime) -> str:
        """列出当前会话已提交的异步任务及其最近状态。"""
        tasks = _tracked_tasks(runtime)
        if not tasks:
            return "当前会话没有已提交的异步任务。"
        return "\n".join(
            f"{task_id}: {task.get('agent_name', 'unknown')}，状态：{task.get('status', 'unknown')}"
            for task_id, task in tasks.items()
        )

    @tool
    async def cancel_async_task(task_id: str, runtime: ToolRuntime) -> str | Command:
        """取消当前会话中仍在执行的异步任务。"""
        tasks = _tracked_tasks(runtime)
        tracked_task = tasks.get(task_id)
        if tracked_task is None:
            return "未找到当前会话中的异步任务。"
        if str(tracked_task.get("status", "")).lower() in TERMINAL_RUN_STATUSES:
            return f"任务已经结束，当前状态：{tracked_task['status']}。"
        run_id = str(tracked_task.get("run_id") or "")
        if not run_id:
            return "任务尚未分配可取消的运行记录，请稍后重试。"
        try:
            registration = registrations[str(tracked_task["agent_name"])]
            await get_client(url=str(registration["url"])).runs.cancel(task_id, run_id)
        except Exception:
            return "暂时无法取消任务，请稍后重试。"

        now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        updated_task = {**tracked_task, "status": "cancelled", "last_updated_at": now}
        return Command(
            update={
                "messages": [ToolMessage("任务已取消。", tool_call_id=runtime.tool_call_id)],
                "async_tasks": {task_id: updated_task},
            }
        )

    return [start_async_task, check_async_task, list_async_tasks, cancel_async_task]
