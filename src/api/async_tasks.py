"""查询远程异步任务，并将终态结果投递到所属主会话。"""

from __future__ import annotations

import os
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from langgraph_sdk import get_client

from agent.schema import AsyncTaskStatusResponse, AuthResponse
from api.agent_loader import agent_loader
from api.auth import get_current_user
from api.message_utils import content_to_text, extract_visualization
from services.deliverables import extract_deliverable_specs


router = APIRouter()

ASYNC_AGENT_PROTOCOL_URL = os.getenv(
    "MYAGENT_ASYNC_AGENT_PROTOCOL_URL",
    os.getenv("MYAGENT_ASYNC_CHART_URL", "http://127.0.0.1:18082"),
)
TERMINAL_RUN_STATUSES = {"success", "error", "interrupted", "cancelled", "timeout"}
TASK_ID_PATTERN = re.compile(
    r"\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)
_RUN_LIMIT_ERROR_PATTERN = re.compile(
    r"(?:tool call limit reached|model call limits exceeded)",
    re.IGNORECASE,
)
_INTERNAL_ARTIFACT_LINE_PATTERN = re.compile(
    r"(?im)^[^\r\n]*(?:资源(?:\s|\*|_)*ID|静态(?:\s|\*|_)*HTML(?:\s|\*|_)*文件)[^\r\n]*(?:\r?\n|$)",
)
REPORT_REQUEST_PATTERN = re.compile(r"(?:报告|报表|分析报告|markdown)|\breport\b", re.IGNORECASE)
REPORT_REQUEST_NEGATION_PATTERN = re.compile(
    r"(?:不要|无需|不需要|不用|不生成|不提供|不输出)[^。！？\n]{0,12}"
    r"(?:报告|报表|分析报告|markdown)|(?:no|without|不要)\s+\breport",
    re.IGNORECASE,
)
GRAPH_REQUEST_PATTERN = re.compile(
    r"(?:图谱|关系图|网络图|可视化|HTML\s*图|画(?:一张|个)?图)|\bgraph\b",
    re.IGNORECASE,
)
DELIVERABLE_LINE_PATTERN = re.compile(
    r"(?im)^\s*DELIVERABLE\s*:\s*"
    r"(/deliverables/[A-Za-z0-9][A-Za-z0-9_.-]*\.(?:md|html|json))\s*\|\s*"
    r"(text/markdown|text/html|application/json)\s*\|\s*([^\r\n|]{1,120})\s*$"
)


def _get_attr(value: Any, key: str, default: Any = None) -> Any:
    """兼容 LangGraph SDK 返回的字典和对象属性访问。"""
    return value.get(key, default) if isinstance(value, dict) else getattr(value, key, default)


def _normalized_run_status(run: Any) -> str:
    """统一 Agent Protocol 的终态别名，供最新与历史运行比较。"""
    status = str(_get_attr(run, "status", "unknown")).lower()
    return {"failed": "error", "canceled": "cancelled", "timed_out": "timeout"}.get(status, status)


def extract_async_task_id(value: Any) -> str | None:
    """从异步任务工具结果中提取 Agent Protocol 分配的任务线程 ID。"""
    if isinstance(value, str):
        match = TASK_ID_PATTERN.search(value)
        return match.group(0) if match else None
    if isinstance(value, list):
        for item in value:
            task_id = extract_async_task_id(item)
            if task_id:
                return task_id
    if isinstance(value, dict):
        for item in value.values():
            task_id = extract_async_task_id(item)
            if task_id:
                return task_id
    return None


def _message_role(message: Any) -> str:
    """将 SDK 消息角色归一化为页面使用的角色名称。"""
    role = _get_attr(message, "role")
    if role:
        return {"human": "user", "ai": "assistant"}.get(str(role), str(role))
    message_type = str(_get_attr(message, "type", type(message).__name__)).lower()
    if "human" in message_type:
        return "user"
    if "tool" in message_type:
        return "tool"
    return "assistant"


def _extract_task_output(values: Any) -> tuple[str, dict[str, str] | None]:
    """提取最终助手文本及最后一个可展示的图表 artifact。"""
    if not isinstance(values, dict):
        return "", None
    messages = values.get("messages")
    if not isinstance(messages, list):
        return "", None

    content = ""
    visualization: dict[str, str] | None = None
    for message in reversed(messages):
        message_content = _get_attr(message, "content", "")
        if visualization is None:
            visualization = extract_visualization(message_content)
        if not content and _message_role(message) == "assistant":
            content = content_to_text(message_content).strip()
        if content and visualization:
            break
    return content, visualization


def _task_requests_report(values: Any) -> bool:
    """根据异步任务的原始用户请求判断是否应要求报告文件。"""
    if not isinstance(values, dict) or not isinstance(values.get("messages"), list):
        return False
    for message in values["messages"]:
        if _message_role(message) != "user":
            continue
        content = content_to_text(_get_attr(message, "content", ""))
        if REPORT_REQUEST_NEGATION_PATTERN.search(content):
            continue
        if REPORT_REQUEST_PATTERN.search(content):
            return True
    return False


def _requested_deliverable_mime_types(values: Any) -> set[str]:
    """从任务原始请求提取明确交付类型，拒绝模型自行附加的文件。"""
    if not isinstance(values, dict) or not isinstance(values.get("messages"), list):
        return set()
    requested: set[str] = set()
    for message in values["messages"]:
        if _message_role(message) != "user":
            continue
        content = content_to_text(_get_attr(message, "content", ""))
        if GRAPH_REQUEST_PATTERN.search(content):
            requested.add("text/html")
        if not REPORT_REQUEST_NEGATION_PATTERN.search(content) and REPORT_REQUEST_PATTERN.search(content):
            requested.add("text/markdown")
    return requested


def _extract_error(run: Any, state: Any) -> str | None:
    """从运行元数据或线程任务中提取可供用户查看的失败原因。"""
    metadata = _get_attr(run, "metadata", {}) or {}
    if isinstance(metadata, dict):
        for key in ("error", "exception", "message"):
            if metadata.get(key):
                return str(metadata[key])
    for task in _get_attr(state, "tasks", []) or []:
        error = _get_attr(task, "error")
        if error:
            return str(error)
    return None


def _run_limit_error(content: str) -> str | None:
    """将框架限额错误转换为不暴露内部运行细节的业务说明。"""
    if _RUN_LIMIT_ERROR_PATTERN.search(content):
        return "分析任务超过本次执行限额，未能形成完整结论。请重试或缩小分析范围。"
    return None


def _extract_deliverables(content: str) -> list[dict[str, str]]:
    """兼容旧任务的文本交付协议。"""
    return [
        {"path": path, "mime_type": mime_type, "label": label.strip()}
        for path, mime_type, label in DELIVERABLE_LINE_PATTERN.findall(content)
    ]


def _extract_task_deliverables(values: Any, content: str) -> list[dict[str, str]]:
    """优先读取实际写入工具结果，兼容旧 Agent 的最终文本声明。"""
    specifications: list[dict[str, str]] = []
    if isinstance(values, dict) and isinstance(values.get("messages"), list):
        for message in values["messages"]:
            if _get_attr(message, "name") == "write_deliverable":
                specifications.extend(extract_deliverable_specs(_get_attr(message, "content", "")))
    if not specifications:
        specifications = _extract_deliverables(content)
    return list({item["path"]: item for item in specifications}.values())


def _keep_latest_requested_deliverables(
    specifications: list[dict[str, str]],
    requested_mime_types: set[str],
) -> list[dict[str, str]]:
    """每种用户明确请求的交付类型只保留本次任务最后生成的一个文件。"""
    latest: dict[str, dict[str, str]] = {}
    for specification in specifications:
        mime_type = specification["mime_type"]
        if mime_type in requested_mime_types:
            latest[mime_type] = specification
    return list(latest.values())


def _sanitize_task_content(content: str) -> str:
    """移除系统登记报告和图表时不应展示的内部标识。"""
    content = DELIVERABLE_LINE_PATTERN.sub("", content)
    return _INTERNAL_ARTIFACT_LINE_PATTERN.sub("", content).strip()


async def get_async_task_status(
    task_id: str, user_id: str,
) -> AsyncTaskStatusResponse:
    """返回任务状态；终态结果写入主会话并回传任务卡片所需的 artifact。"""
    binding = await agent_loader.get_async_task_binding(task_id)
    if binding is None or binding.user_id != user_id:
        raise HTTPException(status_code=404, detail="任务不存在或无权访问")
    await agent_loader.require_session(user_id, binding.thread_id)
    client = get_client(url=ASYNC_AGENT_PROTOCOL_URL)
    try:
        runs = await client.runs.list(task_id, limit=10)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail="无法查询异步子 Agent 任务，请稍后重试",
        ) from exc

    if not runs:
        return AsyncTaskStatusResponse(task_id=task_id, status="pending", done=False)

    latest_run = runs[0]
    status = _normalized_run_status(latest_run)
    is_terminal = status in TERMINAL_RUN_STATUSES
    if not is_terminal:
        return AsyncTaskStatusResponse(
            task_id=task_id, status=status, done=False,
            run_id=_get_attr(latest_run, "run_id"),
        )
    state = None
    content = ""
    visualization = None
    report_requested = False
    deliverable_specs: list[dict[str, str]] = []
    try:
        state = await client.threads.get_state(task_id)
        values = _get_attr(state, "values", {})
        content, visualization = _extract_task_output(values)
        report_requested = _task_requests_report(values)
        deliverable_specs = _extract_task_deliverables(values, content)
        requested_mime_types = _requested_deliverable_mime_types(values)
        deliverable_specs = _keep_latest_requested_deliverables(
            deliverable_specs,
            requested_mime_types,
        )
        content = _sanitize_task_content(content)
    except Exception as exc:
        # run 成功不代表已读到结果；失败必须可重试，不能写入占位成功消息。
        raise HTTPException(status_code=502, detail="无法读取异步任务结果，请稍后重试") from exc

    # 旧版 DeepAgents 暴露过 update_async_task。它会在原线程追加一次运行，更新失败时
    # 不应覆盖同一线程中已经成功写入且仍可读取的交付件。
    prior_successful_run = next(
        (run for run in runs[1:] if _normalized_run_status(run) == "success"), None,
    )
    if status != "success" and prior_successful_run is not None and (content or visualization or deliverable_specs):
        latest_run = prior_successful_run
        status = "success"

    delivered = False
    error = None
    if is_terminal:
        artifact = None
        if visualization is not None and visualization.get("artifact_id"):
            artifact = {
                "type": "chart_artifact",
                "artifact_id": visualization["artifact_id"],
                "mime_type": visualization.get("mime_type", "text/html"),
            }
        elif visualization is not None:
            artifact = {
                "type": "image", "src": visualization["src"],
                "mime_type": visualization.get("mime_type", "image/png"),
            }
        limit_error = _run_limit_error(content)
        deliverables: list[dict[str, str]] = []
        if status == "success" and limit_error:
            # Agent Protocol 会将中间件的 end 视为成功终态；不能把限额错误伪装成报告。
            status = "error"
            error = limit_error
            main_message = (
                "图表已生成，但本次分析未能形成完整结论。"
                "请重试或缩小分析范围后再获取报告。"
                if visualization is not None
                else f"后台任务未完成：{error}"
            )
        elif status == "success":
            has_markdown_deliverable = any(
                item["mime_type"] == "text/markdown" for item in deliverable_specs
            )
            has_html_deliverable = any(item["mime_type"] == "text/html" for item in deliverable_specs)
            if report_requested and not has_markdown_deliverable:
                status = "error"
                error = "威胁分析未生成可下载报告，请重试。"
                main_message = f"后台任务未完成：{error}"
            elif visualization is None and not content and not deliverable_specs:
                # 通用异步任务允许返回普通文本；只有完全没有正文和交付物时，
                # 才能判定为远端成功状态下的空结果，避免伪造成功交付。
                raise HTTPException(status_code=502, detail="异步任务未返回可交付结果，请稍后重试")
            else:
                # 图表-only 请求不应因为没有报告而失败。
                main_message = "图表已生成。" if visualization else content
        else:
            error = _extract_error(latest_run, state) or {
                "timeout": "后台任务超时。", "cancelled": "后台任务已取消。",
                "interrupted": "后台任务已中断。",
            }.get(status, "后台任务未能完成。")
            main_message = f"后台任务未完成：{error}"
        if status == "success" and deliverable_specs:
            try:
                deliverables = await agent_loader.register_sandbox_deliverables(task_id, deliverable_specs)
            except Exception as exc:
                raise HTTPException(
                    status_code=502,
                    detail="无法登记任务交付件，请稍后重试",
                ) from exc
            if len(deliverables) != len(deliverable_specs):
                status = "error"
                error = "任务交付件无效或不可用，请重试。"
                main_message = f"后台任务未完成：{error}"
            else:
                main_message = f"已生成 {len(deliverables)} 个交付件。"
        try:
            delivered = await agent_loader.publish_async_task_result(
                task_id,
                content=main_message,
                artifact=artifact,
                deliverables=deliverables,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail="无法将异步任务结果写入主会话，请稍后重试",
            ) from exc

    return AsyncTaskStatusResponse(
        task_id=task_id,
        status=status,
        done=is_terminal,
        delivered=delivered,
        result=main_message if is_terminal else None,
        visualization=visualization if is_terminal else None,
        deliverables=[
            {
                **deliverable,
                "download_src": f"/deliverables/{deliverable['artifact_id']}?user_id={user_id}",
                "preview_src": (
                    f"/deliverables/{deliverable['artifact_id']}?user_id={user_id}&preview=1"
                    if deliverable["mime_type"] == "text/html" else None
                ),
            }
            for deliverable in deliverables
        ],
        error=error,
        run_id=_get_attr(latest_run, "run_id"),
        updated_at=str(_get_attr(latest_run, "updated_at", "")) or None,
    )


@router.get("/async-tasks/{task_id}", response_model=AsyncTaskStatusResponse)
async def authenticated_get_async_task_status(
    task_id: str,
    current_user: AuthResponse = Depends(get_current_user),
) -> AsyncTaskStatusResponse:
    """读取当前登录用户拥有的异步任务状态。"""
    return await get_async_task_status(task_id, current_user.user_id)
