"""使用 Jev 对当前请求做短时、可回退的任务分类。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from typing import Any
from uuid import uuid4

import httpx

from agent.config import (
    JEVMODEL_API_KEY,
    JEVMODEL_BASE_URL,
    JEVMODEL_ENABLED,
    JEVMODEL_MODEL,
    JEVMODEL_TIMEOUT_SECONDS,
)
from agent.schema import TaskIntent


logger = logging.getLogger(__name__)
_MAX_STATE_CHARACTERS = 8_000
_CURRENT_MESSAGE_BUDGET = 6_000
_HISTORY_BUDGET = 1_600
_MIN_CHOICE_PROBABILITY = 0.85
_MIN_CHOICE_MARGIN = 0.20
_YES_PROBABILITY_THRESHOLD = 0.85
_NO_PROBABILITY_THRESHOLD = 0.15

_QUESTIONS = {
    "task_type": {
        "type": "choice",
        "instructions": (
            "Classify the user's current request for a threat-intelligence Agent. "
            "Use conversation context only to resolve references."
        ),
        "criteria": {
            "article_import": "Import a public article URL or a configured intelligence source.",
            "single_document_query": "Read or export one explicitly identified stored article.",
            "library_analysis": "Query the intelligence library, list records, compute statistics, or analyze relationships.",
            "current_result_revision": "Revise, redraw, or transform results already available in this conversation.",
            "task_management": "Check, list, or cancel background analysis tasks.",
            "skill_management": "Install, assign, update, list, or remove Agent skills.",
            "general": "General explanation, public search, or ordinary file work outside the specialized paths.",
            "mixed": "Contains multiple independent tasks that should be separated.",
            "unclear": "The task cannot be classified from the current request and context.",
        },
    },
    "scope": {
        "type": "choice",
        "instructions": (
            "Classify the data scope explicitly requested by the user. Do not infer restrictions from preferences "
            "or old requests."
        ),
        "criteria": {
            "single_document": "One explicitly identified article or document.",
            "specified_objects": "One or more explicitly named indicators, entities, documents, or sources.",
            "entire_library": "The whole stored ThreatWeave intelligence library.",
            "current_conversation": "Only results or files already available in the current conversation.",
            "unspecified": "The request has no explicit usable data scope.",
            "not_applicable": "The task does not need a ThreatWeave data scope.",
        },
    },
    "wants_markdown_report": {
        "type": "noul",
        "instructions": "The user explicitly asks for a Markdown report or Markdown file in this request.",
    },
    "wants_html_chart": {
        "type": "noul",
        "instructions": "The user explicitly asks for an HTML chart, graph, visualization, or diagram in this request.",
    },
}


def _message_text(message: Any) -> str:
    """读取历史消息的文本，避免把工具参数或内部结果送给分类器。"""
    content = message.get("content", "") if isinstance(message, dict) else getattr(message, "content", "")
    if isinstance(content, list):
        return "".join(
            str(item.get("text", "")) if isinstance(item, dict) else str(item)
            for item in content
        ).strip()
    return str(content or "").strip()


def _message_role(message: Any) -> str:
    """兼容 LangChain 消息和字典表示的角色字段。"""
    return str(message.get("role", "") if isinstance(message, dict) else getattr(message, "type", "")).lower()


def build_task_intent_state(current_message: str, previous_messages: Iterable[Any]) -> str:
    """构造 8,000 字符以内的分类状态，当前消息始终优先保留。"""
    current = current_message.strip()[:_CURRENT_MESSAGE_BUDGET]
    history: list[str] = []
    used_characters = 0
    for message in reversed(list(previous_messages)):
        role = _message_role(message)
        if role not in {"human", "user", "ai", "assistant"}:
            continue
        additional_kwargs = (
            message.get("additional_kwargs", {})
            if isinstance(message, dict)
            else getattr(message, "additional_kwargs", {})
        ) or {}
        if additional_kwargs.get("internal_async_task_result"):
            continue
        text = _message_text(message)
        if not text:
            continue
        item = f"{role}: {text[:600]}"
        if used_characters + len(item) > _HISTORY_BUDGET:
            break
        history.append(item)
        used_characters += len(item)
        if len(history) == 3:
            break
    sections = [f"Current user request:\n{current}"]
    if history:
        sections.append("Recent conversation context:\n" + "\n".join(reversed(history)))
    return "\n\n".join(sections)[:_MAX_STATE_CHARACTERS]


def _trusted_choice(answer: Any) -> str | None:
    """只接受高概率且领先第二候选的 choice 结果。"""
    if not isinstance(answer, dict) or answer.get("type") != "choice":
        return None
    choice = answer.get("choice")
    probabilities = answer.get("probabilities")
    if not isinstance(choice, str) or not isinstance(probabilities, dict):
        return None
    selected = probabilities.get(choice)
    if not isinstance(selected, (int, float)):
        return None
    runner_up = max(
        (
            value
            for label, value in probabilities.items()
            if label != choice and isinstance(value, (int, float))
        ),
        default=0.0,
    )
    if selected < _MIN_CHOICE_PROBABILITY or selected - runner_up < _MIN_CHOICE_MARGIN:
        return None
    return choice


def _trusted_noul(answer: Any) -> bool | None:
    """只接受明确的肯定或否定概率，保留中间区间给主 Agent 自行判断。"""
    if not isinstance(answer, dict) or answer.get("type") != "noul":
        return None
    probability = answer.get("noul")
    if not isinstance(probability, (int, float)):
        return None
    if probability >= _YES_PROBABILITY_THRESHOLD:
        return True
    if probability <= _NO_PROBABILITY_THRESHOLD:
        return False
    return None


def _parse_task_intent(payload: Any) -> TaskIntent | None:
    """校验 Jev 响应，并删除不足以支撑路由的分类字段。"""
    if not isinstance(payload, dict) or not isinstance(payload.get("answers"), dict):
        return None
    answers = payload["answers"]
    intent = TaskIntent(
        task_type=_trusted_choice(answers.get("task_type")),
        scope=_trusted_choice(answers.get("scope")),
        wants_markdown_report=_trusted_noul(answers.get("wants_markdown_report")),
        wants_html_chart=_trusted_noul(answers.get("wants_html_chart")),
        model=str(payload.get("model") or JEVMODEL_MODEL),
    )
    return intent if any((
        intent.task_type,
        intent.scope,
        intent.wants_markdown_report is not None,
        intent.wants_html_chart is not None,
    )) else None


async def _request_task_intent(client: httpx.AsyncClient, state: str) -> TaskIntent | None:
    """执行单次 Jev 请求；请求失败只记录诊断，不影响主 Agent 运行。"""
    try:
        response = await asyncio.wait_for(
            client.post(
                "/v1/systemone",
                headers={
                    "Authorization": f"Bearer {JEVMODEL_API_KEY}",
                    "Idempotency-Key": f"threatweave-intent-{uuid4().hex}",
                },
                json={"model": JEVMODEL_MODEL, "state": state, "questions": _QUESTIONS},
            ),
            timeout=JEVMODEL_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        intent = _parse_task_intent(response.json())
    except (asyncio.TimeoutError, httpx.HTTPError, ValueError, TypeError) as error:
        logger.warning(
            "Jev 前置分类不可用（%s），将继续执行原始 Agent 流程",
            type(error).__name__,
        )
        return None
    if intent is not None:
        logger.info(
            "Jev 前置分类已接受：task_type=%s scope=%s report=%s chart=%s model=%s",
            intent.task_type,
            intent.scope,
            intent.wants_markdown_report,
            intent.wants_html_chart,
            intent.model,
        )
    return intent


async def classify_task_intent(
    current_message: str,
    previous_messages: Iterable[Any],
    *,
    client: httpx.AsyncClient | None = None,
) -> TaskIntent | None:
    """在新用户消息执行前分类；未启用、缺少密钥或异常时返回 ``None``。"""
    if not JEVMODEL_ENABLED or not JEVMODEL_API_KEY or not current_message.strip():
        return None
    state = build_task_intent_state(current_message, previous_messages)
    if client is not None:
        return await _request_task_intent(client, state)
    timeout = httpx.Timeout(JEVMODEL_TIMEOUT_SECONDS)
    async with httpx.AsyncClient(
        base_url=JEVMODEL_BASE_URL,
        timeout=timeout,
    ) as request_client:
        return await _request_task_intent(request_client, state)
