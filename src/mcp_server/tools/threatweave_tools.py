"""为模型暴露 ThreatWeave 受限只读查询工具。"""

from __future__ import annotations

from typing import Annotated, Any

from fastmcp import Context, FastMCP
from pydantic import Field

from mcp_server.http_base import request_threatweave_api


ReadSql = Annotated[str, Field(
    min_length=1,
    description="单条 SELECT 或非递归 WITH SELECT 查询；只能访问 describe_read_model 返回的 ThreatWeave 业务数据集。",
)]
ReadParameters = Annotated[
    list[Any] | None,
    Field(description="与 SQL 中 ? 占位符按顺序对应的参数；不得把用户输入拼接进 SQL 文本。"),
]


def _http_client(ctx: Context):
    """取得 MCP 生命周期内共享的 Java HTTP 客户端。"""
    return ctx.request_context.lifespan_context["http_client"]


def register_threatweave_tools(mcp: FastMCP) -> None:
    """注册仅包含业务读模型发现和受限 SQL 查询的 MCP 工具。"""

    @mcp.tool(name="describe_read_model")
    async def describe_read_model(ctx: Context | None = None) -> dict[str, Any]:
        """描述可查询的 ThreatWeave 业务数据集、主键、关联、字段语义和 SQL 示例。"""
        return await request_threatweave_api(
            _http_client(ctx), "GET", "/threatweave/read-model",
        )

    @mcp.tool(name="execute_read_query")
    async def execute_read_query(
        sql: ReadSql,
        parameters: ReadParameters = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """执行受限 ThreatWeave 只读 SQL 查询。

        Java 端拒绝非 SELECT、非业务数据集、锁定/递归语句和超出复杂度限制的查询，
        并强制最大返回行数、响应字节数和查询超时。
        """
        return await request_threatweave_api(
            _http_client(ctx), "POST", "/threatweave/read-query",
            json={"sql": sql, "parameters": parameters or []},
        )
