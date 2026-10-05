from fastmcp import FastMCP
from mcp_server.http_base import mcp_lifespan
from mcp_server.server_config import MCP_HOST, MCP_PORT, MCP_PATH


mcp = FastMCP(
    name="ThreatWeave-MCP-Server",
    instructions="只读查询 ThreatWeave 业务数据的受限 SQL 工具集",
    version="1.0.0",
    lifespan=mcp_lifespan,
)

from mcp_server.tools.threatweave_tools import register_threatweave_tools

register_threatweave_tools(mcp)


def main():

    # 启动 Streamable HTTP 服务
    mcp.run(
        transport="streamable-http",
        host=MCP_HOST,
        port=MCP_PORT,
        path=MCP_PATH
    )
    # 注意：run() 会阻塞，且 lifespan 会在服务器关闭时自动清理资源


if __name__ == "__main__":
    main()
