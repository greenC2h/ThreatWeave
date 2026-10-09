"""将 Charts MCP 的动态生成能力适配为 ThreatWeave 可调用工具。"""

from __future__ import annotations

import json
import os
import re
from typing import Any

import httpx
from langchain_core.tools import tool
from langchain_mcp_adapters.client import MultiServerMCPClient


_HTML_PREFIXES = ("<!doctype html", "<html", "<svg")
_HTML_URL_PATTERN = re.compile(r"https?://\S+\.html(?:[?#].*)?$", re.IGNORECASE)


class ChartsMcpUnavailableError(RuntimeError):
    """表示 Charts MCP 当前不能提供工具发现或生成服务。"""


def _chart_type_from_tool_name(tool_name: str) -> str | None:
    """将 Charts MCP 的 ``generate_*`` 工具名转换为稳定类型名称。"""
    if not tool_name.startswith("generate_"):
        return None
    chart_type = tool_name.removeprefix("generate_")
    if chart_type.endswith("_chart"):
        chart_type = chart_type.removesuffix("_chart")
    return chart_type or None


def _schema_from_tool(chart_tool: Any) -> dict[str, Any]:
    """读取 StructuredTool 的 JSON Schema，兼容 Pydantic v1/v2。"""
    schema_model = getattr(chart_tool, "args_schema", None)
    if schema_model is None:
        return {}
    if isinstance(schema_model, dict):
        return schema_model
    model_json_schema = getattr(schema_model, "model_json_schema", None)
    if callable(model_json_schema):
        return model_json_schema()
    schema = getattr(schema_model, "schema", None)
    return schema() if callable(schema) else {}


def _resolve_schema(schema: dict[str, Any], root_schema: dict[str, Any]) -> dict[str, Any]:
    """解析构建最小示例所需的本地 JSON Schema 引用。"""
    reference = schema.get("$ref")
    if reference and reference.startswith("#/"):
        resolved: Any = root_schema
        for part in reference[2:].split("/"):
            resolved = resolved.get(part, {}) if isinstance(resolved, dict) else {}
        return resolved if isinstance(resolved, dict) else {}
    for key in ("anyOf", "oneOf"):
        alternatives = schema.get(key)
        if isinstance(alternatives, list):
            for alternative in alternatives:
                if isinstance(alternative, dict) and alternative.get("type") != "null":
                    return _resolve_schema(alternative, root_schema)
    return schema


def _example_value(
    schema: dict[str, Any],
    root_schema: dict[str, Any],
    property_name: str = "",
) -> Any:
    """根据 MCP Schema 构建只包含必要字段的最小调用示例。"""
    schema = _resolve_schema(schema, root_schema)
    if "default" in schema:
        return schema["default"]
    if "const" in schema:
        return schema["const"]
    enum = schema.get("enum")
    if isinstance(enum, list) and enum:
        return enum[0]

    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        schema_type = next((item for item in schema_type if item != "null"), "string")
    if schema_type == "object" or "properties" in schema:
        properties = schema.get("properties", {})
        if not isinstance(properties, dict):
            return {}
        required = schema.get("required") or list(properties)[:1]
        return {
            name: _example_value(properties[name], root_schema, name)
            for name in required
            if name in properties and isinstance(properties[name], dict)
        }
    if schema_type == "array":
        items = schema.get("items")
        return [_example_value(items, root_schema, property_name)] if isinstance(items, dict) else []
    if schema_type in {"integer", "number"}:
        return 1
    if schema_type == "boolean":
        return False
    if schema_type == "string":
        return "html" if property_name.lower() == "format" else "示例"
    return None


def _compact_description(description: str) -> str:
    """将底层工具说明压缩为图表目录的单行摘要。"""
    normalized = " ".join(str(description or "").split())
    first_sentence = re.split(r"(?<=[.!?])\s+", normalized, maxsplit=1)[0]
    return first_sentence[:180] or "生成该类型的可视化图表"


def _build_chart_tool_map(chart_mcp_tools: list[Any]) -> dict[str, Any]:
    """建立 ``chart_type`` 到 Charts MCP 生成工具的映射。"""
    chart_tool_map: dict[str, Any] = {}
    for chart_tool in chart_mcp_tools:
        chart_type = _chart_type_from_tool_name(str(getattr(chart_tool, "name", "")))
        if chart_type is None:
            continue
        if chart_type in chart_tool_map:
            raise ValueError(f"Charts MCP 图表类型重复: {chart_type}")
        chart_tool_map[chart_type] = chart_tool
    if not chart_tool_map:
        raise ValueError("Charts MCP 未提供 generate_* 工具")
    return chart_tool_map


async def _discover_chart_tools() -> dict[str, Any]:
    """按需发现 Charts MCP 工具，避免服务启动依赖可选图表服务。"""
    chart_url = os.getenv("MODELSCOPE_CHARTS_MCP_URL", "").strip()
    if not chart_url:
        raise ChartsMcpUnavailableError("Charts MCP 未配置")
    try:
        client = MultiServerMCPClient({
            "charts-mcp": {"url": chart_url, "transport": "streamable_http"},
        })
        return _build_chart_tool_map(await client.get_tools(server_name="charts-mcp"))
    except ChartsMcpUnavailableError:
        raise
    except Exception as exc:
        raise ChartsMcpUnavailableError("Charts MCP 当前不可用") from exc


def _request_html_config(chart_config: dict[str, Any]) -> dict[str, Any]:
    """复制图表参数并请求 Charts MCP 返回 HTML。"""
    config = dict(chart_config)
    input_config = config.get("input")
    if isinstance(input_config, dict):
        config["input"] = {**input_config, "format": "html"}
    else:
        config["format"] = "html"
    return config


async def _download_html(url: str) -> str:
    """下载 Charts MCP 返回的临时 HTML，避免把远程 URL 交给前端。"""
    async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
        response = await client.get(url)
        response.raise_for_status()
    return response.text


def _strip_code_fence(value: str) -> str:
    """去除 MCP 文本结果可能附带的 Markdown HTML 代码围栏。"""
    match = re.fullmatch(r"```(?:html)?\s*(.*?)```", value.strip(), re.IGNORECASE | re.DOTALL)
    return match.group(1).strip() if match else value.strip()


def _find_html_payload(value: Any, depth: int = 0) -> tuple[str, str] | None:
    """从 MCP 结果递归提取 HTML 正文或 HTML URL。"""
    if depth > 6:
        return None
    if isinstance(value, tuple) and value:
        return _find_html_payload(value[0], depth + 1)
    if isinstance(value, list):
        for item in value:
            payload = _find_html_payload(item, depth + 1)
            if payload:
                return payload
        return None
    if isinstance(value, dict):
        for key in ("text", "content", "resource", "result", "resultObj", "html", "uri"):
            if key in value:
                payload = _find_html_payload(value[key], depth + 1)
                if payload:
                    return payload
        return None
    if not isinstance(value, str):
        return None

    text = _strip_code_fence(value)
    if text.startswith(("{", "[")):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = None
        if parsed is not None:
            return _find_html_payload(parsed, depth + 1)
    if text.lower().startswith(_HTML_PREFIXES):
        return "html", text
    if _HTML_URL_PATTERN.fullmatch(text):
        return "html-url", text
    return None


async def _html_from_result(result: Any) -> str:
    """将 Charts MCP 返回值统一为可写入交付件的 HTML 文本。"""
    payload = _find_html_payload(result)
    if payload is None:
        raise RuntimeError("Charts MCP 未返回 HTML 或 HTML URL")
    payload_type, payload_value = payload
    return await _download_html(payload_value) if payload_type == "html-url" else payload_value


def create_chart_tools() -> list[Any]:
    """创建按需发现 Charts MCP 能力的通用可视化工具。"""
    chart_tool_map: dict[str, Any] | None = None

    async def get_chart_tool_map() -> dict[str, Any]:
        nonlocal chart_tool_map
        if chart_tool_map is None:
            chart_tool_map = await _discover_chart_tools()
        return chart_tool_map

    @tool
    async def get_chart_spec(chart_type: str = "") -> str:
        """列出图表类型，或返回指定类型的真实 MCP Schema 和最小调用示例。"""
        try:
            available_tools = await get_chart_tool_map()
        except ChartsMcpUnavailableError as exc:
            return json.dumps({"status": "unavailable", "message": str(exc)}, ensure_ascii=False)

        if not chart_type:
            return json.dumps(
                {
                    "available_types": [
                        {
                            "chart_type": name,
                            "description": _compact_description(getattr(chart_tool, "description", "")),
                        }
                        for name, chart_tool in sorted(available_tools.items())
                    ],
                },
                ensure_ascii=False,
            )

        chart_tool = available_tools.get(chart_type)
        if chart_tool is None:
            return json.dumps(
                {"error": f"未知图表类型: {chart_type}", "available_types": sorted(available_tools)},
                ensure_ascii=False,
            )
        schema = _schema_from_tool(chart_tool)
        return json.dumps(
            {
                "chart_type": chart_type,
                "tool_name": chart_tool.name,
                "description": getattr(chart_tool, "description", ""),
                "input_schema": schema,
                "minimum_example": _example_value(schema, schema),
            },
            ensure_ascii=False,
        )

    @tool
    async def generate_visualization(chart_type: str, chart_config: dict[str, Any]) -> str:
        """按 Charts MCP 的真实 Schema 生成 HTML 可视化交付内容。"""
        try:
            available_tools = await get_chart_tool_map()
        except ChartsMcpUnavailableError as exc:
            return json.dumps({"status": "unavailable", "message": str(exc)}, ensure_ascii=False)

        chart_tool = available_tools.get(chart_type)
        if chart_tool is None:
            return json.dumps(
                {"error": f"未知图表类型: {chart_type}", "available_types": sorted(available_tools)},
                ensure_ascii=False,
            )
        try:
            html = await _html_from_result(
                await chart_tool.ainvoke(_request_html_config(chart_config)),
            )
        except Exception:
            return json.dumps(
                {"status": "unavailable", "message": "Charts MCP 未能生成可用 HTML 图表。"},
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "type": "deliverable_content",
                "mime_type": "text/html",
                "suggested_filename": f"threatweave-{chart_type}.html",
                "chart_type": chart_type,
                "content": html,
                "message": "Charts MCP 已生成 HTML 图表。请在用户明确要求图表时用 write_deliverable 保存。",
            },
            ensure_ascii=False,
        )

    return [get_chart_spec, generate_visualization]
